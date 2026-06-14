from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.models import ConsequenceTier, Intent
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.routing.worker_routing import route_with_workers
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )


def _worker(worker_id: str) -> dict[str, object]:
    model = worker_id.split("@", 1)[0]
    return {
        "worker_id": worker_id,
        "model_id": model,
        "base_model": model,
        "surface": "ollama-local",
        "provider_id": "ollama-local",
        "contract_type": "local_resource",
        "capabilities_json": json.dumps(["coding"]),
        "tools_json": json.dumps(["tools"]),
        "modalities_json": json.dumps(["text"]),
        "stats_json": json.dumps({"coding": 7, "speed": 7, "stability": 7}),
        "best_jobs_json": json.dumps(["repo_coding"]),
        "avoid_jobs_json": "[]",
    }


def _operation_task() -> dict[str, Any]:
    return {
        "task_id": "TEST-QUALITY-001",
        "domain_id": "repo_coding",
        "validator": "required_fields_and_terms",
        "expected": {
            "must_contain": {
                "next_action": ["fix"],
                "completion_test": ["pytest"],
            }
        },
    }


@pytest.mark.asyncio
async def test_operation_quality_scores_are_persisted(tmp_path: Path) -> None:
    store = StateStore(_settings(tmp_path))
    await store.initialize()

    await store.record_operation_quality_score(
        {
            "dispatch_id": "dsp_quality",
            "worker_id": "accurate@ollama-local",
            "operation_domain": "repo_coding",
            "validator_name": "required_fields_and_terms",
            "composite_score": 0.875,
            "dimensional_scores": {"structural": 1.0, "content": 0.75, "functional": 1.0, "composite": 0.875},
            "task_id": "TEST-QUALITY-001",
            "proof_kind": "live",
            "validation": {"checks": [{"check": "completion_test", "passed": True}]},
        }
    )

    rows = await store.list_operation_quality_scores()
    live = await store.load_live_operation_quality_scores()

    assert rows[0]["dispatch_id"] == "dsp_quality"
    assert rows[0]["proof_kind"] == "live"
    assert json.loads(rows[0]["dimensional_scores_json"])["composite"] == 0.875
    assert live["accurate@ollama-local"]["repo_coding"]["composite_score"] == 0.875


@pytest.mark.asyncio
async def test_dispatch_scores_explicit_operation_task(tmp_path: Path) -> None:
    class AccurateAdapter:
        async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
            return {
                "ok": True,
                "model": "accurate",
                "text": json.dumps({"next_action": "fix the bug", "completion_test": "pytest passes"}),
                "raw": {"provider": "fake", "proof": "live"},
            }

    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.db.execute(
        "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
        "repo_coding",
        '["coding", "tools"]',
        "{}",
        0,
        "local_resource",
    )
    await store.db.execute(
        """
        INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
          capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        "accurate@ollama-local",
        "accurate",
        "accurate",
        "ollama-local",
        "ollama-local",
        "local_resource",
        '["coding"]',
        '["tools"]',
        '["text"]',
        '{"coding": 7, "speed": 7, "stability": 7}',
        '["repo_coding"]',
        "[]",
    )
    intent = Intent(
        id="int_quality",
        source="test",
        raw_text="fix this repo bug",
        parsed_payload={"operation_task": _operation_task()},
        consequence_tier=ConsequenceTier.LOW,
    )
    await store.create_intent(intent)
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.adapters = {"ollama-http": AccurateAdapter()}  # type: ignore[assignment]

    result = await dispatcher._execute_intent(intent, job_class_override="repo_coding")

    rows = await store.list_operation_quality_scores()
    assert result.state == "completed"
    assert result.receipt["operation_quality_score"]["composite_score"] == 1.0
    assert rows[0]["dispatch_id"] == result.dispatch_id
    assert rows[0]["worker_id"] == "accurate@ollama-local"
    assert rows[0]["proof_kind"] == "live"


def test_route_with_workers_prefers_measured_operation_quality() -> None:
    intent = Intent(id="int_route_quality", source="test", raw_text="fix repo bug", parsed_payload={})

    decision = route_with_workers(
        intent,
        [_worker("weak@ollama-local"), _worker("accurate@ollama-local")],
        "repo_coding",
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
        operation_quality_scores={
            "weak@ollama-local": {"repo_coding": {"composite_score": 0.2, "sample_count": 1}},
            "accurate@ollama-local": {"repo_coding": {"composite_score": 0.95, "sample_count": 1}},
        },
    )

    assert decision.candidates_considered[0]["worker_id"] == "accurate@ollama-local"
    assert decision.candidates_considered[0]["measured_quality_score"] == 0.95
    assert decision.candidates_considered[0]["quality_source"] == "operation_quality_scores:repo_coding"
