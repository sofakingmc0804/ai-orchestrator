from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
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
