from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.models import BillingClass, Capability, ConsequenceTier
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.state.store import StateStore


class FakeAdapter:
    def __init__(self, name: str, failures_before_success: int = 0) -> None:
        self.name = name
        self.failures_before_success = failures_before_success
        self.calls = 0

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            return {"ok": False, "error": f"{self.name} failed {self.calls}", "repair_action": "fake repair"}
        return {"ok": True, "model": "fake", "text": f"{self.name} ok"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=Path.cwd(),
    )


def _cap(adapter: str, billing: BillingClass = BillingClass.LOCAL_RESOURCE, quality: int = 3) -> Capability:
    return Capability(
        id=f"{adapter}:classify_text",
        adapter_name=adapter,
        capability_id="classify_text",
        rating_instruction=3,
        rating_quality=quality,
        latency_band="medium",
        consequence_max=ConsequenceTier.MEDIUM,
        billing_class=billing,
    )


async def _dispatcher(tmp_path: Path, caps: list[Capability]) -> tuple[Dispatcher, StateStore]:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_capabilities(caps)
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.retry_backoff_seconds = [0, 0]
    return dispatcher, store


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt(tmp_path: Path) -> None:
    dispatcher, store = await _dispatcher(tmp_path, [_cap("flaky")])
    flaky = FakeAdapter("flaky", failures_before_success=1)
    dispatcher.adapters = {"flaky": flaky}  # type: ignore[assignment]

    result = await dispatcher.dispatch_text("classify retry smoke")

    assert result.state == "completed"
    assert result.adapter_name == "flaky"
    assert flaky.calls == 2
    attempts = await store.list_dispatch_attempts(result.dispatch_id)
    assert [a["state"] for a in attempts] == ["completed", "failed"]


@pytest.mark.asyncio
async def test_fallback_uses_next_safe_candidate(tmp_path: Path) -> None:
    dispatcher, store = await _dispatcher(tmp_path, [_cap("aaa-primary", quality=5), _cap("bbb-fallback", quality=4)])
    primary = FakeAdapter("aaa-primary", failures_before_success=99)
    fallback = FakeAdapter("bbb-fallback")
    dispatcher.adapters = {"aaa-primary": primary, "bbb-fallback": fallback}  # type: ignore[assignment]
    dispatcher.max_attempts_per_adapter = 2

    result = await dispatcher.dispatch_text("classify fallback smoke")

    assert result.state == "completed"
    assert result.adapter_name == "bbb-fallback"
    assert primary.calls == 2
    assert fallback.calls == 1
    repairs = await store.list_repair_queue()
    assert repairs[0]["failure_source"] == "aaa-primary"


@pytest.mark.asyncio
async def test_metered_candidate_is_not_used_as_fallback(tmp_path: Path) -> None:
    dispatcher, store = await _dispatcher(tmp_path, [_cap("aaa-primary"), _cap("metered-provider", BillingClass.METERED_EXTRA_COST, quality=5)])
    primary = FakeAdapter("aaa-primary", failures_before_success=99)
    metered = FakeAdapter("metered-provider")
    dispatcher.adapters = {"aaa-primary": primary, "metered-provider": metered}  # type: ignore[assignment]
    dispatcher.max_attempts_per_adapter = 1

    result = await dispatcher.dispatch_text("classify forbidden fallback smoke")

    assert result.state == "failed"
    assert primary.calls == 1
    assert metered.calls == 0
    attempts = await store.list_dispatch_attempts(result.dispatch_id)
    assert {a["adapter_name"] for a in attempts} == {"aaa-primary"}
