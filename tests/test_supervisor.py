from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.process.supervisor import (
    BUDGET_PROBES_INTERVAL_SECONDS,
    BUDGET_PROBES_TASK_ID,
    ensure_budget_probe_scheduler_task,
    run_supervisor_tick,
    start_supervisor_thread,
)
from orchestrator.scheduler.tasks import run_scheduler_task_once
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    home = tmp_path / ".orchestrator"
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=Path.cwd(),
    )


@pytest.mark.asyncio
async def test_supervisor_tick_recovers_dispatch_and_writes_live_receipt(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.record_dispatch(
        {
            "id": "dsp_started",
            "intent_id": "int_started",
            "adapter_name": "ollama-http",
            "envelope": {"intent": {"raw_text": "resume this"}},
            "state": "started",
            "started_at": "2026-06-14T00:00:00+00:00",
        }
    )

    async def fake_scheduler(_settings: Settings, _store: StateStore) -> dict[str, object]:
        return {"state": "completed", "tasks": 0}

    async def fake_repair(_store: StateStore) -> dict[str, object]:
        return {"state": "completed", "checked": 1}

    receipt = await run_supervisor_tick(settings, store, scheduler_once=fake_scheduler, repair_core=fake_repair)

    assert receipt["proof_kind"] == "live"
    assert receipt["state"] == "produced"
    assert receipt["restart_recovery_proved"] is True
    assert receipt["recovered_dispatches_count"] == 1
    assert receipt["budget_task_id"] == BUDGET_PROBES_TASK_ID
    assert Path(str(receipt["receipt_path"])).exists()
    payload = json.loads(Path(str(receipt["receipt_path"])).read_text(encoding="utf-8"))
    assert payload["recovered_dispatches"][0]["dispatch_id"] == "dsp_started"
    task = await store.get_scheduler_task(BUDGET_PROBES_TASK_ID)
    assert task is not None
    assert task["task_type"] == "budget_probes_cron"
    assert task["interval_seconds"] == BUDGET_PROBES_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_budget_probes_cron_scheduler_task_runs_probe_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await ensure_budget_probe_scheduler_task(store)
    task = await store.get_scheduler_task(BUDGET_PROBES_TASK_ID)
    assert task is not None

    async def fake_run_budget_probes_once(_store: StateStore) -> dict[str, object]:
        return {
            "subscription_stored": 1,
            "subscription_failed": 0,
            "subscription_total": 1,
            "api_probe_stored": 2,
            "api_probe_failed": 0,
            "api_probe_total": 2,
        }

    monkeypatch.setattr("orchestrator.scheduler.tasks.run_budget_probes_once", fake_run_budget_probes_once)

    result = await run_scheduler_task_once(settings, store, task, force=True)

    assert result["state"] == "completed"
    assert result["budget_probes"]["subscription_stored"] == 1
    refreshed = await store.get_scheduler_task(BUDGET_PROBES_TASK_ID)
    assert refreshed is not None
    assert refreshed["next_run_at"]


def test_supervisor_thread_stops_cleanly(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ticks: list[str] = []

    async def fake_tick(_settings: Settings) -> dict[str, object]:
        ticks.append("tick")
        return {"state": "produced"}

    thread = start_supervisor_thread(settings, seconds=0.02, tick_runner=fake_tick)
    thread.stop_event.wait(0.08)  # type: ignore[attr-defined]
    thread.stop_event.set()  # type: ignore[attr-defined]
    thread.join(timeout=2)

    assert ticks
    assert not thread.is_alive()


def test_startup_scripts_use_supervised_fastapi_service() -> None:
    start_script = Path("scripts/start-orchestrator.ps1").read_text(encoding="utf-8")
    install_script = Path("scripts/install-startup-task.ps1").read_text(encoding="utf-8")

    assert "orchestrator.main" in start_script
    assert "orchestrator.ui.simple_main" not in start_script
    assert "-WindowStyle Hidden" in start_script
    assert "watchdog_restart" in start_script
    assert "proof_kind = \"live\"" in start_script
    assert "-Watchdog" in install_script
    assert "-RestartCount 3" in install_script
