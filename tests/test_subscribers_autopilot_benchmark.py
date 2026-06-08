from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import orchestrator.benchmarks.latency as latency
from orchestrator.autopilot.watchers import scan_autopilot_folder_once
from orchestrator.config import Settings
from orchestrator.models import DispatchResult, Notification
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.notifications.subscribers.runner import run_all_subscribers_once
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=Path.cwd(),
    )


@pytest.mark.asyncio
async def test_subscribers_create_email_draft_and_receipts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    spine = NotificationSpine(settings.notifications_path, store)
    await spine.publish(
        Notification(
            id="ntf_test",
            severity="error",
            title="Subscriber smoke",
            body="repair this",
            channels_requested=["email", "in_app"],
        )
    )

    result = await run_all_subscribers_once(settings)

    assert result["subscribers"][0]["delivered"] == 1
    assert result["subscribers"][1]["delivered"] == 1
    assert (settings.home / "email_drafts" / "ntf_test.eml").exists()
    assert (settings.home / "subscribers" / "email.jsonl").exists()


@pytest.mark.asyncio
async def test_autopilot_requires_enabled_policy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "input.txt").write_text("work", encoding="utf-8")

    disabled = await scan_autopilot_folder_once(settings, store, folder)
    assert disabled["enabled"] is False
    assert disabled["queued"] == 0

    (folder / ".orchestrator-policy.yaml").write_text("autopilot: enabled\ndefault_intent: Classify this file.\n", encoding="utf-8")
    enabled = await scan_autopilot_folder_once(settings, store, folder)
    assert enabled["enabled"] is True
    assert enabled["queued"] == 1
    selections = await store.list_selections()
    assert selections[0]["payload"]["source"] == "autopilot"


@pytest.mark.asyncio
async def test_latency_benchmark_writes_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def fake_discover() -> tuple[list[Any], list[Any]]:
        return [], []

    async def fake_dispatch(self: object, raw_text: str, project_root: Path | None = None) -> DispatchResult:
        return DispatchResult(dispatch_id="dsp_test", intent_id="int_test", adapter_name="ollama-http", state="completed", result_text="ok")

    monkeypatch.setattr(latency, "discover_services_and_capabilities", fake_discover)
    monkeypatch.setattr(latency.Dispatcher, "dispatch_text", fake_dispatch)
    result = await latency.run_selection_latency_benchmark(settings)

    assert result["passed"] is True
    assert Path(str(result["receipt_path"])).exists()
