from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.intent.interpreter import parse_intent
from orchestrator.models import BillingClass, HealthState, ServiceInfo
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


def _worker(
    worker_id: str,
    surface: str,
    contract_type: str,
    stats: dict[str, int],
) -> dict[str, Any]:
    return {
        "worker_id": worker_id,
        "model_id": worker_id.split("@", 1)[0],
        "base_model": worker_id.split("@", 1)[0],
        "surface": surface,
        "provider_id": surface,
        "contract_type": contract_type,
        "capabilities_json": json.dumps(["coding"]),
        "tools_json": json.dumps(["tools"]),
        "modalities_json": json.dumps(["text"]),
        "stats_json": json.dumps(stats),
        "best_jobs_json": json.dumps(["repo_coding"]),
        "avoid_jobs_json": "[]",
        "marginal_cost_json": "{}",
    }


async def _seed_workers(store: StateStore) -> None:
    await store.db.execute(
        "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
        "repo_coding",
        '["coding", "tools"]',
        "{}",
        0,
        "local_resource",
    )
    for worker in [
        _worker(
            "gpt-5.3-codex@github-copilot",
            "github-copilot",
            BillingClass.SUBSCRIPTION_QUOTA.value,
            {"coding": 10, "speed": 8, "stability": 8},
        ),
        _worker(
            "deepseek-v4-pro@ollama-cloud",
            "ollama-cloud",
            BillingClass.SUBSCRIPTION_USAGE.value,
            {"coding": 8, "speed": 7, "stability": 8},
        ),
    ]:
        await store.db.execute(
            """
            INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
              capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json, marginal_cost_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            worker["worker_id"],
            worker["model_id"],
            worker["base_model"],
            worker["surface"],
            worker["provider_id"],
            worker["contract_type"],
            worker["capabilities_json"],
            worker["tools_json"],
            worker["modalities_json"],
            worker["stats_json"],
            worker["best_jobs_json"],
            worker["avoid_jobs_json"],
            worker["marginal_cost_json"],
        )


def test_failover_ladder_orders_by_health_quota_and_flat_rate_floor() -> None:
    intent = parse_intent("fix this repo bug")
    decision = route_with_workers(
        intent,
        [
            _worker(
                "gpt-5.3-codex@github-copilot",
                "github-copilot",
                BillingClass.SUBSCRIPTION_QUOTA.value,
                {"coding": 10, "speed": 8, "stability": 8},
            ),
            _worker(
                "deepseek-v4-pro@ollama-cloud",
                "ollama-cloud",
                BillingClass.SUBSCRIPTION_USAGE.value,
                {"coding": 8, "speed": 7, "stability": 8},
            ),
        ],
        "repo_coding",
        job_class_spec={"required_capabilities_json": '["coding", "tools"]'},
        service_health={"copilot-gh": "stopped", "ollama-cloud": "healthy"},
        budget_probes=[
            {"provider_id": "github_copilot", "remaining": 5, "limit": 100, "ok": True, "probed_at": "now"},
            {"provider_id": "ollama-cloud", "remaining": 1000, "limit": 1000, "ok": True, "probed_at": "now"},
        ],
    )

    assert decision.chosen_adapter == "ollama-cloud"
    assert decision.candidates_considered[0]["worker_id"] == "deepseek-v4-pro@ollama-cloud"
    assert decision.candidates_considered[0]["failover_floor"] is True
    assert decision.candidates_considered[0]["marginal_cost_class"] == "flat_rate"
    assert decision.candidates_rejected[0]["worker_id"] == "gpt-5.3-codex@github-copilot"
    assert decision.candidates_rejected[0]["health_state"] == "stopped"
    assert "quota reserve protected" in decision.candidates_rejected[0]["rejected_reason"]


@pytest.mark.asyncio
async def test_dispatcher_completes_on_lower_flat_rate_rung_when_top_is_unavailable(tmp_path: Path) -> None:
    class FailingPremium:
        calls = 0

        async def dispatch(self, _envelope: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return {"ok": False, "error": "premium should not run"}

    class FlatRateFloor:
        calls = 0

        async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return {
                "ok": True,
                "model": envelope.get("model"),
                "text": "floor completed",
                "raw": {"proof": "live"},
            }

    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await _seed_workers(store)
    await store.upsert_services(
        [
            ServiceInfo(id="copilot-gh", name="Copilot", service_group="cloud_clients", adapter_name="copilot-gh", protocol="cli", health_state=HealthState.STOPPED),
            ServiceInfo(id="ollama-cloud", name="Ollama Cloud", service_group="cloud_clients", adapter_name="ollama-cloud", protocol="http", health_state=HealthState.HEALTHY),
        ]
    )
    await store.upsert_budget_probes(
        [
            {"id": "github_copilot:quota", "provider_id": "github_copilot", "probe_type": "subscription_quota", "remaining": 5, "limit": 100, "ok": True, "probed_at": "now"},
            {"id": "ollama-cloud:usage", "provider_id": "ollama-cloud", "probe_type": "subscription_usage", "remaining": 1000, "limit": 1000, "ok": True, "probed_at": "now"},
        ]
    )
    premium = FailingPremium()
    floor = FlatRateFloor()
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.adapters = {"copilot-gh": premium, "ollama-cloud": floor}  # type: ignore[assignment]
    dispatcher.max_attempts_per_adapter = 1

    result = await dispatcher.dispatch_text("fix this repo bug")

    assert result.state == "completed"
    assert result.adapter_name == "ollama-cloud"
    assert premium.calls == 0
    assert floor.calls == 1
    assert result.receipt["cost_class"] == BillingClass.SUBSCRIPTION_USAGE.value
    assert result.receipt["failover_ladder"][0]["worker_id"] == "deepseek-v4-pro@ollama-cloud"
    assert result.receipt["failover_ladder"][0]["marginal_cost_class"] == "flat_rate"
