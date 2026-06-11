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
) -> dict[str, object]:
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
        "stats_json": json.dumps({"coding": 8 if "coding" in capabilities else 3}),
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
