from __future__ import annotations

import asyncio
import json
import platform
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Any

from orchestrator.config import Settings
from orchestrator.process.recovery import repair_core_services
from orchestrator.scheduler.tasks import run_due_scheduler_once
from orchestrator.state.store import StateStore, iso


BUDGET_PROBES_TASK_ID = "task_budget_probes_cron"
BUDGET_PROBES_INTERVAL_SECONDS = 15 * 60
SUPERVISOR_TICK_SECONDS = 5 * 60


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def supervisor_dir(settings: Settings) -> Path:
    path = settings.home / "supervisor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_supervisor_receipt(settings: Settings, payload: dict[str, Any]) -> Path:
    receipt_dir = supervisor_dir(settings)
    receipt_id = str(payload.get("id") or f"sup_{uuid.uuid4().hex[:16]}")
    path = receipt_dir / f"{_stamp()}-{receipt_id}.json"
    payload["receipt_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


async def ensure_budget_probe_scheduler_task(store: StateStore) -> str:
    existing = await store.get_scheduler_task(BUDGET_PROBES_TASK_ID)
    if (
        existing
        and existing.get("task_type") == "budget_probes_cron"
        and int(existing.get("interval_seconds") or 0) == BUDGET_PROBES_INTERVAL_SECONDS
        and bool(existing.get("enabled"))
    ):
        return BUDGET_PROBES_TASK_ID

    await store.upsert_scheduler_task(
        name="Subscription and API budget refresh",
        task_type="budget_probes_cron",
        target_ref="subscription_usage_snapshots",
        payload={
            "consumer": "routing_budget_lookup",
            "proof_kind": "live",
            "sources": ["subscription_usage_snapshots", "budget_probes"],
        },
        schedule_kind="interval",
        interval_seconds=BUDGET_PROBES_INTERVAL_SECONDS,
        enabled=True,
        task_id=BUDGET_PROBES_TASK_ID,
        next_run_at=(
            str(existing.get("next_run_at"))
            if existing and existing.get("next_run_at")
            else iso(datetime.now(timezone.utc) + timedelta(seconds=BUDGET_PROBES_INTERVAL_SECONDS))
        ),
    )
    return BUDGET_PROBES_TASK_ID


async def run_supervisor_tick(
    settings: Settings,
    store: StateStore | None = None,
    *,
    scheduler_once: Callable[[Settings, StateStore], Awaitable[dict[str, Any]]] = run_due_scheduler_once,
    repair_core: Callable[[StateStore], Awaitable[dict[str, Any]]] = repair_core_services,
) -> dict[str, Any]:
    owns_store = store is None
    state = store or StateStore(settings)
    if owns_store:
        await state.initialize()

    started_at = iso()
    errors: list[dict[str, str]] = []
    recovered = await state.recover_interrupted_dispatches()
    budget_task_id = await ensure_budget_probe_scheduler_task(state)

    try:
        scheduler_result = await scheduler_once(settings, state)
    except Exception as exc:
        scheduler_result = {"state": "failed", "error": str(exc)}
        errors.append({"mechanism": "scheduler", "error": str(exc)})
        await state.add_repair_item(
            "supervisor:scheduler",
            f"Supervisor scheduler tick failed: {exc}",
            "Repair scheduler task execution and rerun the supervisor tick.",
        )

    try:
        repair_result = await repair_core(state)
    except Exception as exc:
        repair_result = {"state": "failed", "error": str(exc)}
        errors.append({"mechanism": "repair_core_services", "error": str(exc)})
        await state.add_repair_item(
            "supervisor:repair",
            f"Supervisor health/repair loop failed: {exc}",
            "Repair the core service repair loop and rerun the supervisor tick.",
        )

    receipt = {
        "id": f"sup_{uuid.uuid4().hex[:16]}",
        "event": "supervisor_tick",
        "state": "produced" if not errors else "blocked_after_repair_attempt",
        "proof_kind": "live",
        "machine": platform.node(),
        "repo_root": str(settings.repo_root),
        "state_path": str(settings.state_path),
        "started_at": started_at,
        "completed_at": iso(),
        "budget_task_id": budget_task_id,
        "budget_task_interval_seconds": BUDGET_PROBES_INTERVAL_SECONDS,
        "recovered_dispatches_count": len(recovered),
        "recovered_dispatches": recovered,
        "scheduler": scheduler_result,
        "repair": repair_result,
        "errors": errors,
        "restart_recovery_proved": bool(recovered),
    }
    write_supervisor_receipt(settings, receipt)
    await state.audit("supervisor", "supervisor_tick", "ai-orchestrator", receipt)
    return receipt


async def _sleep_or_stop(stop_event: Event, seconds: float) -> bool:
    return bool(await asyncio.to_thread(stop_event.wait, seconds))


def start_supervisor_thread(
    settings: Settings,
    *,
    seconds: float = SUPERVISOR_TICK_SECONDS,
    tick_runner: Callable[[Settings], Awaitable[dict[str, Any]]] | None = None,
) -> Thread:
    stop_event = Event()
    runner = tick_runner or (lambda active_settings: run_supervisor_tick(active_settings))

    async def loop() -> None:
        failures = 0
        while not stop_event.is_set():
            try:
                await runner(settings)
                failures = 0
                delay = seconds
            except Exception as exc:
                failures += 1
                delay = min(max(2.0, 2.0**failures), 300.0)
                write_supervisor_receipt(
                    settings,
                    {
                        "id": f"sup_{uuid.uuid4().hex[:16]}",
                        "event": "supervisor_tick_crash",
                        "state": "blocked_after_repair_attempt",
                        "proof_kind": "live",
                        "machine": platform.node(),
                        "repo_root": str(settings.repo_root),
                        "state_path": str(settings.state_path),
                        "started_at": iso(),
                        "completed_at": iso(),
                        "error": str(exc),
                        "backoff_seconds": delay,
                    },
                )
            if await _sleep_or_stop(stop_event, delay):
                break

    def thread_main() -> None:
        asyncio.run(loop())

    thread = Thread(target=thread_main, name="ai-orchestrator-supervisor", daemon=True)
    thread.stop_event = stop_event  # type: ignore[attr-defined]
    thread.start()
    return thread
