from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import orchestrator.process.repair_retry as repair_retry
from orchestrator.config import Settings
from orchestrator.models import DispatchResult
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_retry_open_repairs_proves_lm_studio_after_repair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_repair_lm_studio_local_server(_store: StateStore) -> dict[str, Any]:
        return {"service": "lm-studio", "state": "healthy", "models": {"ok": True, "models": [{"id": "local"}]}}

    async def fake_discover_services_and_capabilities() -> tuple[list[object], list[object]]:
        return [], []

    class FakeDispatcher:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def prove_adapter(self, adapter_name: str, prompt: str, capability_id: str | None = None, allow_subscription: bool = False) -> DispatchResult:
            calls.append(
                {
                    "adapter_name": adapter_name,
                    "prompt": prompt,
                    "capability_id": capability_id,
                    "allow_subscription": allow_subscription,
                }
            )
            return DispatchResult(dispatch_id="dsp_lm", intent_id="int_lm", adapter_name=adapter_name, state="completed", result_text="OK")

    monkeypatch.setattr(repair_retry, "repair_lm_studio_local_server", fake_repair_lm_studio_local_server)
    monkeypatch.setattr(repair_retry, "discover_services_and_capabilities", fake_discover_services_and_capabilities)
    monkeypatch.setattr(repair_retry, "Dispatcher", FakeDispatcher)
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.add_repair_item("lm-studio", "server off", "start local server")

    result = await repair_retry.retry_open_repairs(settings, store)

    assert result["attempted"] == 1
    assert calls == [
        {
            "adapter_name": "lm-studio",
            "prompt": "LM Studio repair proof: answer OK.",
            "capability_id": "local_chat",
            "allow_subscription": False,
        }
    ]
    assert result["results"][0]["proof"]["state"] == "completed"
