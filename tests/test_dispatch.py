from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.cli.main import _dispatch
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.models import BillingClass, Capability, ConsequenceTier, ServiceInfo
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_high_consequence_intent_creates_approval_request(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    services, capabilities = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(capabilities)
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    result = await dispatcher.dispatch_text("send this externally after editing")
    assert result.state == "awaiting_approval"
    assert "approval_request" in settings.notifications_path.read_text(encoding="utf-8")

    approvals = await store.list_pending_approvals()
    assert len(approvals) == 1
    rejected = await dispatcher.approve_intent(result.intent_id, approved=False)
    assert rejected.state == "rejected"
    assert await store.list_pending_approvals() == []
    row = await store.get_intent(result.intent_id)
    assert row is not None
    assert row["state"] == "rejected"


@pytest.mark.asyncio
async def test_prove_adapter_records_completed_local_receipt(tmp_path: Path) -> None:
    class FakeLocalAdapter:
        async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "model": "fake-local", "text": f"proved {envelope['dispatch_id']}"}

    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_services([ServiceInfo(id="fake-local", name="Fake Local", service_group="test", adapter_name="fake-local", protocol="test")])
    await store.upsert_capabilities(
        [
            Capability(
                id="fake-local:local_chat",
                adapter_name="fake-local",
                capability_id="local_chat",
                rating_instruction=3,
                rating_quality=3,
                latency_band="fast",
                consequence_max=ConsequenceTier.LOW,
                billing_class=BillingClass.LOCAL_RESOURCE,
            )
        ]
    )
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.adapters = {"fake-local": FakeLocalAdapter()}  # type: ignore[assignment]

    result = await dispatcher.prove_adapter("fake-local", "prove it")

    assert result.state == "completed"
    assert result.receipt["service"] == "fake-local"
    assert result.receipt["cost_class"] == BillingClass.LOCAL_RESOURCE.value
    assert result.output_path is not None
    assert result.output_path.exists()
    token_rows = await store.list_token_usage(dispatch_id=result.dispatch_id)
    assert len(token_rows) == 1
    assert token_rows[0]["success"] == 1
    assert token_rows[0]["confidence"] == "estimated"
    assert result.receipt["tokens_in"] == token_rows[0]["tokens_in"]


@pytest.mark.asyncio
async def test_prove_adapter_denies_subscription_without_explicit_allowance(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_services([ServiceInfo(id="quota-backed", name="Quota Backed", service_group="test", adapter_name="quota-backed", protocol="test")])
    await store.upsert_capabilities(
        [
            Capability(
                id="quota-backed:code_repair",
                adapter_name="quota-backed",
                capability_id="code_repair",
                rating_instruction=3,
                rating_quality=3,
                latency_band="medium",
                consequence_max=ConsequenceTier.MEDIUM,
                billing_class=BillingClass.SUBSCRIPTION_QUOTA,
            )
        ]
    )
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))

    result = await dispatcher.prove_adapter("quota-backed", "prove it")

    assert result.state == "failed"
    assert "--allow-subscription" in str(result.error)
    assert await store.list_dispatches() == []


@pytest.mark.asyncio
async def test_cli_dispatch_returns_compact_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeDispatcher:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def dispatch_text(self, text: str) -> Any:
            return type(
                "Result",
                (),
                {
                    "state": "completed",
                    "dispatch_id": "dsp_test",
                    "intent_id": "int_test",
                    "adapter_name": "ollama-http",
                    "output_path": tmp_path / "result.txt",
                    "error": None,
                    "result_text": "x" * 1000,
                    "receipt": {"model": "qwen", "receipt_path": str(tmp_path / "receipt.json"), "routing_decision": {"large": "y" * 5000}},
                    "model_dump": lambda self, mode="json": {"receipt": self.receipt, "result_text": self.result_text},
                },
            )()

    async def fake_discover() -> tuple[list[ServiceInfo], list[Capability]]:
        return [], []

    monkeypatch.setattr("orchestrator.cli.main.Dispatcher", FakeDispatcher)
    monkeypatch.setattr("orchestrator.cli.main.discover_services_and_capabilities", fake_discover)
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)

    compact = await _dispatch(settings, "short task")

    assert compact["dispatch_id"] == "dsp_test"
    assert compact["receipt_path"] == str(tmp_path / "receipt.json")
    assert "receipt" not in compact
    assert len(str(compact["result_preview"])) == 500


def test_budget_probe_provider_aliases_match_runtime_surfaces(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))

    assert dispatcher._budget_probe_for_provider("ollama-local", [{"provider_id": "ollama"}]) == {"provider_id": "ollama"}
    assert dispatcher._budget_probe_for_provider("github-copilot", [{"provider_id": "github_copilot"}]) == {"provider_id": "github_copilot"}
    assert dispatcher._budget_probe_for_provider("claude-max", [{"provider_id": "claude"}]) == {"provider_id": "claude"}


@pytest.mark.asyncio
async def test_worker_dispatch_fallback_uses_candidate_specific_model(tmp_path: Path) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.models: list[str] = []

        async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
            model = str(envelope.get("model") or "")
            self.models.append(model)
            if model == "slow-model":
                return {"ok": False, "error": "slow failed", "repair_action": "try next worker"}
            return {"ok": True, "model": model, "text": "fast ok"}

    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
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
    for model in ("slow-model", "fast-model"):
        await store.db.execute(
            """
            INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
              capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            f"{model}@ollama-local",
            model,
            model,
            "ollama-local",
            "ollama-local",
            "local_resource",
            '["coding"]',
            '["tools"]',
            '["text"]',
            '{"coding": 8}',
            '["repo_coding"]',
            "[]",
        )
    fake = FakeAdapter()
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.adapters = {"ollama-http": fake}  # type: ignore[assignment]
    dispatcher.max_attempts_per_adapter = 1

    result = await dispatcher.dispatch_text("fix this repo bug")

    assert result.state == "completed"
    assert fake.models == ["slow-model", "fast-model"]
    assert result.receipt["model"] == "fast-model"
    token_rows = await store.list_token_usage(dispatch_id=result.dispatch_id)
    assert len(token_rows) == 2
    assert sorted(row["success"] for row in token_rows) == [0, 1]
    assert result.receipt["tokens_in"] == sum(int(row["tokens_in"] or 0) for row in token_rows)
    assert result.receipt["tokens_out"] == sum(int(row["tokens_out"] or 0) for row in token_rows)
