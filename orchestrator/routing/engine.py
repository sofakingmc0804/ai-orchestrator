from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.models import BillingClass, ConsequenceTier, Intent, RoutingDecision


SCORE_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "data" / "contracts" / "local_model_operation_scores_2026-06-07.json"


BILLING_ORDER = {
    BillingClass.LOCAL_RESOURCE.value: 0,
    BillingClass.SUBSCRIPTION_UNLIMITED.value: 1,
    BillingClass.SUBSCRIPTION_QUOTA.value: 2,
    BillingClass.SUBSCRIPTION_USAGE.value: 3,
    BillingClass.UNKNOWN_COST.value: 99,
    BillingClass.METERED_EXTRA_COST.value: 100,
}

FORBIDDEN = {BillingClass.METERED_EXTRA_COST.value, BillingClass.UNKNOWN_COST.value}
TIER_ORDER = {ConsequenceTier.LOW.value: 0, ConsequenceTier.MEDIUM.value: 1, ConsequenceTier.HIGH.value: 2, ConsequenceTier.CRITICAL.value: 3}
RESERVE_BY_TIER = {
    ConsequenceTier.LOW.value: 0.20,
    ConsequenceTier.MEDIUM.value: 0.20,
    ConsequenceTier.HIGH.value: 0.05,
    ConsequenceTier.CRITICAL.value: 0.0,
}
PROVIDER_BY_ADAPTER = {
    "copilot-gh": "github_copilot",
    "copilot-vscode": "github_copilot",
    "claude-desktop-mcp": "claude",
    "claude-code-cli": "claude",
    "gemini-cli": "gemini",
}


def _quota_rejection_reason(intent: Intent, row: dict[str, Any], quota_state: dict[str, dict[str, Any]]) -> str | None:
    if row.get("billing_class") != BillingClass.SUBSCRIPTION_QUOTA.value:
        return None
    provider = PROVIDER_BY_ADAPTER.get(str(row.get("adapter_name") or ""))
    if not provider:
        return None
    quota = quota_state.get(provider)
    if not quota:
        return None
    limit = int(quota.get("units_limit") or 0)
    remaining = int(quota.get("remaining") or 0)
    if limit <= 0:
        return None
    priority = str(intent.parsed_payload.get("priority") or "").lower()
    reserve = 0.0 if priority == "urgent" else RESERVE_BY_TIER.get(intent.consequence_tier.value, 0.20)
    reserve_units = limit * reserve
    if remaining <= reserve_units:
        return f"quota reserve protected for {provider}: remaining {remaining}/{limit} at reserve {reserve:.0%}"
    return None


def _project_policy_rejection(row: dict[str, Any], project_policy: dict[str, Any]) -> str | None:
    adapter = str(row.get("adapter_name") or "")
    allowed = {str(item) for item in project_policy.get("allowed_adapters") or []}
    forbidden = {str(item) for item in project_policy.get("forbidden_adapters") or []}
    if adapter in forbidden:
        return "adapter forbidden by project policy"
    if allowed and adapter not in allowed:
        return "adapter not in project allowed_adapters"
    return None


def _preference_rank(row: dict[str, Any], project_policy: dict[str, Any]) -> int:
    preferred = [str(item) for item in project_policy.get("preferred_adapters") or []]
    adapter = str(row.get("adapter_name") or "")
    return preferred.index(adapter) if adapter in preferred else 999


def load_score_contract(path: Path | None = None) -> dict[str, Any] | None:
    """Load the benchmark score contract JSON, or None if unavailable."""
    target = path or SCORE_CONTRACT_PATH
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema_version", "").startswith("operation-score-contract/"):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


# Map adapter providers to the set of benchmark surfaces they correspond to.
# When a benchmark surface matches, that adapter's models are in the benchmark
# population and the score contract applies.
_PROVIDER_TO_BENCHMARK_SURFACE: dict[str, str] = {
    "ollama": "ollama_http",
    "lm_studio": "lm_studio",
}


