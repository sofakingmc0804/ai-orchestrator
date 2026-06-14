from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from orchestrator.intent.interpreter import parse_intent
from orchestrator.routing.worker_routing import route_with_workers


def _worker(
    worker_id: str,
    surface: str,
    provider_id: str,
    contract_type: str = "subscription_quota",
) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "model_id": worker_id.split("@", 1)[0],
        "base_model": worker_id.split("@", 1)[0],
        "surface": surface,
        "provider_id": provider_id,
        "contract_type": contract_type,
        "capabilities_json": json.dumps(["coding"]),
        "tools_json": json.dumps(["tools"]),
        "modalities_json": json.dumps(["text"]),
        "stats_json": json.dumps({"coding": 9, "speed": 7, "stability": 8}),
        "best_jobs_json": json.dumps(["repo_coding"]),
        "avoid_jobs_json": "[]",
    }


def _snapshot(service_id: str, remaining: int, limit: int, *, ok: bool = True, checked_at: str | None = None) -> dict[str, object]:
    return {
        "id": f"{service_id}:default",
        "service_id": service_id,
        "tokens_remaining": remaining,
        "tokens_limit": limit,
        "ok": ok,
        "checked_at": checked_at or datetime.now(UTC).isoformat(),
        "status": "ok" if ok else "failed",
    }


def test_live_subscription_snapshot_protects_reserve_before_budget_probe() -> None:
    intent = parse_intent("repair this repo bug")

    decision = route_with_workers(
        intent,
        [_worker("gpt-5.3-codex@codex-cli", "codex-cli", "codex")],
        "repo_coding",
        subscription_usage_snapshots=[_snapshot("openai_chatgpt", remaining=20, limit=100)],
        budget_probes=[{"provider_id": "codex", "remaining": 90, "limit": 100, "ok": True, "probed_at": "placeholder"}],
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
    )

    assert decision.chosen_adapter is None
    assert decision.candidates_rejected[0]["rejected_reason"].startswith("quota reserve protected")
    assert "codex" in decision.candidates_rejected[0]["rejected_reason"]


def test_healthy_live_subscription_snapshot_allows_route() -> None:
    intent = parse_intent("repair this repo bug")

    decision = route_with_workers(
        intent,
        [_worker("gpt-5.3-codex@codex-cli", "codex-cli", "codex")],
        "repo_coding",
        subscription_usage_snapshots=[_snapshot("openai_chatgpt", remaining=81, limit=100)],
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
    )

    assert decision.chosen_adapter == "codex-cli"
    assert decision.candidates_considered[0]["budget_score"] == 0.81
    assert decision.candidates_considered[0]["budget_source"] == "subscription_usage_snapshots"


def test_budget_probe_is_only_fallback_for_stale_subscription_snapshot() -> None:
    intent = parse_intent("repair this repo bug")
    stale_checked_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()

    decision = route_with_workers(
        intent,
        [_worker("gpt-5.3-codex@codex-cli", "codex-cli", "codex")],
        "repo_coding",
        subscription_usage_snapshots=[_snapshot("openai_chatgpt", remaining=20, limit=100, checked_at=stale_checked_at)],
        budget_probes=[{"provider_id": "codex", "remaining": 90, "limit": 100, "ok": True, "probed_at": "fallback"}],
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
    )

    assert decision.chosen_adapter == "codex-cli"
    assert decision.candidates_considered[0]["budget_source"] == "budget_probes:fallback"
