from __future__ import annotations

import hashlib
import random
from statistics import median
from typing import Any


FLAT_RATE_CONTRACTS = {
    "local_resource",
    "subscription_unlimited",
    "subscription_quota",
    "subscription_usage",
}


def _text(value: Any) -> str:
    return str(value or "")


def _anon_id(seed: str, index: int, output: str) -> str:
    digest = hashlib.sha256(f"{seed}:{index}:{output}".encode("utf-8")).hexdigest()[:12]
    return f"anon_{digest}"


def _eligible_judges(candidate: dict[str, Any], judges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    provider_id = _text(candidate.get("provider_id"))
    model_family = _text(candidate.get("model_family"))
    eligible: list[dict[str, Any]] = []
    for judge in judges:
        contract = _text(judge.get("contract_type"))
        if contract not in FLAT_RATE_CONTRACTS:
            continue
        if _text(judge.get("provider_id")) == provider_id:
            continue
        if _text(judge.get("model_family")) == model_family:
            continue
        eligible.append(dict(judge))
    return eligible


def build_blind_judge_panel(
    candidates: list[dict[str, Any]],
    judges: list[dict[str, Any]],
    *,
    seed: str,
    min_judges: int = 3,
) -> dict[str, Any]:
    rng = random.Random(seed)
    blind_outputs: list[dict[str, str]] = []
    assignments: dict[str, list[str]] = {}
    audit_assignments: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates):
        output = _text(candidate.get("output"))
        anonymous_id = _anon_id(seed, index, output)
        eligible = _eligible_judges(candidate, judges)
        eligible_provider_count = len({_text(judge.get("provider_id")) for judge in eligible})
        if len(eligible) < min_judges or eligible_provider_count < min_judges:
            raise ValueError(f"candidate {candidate.get('worker_id')} needs at least 3 eligible judges")
        eligible = sorted(eligible, key=lambda judge: _text(judge.get("judge_id")))
        rng.shuffle(eligible)
        selected = eligible[: max(min_judges, len(eligible))]
        judge_ids = [_text(judge.get("judge_id")) for judge in selected]
        assignments[anonymous_id] = judge_ids
        blind_outputs.append({"anonymous_id": anonymous_id, "output": output})
        audit_assignments.append(
            {
                "anonymous_id": anonymous_id,
                "candidate_worker_id": _text(candidate.get("worker_id")),
                "candidate_provider_id": _text(candidate.get("provider_id")),
                "candidate_model_family": _text(candidate.get("model_family")),
                "judge_ids": judge_ids,
                "judge_provider_ids": [_text(judge.get("provider_id")) for judge in selected],
                "judge_model_families": [_text(judge.get("model_family")) for judge in selected],
                "bias_controls": [
                    "blind_anonymous_candidate_ids",
                    "self_provider_excluded",
                    "self_model_family_excluded",
                    "metered_judges_excluded",
                    "three_diverse_judges_minimum",
                ],
            }
        )

    rng.shuffle(blind_outputs)
    return {
        "blind_outputs": blind_outputs,
        "assignments": assignments,
        "audit_assignments": audit_assignments,
        "min_judges": min_judges,
        "seed": seed,
    }


def _variance(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _relative_scores(results: list[dict[str, Any]]) -> None:
    accepted = [item for item in results if item["status"] == "accepted"]
    if not accepted:
        return
    scores = [float(item["consensus_score"]) for item in accepted]
    low = min(scores)
    high = max(scores)
    if high == low:
        for item in accepted:
            item["relative_score"] = 1.0
        return
    for item in accepted:
        item["relative_score"] = round((float(item["consensus_score"]) - low) / (high - low), 4)


def score_blind_consensus(
    panel: dict[str, Any],
    judge_scores: dict[str, list[dict[str, Any]]],
    *,
    variance_threshold: float = 0.05,
) -> dict[str, Any]:
    assignments = panel.get("assignments", {})
    audit_by_anon = {item["anonymous_id"]: item for item in panel.get("audit_assignments", [])}
    min_judges = int(panel.get("min_judges") or 3)
    results: list[dict[str, Any]] = []

    for anonymous_id, allowed_judges in assignments.items():
        allowed = set(allowed_judges)
        raw_scores = judge_scores.get(anonymous_id, [])
        unauthorized = [score.get("judge_id") for score in raw_scores if score.get("judge_id") not in allowed]
        if unauthorized:
            raise ValueError(f"unauthorized judge score for {anonymous_id}: {unauthorized}")
        values = [max(0.0, min(1.0, float(score.get("score") or 0.0))) for score in raw_scores]
        consensus = median(values) if values else 0.0
        variance = _variance(values)
        status = "accepted" if len(values) >= min_judges and variance <= variance_threshold else "needs_rejudge"
        agreement = 0.0
        if variance_threshold > 0:
            agreement = max(0.0, min(1.0, 1.0 - (variance / variance_threshold)))
        audit = audit_by_anon[anonymous_id]
        results.append(
            {
                "anonymous_id": anonymous_id,
                "worker_id": audit["candidate_worker_id"],
                "consensus_score": round(float(consensus), 4),
                "variance": round(variance, 4),
                "inter_judge_agreement": round(agreement, 4),
                "judge_count": len(values),
                "status": status,
                "relative_score": None,
            }
        )

    _relative_scores(results)
    ranking = sorted(
        [item for item in results if item["status"] == "accepted"],
        key=lambda item: (-float(item["relative_score"] or 0.0), item["worker_id"]),
    )
    return {
        "results": results,
        "ranking": ranking,
        "by_worker": {item["worker_id"]: item for item in results},
        "variance_threshold": variance_threshold,
    }
