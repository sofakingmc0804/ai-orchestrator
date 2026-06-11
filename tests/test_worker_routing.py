from __future__ import annotations

import json

from orchestrator.intent.interpreter import parse_intent
from orchestrator.routing.worker_routing import route_with_workers


def _worker(
    worker_id: str,
    surface: str,
    contract_type: str,
    capabilities: list[str],
    tools: list[str] | None = None,
    best_jobs: list[str] | None = None,
    stats: dict[str, int] | None = None,
) -> dict[str, object]:
    stats = stats or {"coding": 8 if "coding" in capabilities else 3, "speed": 5, "stability": 7}
    return {
        "worker_id": worker_id,
        "model_id": worker_id.split("@", 1)[0],
        "base_model": worker_id.split("@", 1)[0],
        "surface": surface,
        "provider_id": surface,
        "contract_type": contract_type,
        "capabilities_json": json.dumps(capabilities),
        "tools_json": json.dumps(tools or []),
        "modalities_json": json.dumps(["text"]),
        "stats_json": json.dumps(stats),
        "best_jobs_json": json.dumps(best_jobs or []),
        "avoid_jobs_json": "[]",
    }


def test_worker_routing_uses_job_class_required_capabilities() -> None:
    intent = parse_intent("fix this repo bug")
    decision = route_with_workers(
        intent,
        [
            _worker("coder-only@ollama-local", "ollama-local", "local_resource", ["coding"]),
            _worker("coder-tools@ollama-local", "ollama-local", "local_resource", ["coding"], ["tools"]),
        ],
        "repo_coding",
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
    )

    assert decision.chosen_adapter == "ollama-http"
    assert decision.candidates_considered[0]["worker_id"] == "coder-tools@ollama-local"
    assert decision.candidates_rejected[0]["worker_id"] == "coder-only@ollama-local"
    assert "insufficient capability match" in decision.candidates_rejected[0]["rejected_reason"]


def test_worker_routing_maps_ollama_cloud_to_explicit_adapter() -> None:
    intent = parse_intent("fix this repo bug")
    decision = route_with_workers(
        intent,
        [
            _worker(
                "qwen3-coder:cloud@ollama-cloud",
                "ollama-cloud",
                "subscription_usage",
                ["coding", "reasoning"],
                ["tools"],
                ["repo_coding"],
            )
        ],
        "repo_coding",
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
    )

    assert decision.chosen_adapter == "ollama-cloud"
    assert decision.candidates_considered[0]["provider"] == "ollama-cloud"


def test_repo_coding_prefers_coder_fit_over_large_generic_model() -> None:
    intent = parse_intent("repair the orchestrator CLI bug")
    decision = route_with_workers(
        intent,
        [
            _worker(
                "gpt-oss:20b@ollama-local",
                "ollama-local",
                "local_resource",
                ["coding", "reasoning"],
                ["tools"],
                ["repo_coding"],
                stats={"coding": 8, "speed": 4, "stability": 7},
            ),
            _worker(
                "qwen2.5-coder:7b@ollama-local",
                "ollama-local",
                "local_resource",
                ["coding", "reasoning"],
                ["tools"],
                ["repo_coding"],
                stats={"coding": 8, "speed": 7, "stability": 7},
            ),
        ],
        "repo_coding",
        job_class_spec={"required_capabilities_json": '["coding", "tools"]', "preferred_stats_json": '{"coding": 3, "speed": 2}'},
    )

    assert decision.candidates_considered[0]["worker_id"] == "qwen2.5-coder:7b@ollama-local"
    assert decision.candidates_considered[0]["model_fit_score"] == 1.0
    assert decision.candidates_considered[1]["model_fit_score"] < 1.0


def test_token_usage_summary_demotes_expensive_failing_worker() -> None:
    intent = parse_intent("fix this repo bug")
    decision = route_with_workers(
        intent,
        [
            _worker(
                "qwen2.5-coder:7b@ollama-local",
                "ollama-local",
                "local_resource",
                ["coding"],
                ["tools"],
                ["repo_coding"],
                stats={"coding": 8, "speed": 7, "stability": 7},
            ),
            _worker(
                "deepseek-coder:7b@ollama-local",
                "ollama-local",
                "local_resource",
                ["coding"],
                ["tools"],
                ["repo_coding"],
                stats={"coding": 8, "speed": 7, "stability": 7},
            ),
        ],
        "repo_coding",
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
        token_usage_summary={
            "ollama-local|qwen2.5-coder:7b": {"avg_tokens_total": 20000, "success_rate": 0.0},
            "ollama-local|deepseek-coder:7b": {"avg_tokens_total": 700, "success_rate": 1.0},
        },
    )

    assert decision.candidates_considered[0]["worker_id"] == "deepseek-coder:7b@ollama-local"
    assert decision.candidates_considered[0]["token_efficiency_score"] > decision.candidates_considered[1]["token_efficiency_score"]


def test_budget_probe_lowers_subscription_worker_with_depleted_quota() -> None:
    intent = parse_intent("summarize this document")
    decision = route_with_workers(
        intent,
        [
            _worker(
                "local-mini@ollama-local",
                "ollama-local",
                "local_resource",
                ["reasoning"],
                best_jobs=["routing_triage"],
                stats={"speed": 8, "stability": 7},
            ),
            _worker(
                "cloud-mini@ollama-cloud",
                "ollama-cloud",
                "subscription_usage",
                ["reasoning"],
                best_jobs=["routing_triage"],
                stats={"speed": 8, "stability": 7},
            ),
        ],
        "routing_triage",
        budget_probes=[{"provider_id": "ollama-cloud", "remaining": 1, "limit": 1000, "ok": True, "probed_at": "now"}],
    )

    cloud = next(row for row in decision.candidates_considered if row["worker_id"] == "cloud-mini@ollama-cloud")
    local = next(row for row in decision.candidates_considered if row["worker_id"] == "local-mini@ollama-local")
    assert cloud["budget_score"] < local["budget_score"]
