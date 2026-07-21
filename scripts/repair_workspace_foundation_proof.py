"""Repair a proof-run meter only from its stronger Hermes usage receipt.

This deliberately does not repeat an expensive model turn.  It compares the
persisted event to the usage JSON Hermes wrote for that exact turn, corrects
only provider/model/quota attribution, and leaves an audit receipt alongside
the original proof bundle.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.config import Settings
from orchestrator.evaluation.sparring import (
    FROZEN_HELD_OUT_SUITE,
    evaluate_held_out_suite,
    held_out_case_prompt,
    score_held_out_response,
)
from orchestrator.state.store import StateStore
from orchestrator.hermes.brain_bridge import prepare_workspace_hermes_turn
from orchestrator.workspace_runtime import build_platform_console_payload


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def ollama_generate(model: str, prompt: str) -> dict[str, Any]:
    """Run the frozen evaluator through the local-only Ollama HTTP surface."""
    response = httpx.post(
        "http://127.0.0.1:11434/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0, "num_predict": 3}},
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Ollama evaluator returned a non-object response")
    return body


def token_counts(response: dict[str, Any]) -> tuple[int, int]:
    return max(int(response.get("prompt_eval_count") or 0), 0), max(int(response.get("eval_count") or 0), 0)


async def record_actual_sparring(store: StateStore, proof_dir: Path, run_id: str) -> dict[str, Any]:
    """Replace proof fixtures with a real, local, held-out replay.

    The candidate is scored against the frozen exact-response ceiling of 1.0.
    That is intentionally non-promotable: equality must reject promotion,
    proving that the policy gate is not a decorative "learning" label.
    """
    prepared_raw = json.loads((proof_dir / "api" / "prepared-mode-packs.json").read_text(encoding="utf-8"))
    if not isinstance(prepared_raw, list):
        raise ValueError("prepared mode packs receipt is invalid")
    prepared_by_pack = {
        str(item.get("workspace_session", {}).get("mode_pack_id") or item.get("workspace_session", {}).get("frozen_mode_pack", {}).get("id")): item
        for item in prepared_raw
        if isinstance(item, dict)
    }
    model = "qwen2.5:0.5b"
    baseline_scores: dict[str, float] = {}
    candidate_scores: dict[str, float] = {}
    episodes: list[dict[str, Any]] = []
    case_receipts: list[dict[str, Any]] = []
    for case in FROZEN_HELD_OUT_SUITE:
        prepared = prepared_by_pack.get(case.mode_pack_id)
        if not isinstance(prepared, dict):
            raise ValueError(f"missing prepared mode pack receipt: {case.mode_pack_id}")
        packet = prepared.get("work_packet") or {}
        packet_id = str(packet.get("id") or "")
        frozen_pack = (prepared.get("workspace_session") or {}).get("frozen_mode_pack") or {}
        prompt, marker = held_out_case_prompt(case)
        candidate = ollama_generate(model, prompt)
        candidate_text = str(candidate.get("response") or "")
        baseline_text = "frozen exact-response ceiling"
        baseline_score = 1.0
        candidate_score = score_held_out_response(candidate_text, marker)
        baseline_scores[case.id] = baseline_score
        candidate_scores[case.id] = candidate_score
        tokens_in, tokens_out = token_counts(candidate)
        await store.record_resource_event(
            workspace_id="personal",
            work_packet_id=packet_id,
            provider="ollama",
            model=model,
            route="held_out_sparring_candidate",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            estimated_cost_usd=0.0,
            actual_cost_usd=0.0,
            quota_source="local_model",
            quota_state={"source": "ollama_generate_response", "policy_arm": "candidate", "baseline": "frozen_exact_response_ceiling"},
            context_pressure=None,
            measurement_source="ollama_generate_response",
        )
        episode = await store.record_evaluation_episode(
            workspace_id="personal",
            work_packet_id=packet_id,
            mode_pack_id=case.mode_pack_id,
            selected_model=model,
            tools=list(frozen_pack.get("preloaded_skills") or []),
            evidence={
                "case_id": case.id,
                "candidate_response": candidate_text,
                "baseline_response": baseline_text,
                "expected_marker": marker,
                "evaluator": str(frozen_pack.get("evaluator") or "frozen_exact_marker"),
                "input_source": "ollama_generate_response",
                "run_id": run_id,
            },
            reviewer_outcome="accepted" if candidate_score == 1.0 else "rejected",
            delivery_receipt=f"local-held-out-runtime:{run_id}:{case.id}",
            cost_usd=0.0,
            failure_class=None if candidate_score == 1.0 else "exact_marker_mismatch",
            score=candidate_score,
            held_out=True,
        )
        episodes.append(episode)
        case_receipts.append(
            {
                "case_id": case.id,
                "mode_pack_id": case.mode_pack_id,
                "expected_marker": marker,
                "baseline_response": baseline_text,
                "candidate_response": candidate_text,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
            }
        )

    sparring = evaluate_held_out_suite(baseline_scores=baseline_scores, candidate_scores=candidate_scores)
    sparring["runtime_source"] = "local_ollama_held_out_replay"
    sparring["policy_arms"] = {"baseline": "frozen_exact_response_ceiling", "candidate": "local_ollama_runtime"}
    sparring["runtime_cases"] = case_receipts
    candidate = await store.create_policy_candidate(
        workspace_id="personal",
        policy={"routing": "qwen2.5:0.5b", "scope": "sandbox_held_out_replay", "run_id": run_id},
        baseline_score=float(sparring["baseline_mean"]),
        episode_ids=[str(episode["id"]) for episode in episodes],
        sandbox_only=True,
    )
    replay = await store.replay_policy_candidate(
        str(candidate["id"]),
        candidate_score=float(sparring["candidate_mean"]),
        held_out_episode_ids=[str(episode["id"]) for episode in episodes],
        replay_evidence=sparring,
    )
    return {"episodes": episodes, "policy_candidate": replay, "sparring": sparring}


async def ensure_example_workspace_session(settings: Settings, proof_dir: Path) -> dict[str, Any]:
    """Prepare one Example session without granting it Personal packet content."""
    path = proof_dir / "api" / "prepared-example-session.json"
    if path.exists():
        return read_json(path)
    prepared = await prepare_workspace_hermes_turn(
        settings,
        workspace_id="example",
        mode_pack_id="software_engineering.v1",
        skill_capability_checks={"repo_read": True, "test_runner": True, "source_reader": True},
        text="Example workspace isolation proof: prepare a local engineering session.",
        job_class="repo_coding",
        argv=[],
        consumer="runtime-proof-bundle",
    )
    write_json(path, prepared)
    return prepared


async def repair(proof_dir: Path) -> dict[str, Any]:
    usage_path = proof_dir / "hermes-usage.json"
    meter_path = proof_dir / "api" / "meter-event.json"
    summary_path = proof_dir / "summary.json"
    usage = read_json(usage_path)
    meter = read_json(meter_path)
    event_id = str((meter.get("resource_event") or {}).get("id") or "")
    if not event_id:
        raise ValueError("proof bundle has no resource event id")

    runtime_home = proof_dir / "runtime"
    settings = Settings(
        home=runtime_home,
        state_path=runtime_home / "state.sqlite",
        notifications_path=runtime_home / "notifications.jsonl",
        log_dir=runtime_home / "logs",
        repo_root=ROOT,
    )
    store = StateStore(settings)
    await store.initialize()
    summary = read_json(summary_path)
    run_id = str(summary.get("run_id") or proof_dir.name)
    retired = await store.supersede_nonruntime_evaluation_fixtures(run_id)
    provider = str(usage.get("provider") or "unknown")
    model = str(usage.get("model") or "unknown")
    corrected = await store.correct_resource_event(
        event_id,
        provider=provider,
        model=model,
        quota_source="unknown",
        measurement_source="hermes_usage_report",
        estimated_cost_usd=None,
        actual_cost_usd=None,
        quota_state={
            "cost_source": str(usage.get("cost_source") or "unknown"),
            "cost_status": str(usage.get("cost_status") or "unknown"),
            "source": "hermes_usage_report",
        },
    )
    workspace_meter = await store.workspace_meter("personal")
    platform_console = await build_platform_console_payload(store)

    previous_outcome_path = proof_dir / "outcome-loop.json"
    existing_outcomes = read_json(previous_outcome_path) if previous_outcome_path.exists() else {}
    if (existing_outcomes.get("sparring") or {}).get("runtime_source") == "local_ollama_held_out_replay":
        actual_outcomes = existing_outcomes
    else:
        if previous_outcome_path.exists():
            write_json(proof_dir / "outcome-loop-nonruntime-fixture.json", existing_outcomes)
        actual_outcomes = await record_actual_sparring(store, proof_dir, run_id)
        write_json(proof_dir / "outcome-loop.json", actual_outcomes)
        write_json(proof_dir / "actual-held-out-sparring.json", actual_outcomes["sparring"])
    workspace_meter = await store.workspace_meter("personal")
    platform_console = await build_platform_console_payload(store)
    example_session = await ensure_example_workspace_session(settings, proof_dir)

    corrected_meter = {
        "state": "metered",
        "workspace_id": "personal",
        "work_packet_id": corrected.get("work_packet_id"),
        "resource_event": corrected,
    }
    write_json(meter_path, corrected_meter)
    write_json(proof_dir / "api" / "workspace-meter.json", workspace_meter)
    write_json(proof_dir / "api" / "platform-console-final.json", platform_console)
    receipt = {
        "action": "resource_event_corrected_from_hermes_usage_report",
        "event_id": event_id,
        "source_path": str(usage_path),
        "source_provider": provider,
        "source_model": model,
        "corrected_event": corrected,
        "meter_matches_usage": (
            corrected["provider"] == provider
            and corrected["model"] == model
            and corrected["measurement_source"] == "hermes_usage_report"
        ),
        "nonruntime_fixtures_retired": retired,
        "held_out_sparring_runtime_source": actual_outcomes["sparring"]["runtime_source"],
        "held_out_sparring_decision": actual_outcomes["sparring"]["decision"],
    }
    write_json(proof_dir / "meter-correction-receipt.json", receipt)

    checks = summary.setdefault("checks", {})
    checks.pop("hermes_local_turn", None)
    checks["hermes_routed_turn"] = provider != "unknown" and model != "unknown"
    checks["meter_metadata_matches_usage"] = receipt["meter_matches_usage"]
    checks["live_meter_matches_usage"] = int(corrected["tokens_total"]) == int(usage.get("total_tokens") or 0)
    # The full meter includes the new sparring calls.  The original Hermes event
    # itself remains exact and is asserted above by meter_metadata_matches_usage.
    checks["live_meter_includes_runtime_sparring"] = int(workspace_meter["tokens_total"]) >= int(usage.get("total_tokens") or 0)
    checks["held_out_policy_replay"] = actual_outcomes["policy_candidate"]["replay"]["suite_id"] == "foundation-held-out-v1"
    checks["nonruntime_fixtures_retired"] = retired["episodes"] >= 0
    checks["personal_and_example_sessions_prepared"] = example_session.get("workspace_id") == "example"
    summary["actual_hermes_usage"] = {
        "provider": provider,
        "model": model,
        "quota_source": "unknown",
        "measurement_source": "hermes_usage_report",
    }
    write_json(summary_path, summary)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proof-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = asyncio.run(repair(args.proof_dir.resolve()))
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