def _overlay_benchmark_scores(
    candidates: list[dict[str, Any]],
    score_contract: dict[str, Any],
    domain_id: str,
) -> list[dict[str, Any]]:
    """Overlay benchmark-derived scores onto capability candidates.

    For each candidate whose provider has benchmark data for the requested
    domain, sets ``benchmark_score`` (population-relative composite 0.0-1.0)
    and ``recommended_model`` (the top model name for dispatch).

    Candidates from providers without benchmark data keep benchmark_score=0.0.
    """
    domain_leaders = score_contract.get("domain_leaders", {}).get(domain_id, [])
    routing_rules = score_contract.get("routing_rules", [])
    rule = next((r for r in routing_rules if r.get("domain_id") == domain_id), None)

    if not rule or not domain_leaders:
        return candidates

    tested_surfaces = set(score_contract.get("tested_surfaces") or ["ollama_http"])

    preferred = rule.get("preferred_local_models", [])
    top_leader = domain_leaders[0] if domain_leaders else {}
    top_model = str((preferred or [top_leader.get("model") or ""])[0] or "")
    best_composite = float(
        rule.get("best_composite")
        or top_leader.get("mean_composite")
        or top_leader.get("pass_rate")
        or 0.0
    )

    for candidate in candidates:
        provider = str(candidate.get("provider") or "")
        surface = _PROVIDER_TO_BENCHMARK_SURFACE.get(provider)
        if not surface or surface not in tested_surfaces:
            continue

        if top_model and best_composite > 0.0:
            candidate["benchmark_score"] = best_composite
            candidate["recommended_model"] = top_model
        else:
            candidate["benchmark_score"] = 0.0
            candidate["recommended_model"] = None

    return candidates


def route_intent(intent: Intent, capabilities: list[dict[str, Any]], quota_state: dict[str, dict[str, Any]] | None = None, project_policy: dict[str, Any] | None = None, score_contract: dict[str, Any] | None = None) -> RoutingDecision:
    quota_state = quota_state or {}
    project_policy = project_policy or {}
    required = str(intent.parsed_payload.get("required_capability") or "classify_text")
    domain_id = str(intent.parsed_payload.get("domain_id") or "")
    considered: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    matches = [c for c in capabilities if c.get("capability_id") == required and c.get("enabled", 1)]
    for cap in matches:
        row = dict(cap)
        reason = None
        if row.get("billing_class") in FORBIDDEN:
            reason = "forbidden billing class"
        elif TIER_ORDER.get(intent.consequence_tier.value, 99) > TIER_ORDER.get(str(row.get("consequence_max")), -1):
            reason = "intent consequence exceeds adapter maximum"
        else:
            reason = _project_policy_rejection(row, project_policy) or _quota_rejection_reason(intent, row, quota_state)
        if reason:
            row["rejected_reason"] = reason
            rejected.append(row)
        else:
            considered.append(row)

    if score_contract is None and domain_id:
        score_contract = load_score_contract()
    if score_contract and domain_id:
        considered = _overlay_benchmark_scores(considered, score_contract, domain_id)

    considered.sort(
        key=lambda c: (
            BILLING_ORDER.get(str(c.get("billing_class")), 99),
            _preference_rank(c, project_policy),
            -float(c.get("benchmark_score") or 0),
            str(c.get("latency_band")),
            -int(c.get("rating_quality") or 0),
        )
    )
    chosen = considered[0]["adapter_name"] if considered else None
    recommended_model = considered[0].get("recommended_model") if considered else None
    if chosen:
        model_note = f" (model: {recommended_model})" if recommended_model else ""
        reasoning = f"Selected {chosen}{model_note} for {required}: cheapest allowed capability match ranked by population-relative benchmark score."
    else:
        reasoning = f"No allowed adapter satisfied {required}; create repair queue entry."
    return RoutingDecision(
        intent_id=intent.id,
        chosen_adapter=chosen,
        candidates_considered=considered,
        candidates_rejected=rejected,
        reasoning=reasoning,
    )
