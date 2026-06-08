from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.autopilot.watchers import scan_autopilot_folder_once
from orchestrator.autopilot.policy_engine import parse_policy_yaml
from orchestrator.config import Settings
from orchestrator.state.store import StateStore, iso


async def run_standing_order_once(settings: Settings, store: StateStore, order: dict[str, Any]) -> dict[str, Any]:
    run_id = f"srun_{uuid.uuid4().hex[:12]}"
    order_id = str(order.get("id") or "")
    folder_path = str(order.get("folder_path") or "")
    enabled = bool(order.get("enabled"))
    result: dict[str, Any] = {
        "run_id": run_id,
        "order_id": order_id,
        "folder_path": folder_path,
        "enabled": enabled,
        "state": "skipped",
        "queued": 0,
        "started_at": iso(),
    }

    if not enabled:
        result["reason"] = "standing_order_disabled"
        result["completed_at"] = iso()
        await store.audit("scheduler", "standing_order_skipped", order_id, result)
        return result

    folder = Path(folder_path).expanduser()
    if not folder.exists() or not folder.is_dir():
        result["state"] = "failed"
        result["reason"] = "folder_missing"
        result["completed_at"] = iso()
        await store.add_repair_item(
            "scheduler",
            f"Standing order {order_id} points to missing folder: {folder}",
            "Restore the folder or update the standing order folder_path.",
        )
        await store.audit("scheduler", "standing_order_failed", order_id, result)
        return result

    policy = parse_policy_yaml(str(order.get("policy_yaml") or ""))
    scan = await scan_autopilot_folder_once(
        settings,
        store,
        folder,
        policy_override=policy,
        policy_label=f"standing_order:{order_id}",
    )
    await store.mark_standing_order_fired(order_id)
    result.update(
        {
            "state": "completed",
            "queued": int(scan.get("queued") or 0),
            "scan": scan,
            "completed_at": iso(),
        }
    )
    await store.audit("scheduler", "standing_order_run", order_id, result)
    return result


async def run_scheduler_once(settings: Settings, store: StateStore | None = None) -> dict[str, Any]:
    return await _run_scheduler_tasks(settings, store, due_only=False, audit_action="scheduler_run_once")


async def run_due_scheduler_once(settings: Settings, store: StateStore | None = None) -> dict[str, Any]:
    return await _run_scheduler_tasks(settings, store, due_only=True, audit_action="scheduler_due_run_once")


async def _run_scheduler_tasks(settings: Settings, store: StateStore | None, *, due_only: bool, audit_action: str) -> dict[str, Any]:
    owns_store = store is None
    store = store or StateStore(settings)
    if owns_store:
        await store.initialize()

    await store.migrate_standing_orders_to_scheduler_tasks()
    tasks = await store.list_scheduler_tasks()
    runs = [await run_scheduler_task_once(settings, store, task, due_only=due_only) for task in tasks]
    summary = {
        "started_at": runs[0]["started_at"] if runs else iso(),
        "completed_at": iso(),
        "tasks": len(tasks),
        "orders": len(tasks),
        "completed": sum(1 for run in runs if run["state"] == "completed"),
        "skipped": sum(1 for run in runs if run["state"] == "skipped"),
        "failed": sum(1 for run in runs if run["state"] == "failed"),
        "queued": sum(int(run.get("queued") or 0) for run in runs),
        "runs": runs,
    }
    await store.audit("scheduler", audit_action, "scheduler_tasks", summary)
    return summary


def _is_due(task: dict[str, Any]) -> bool:
    next_run_at = task.get("next_run_at")
    if not next_run_at:
        return True
    try:
        due_at = datetime.fromisoformat(str(next_run_at))
    except ValueError:
        return True
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at <= datetime.now(timezone.utc)


async def run_scheduler_task_once(
    settings: Settings,
    store: StateStore,
    task: dict[str, Any],
    *,
    force: bool = False,
    due_only: bool = False,
) -> dict[str, Any]:
    run_id = f"srun_{uuid.uuid4().hex[:12]}"
    task_id = str(task.get("id") or "")
    task_type = str(task.get("task_type") or "")
    enabled = bool(task.get("enabled"))
    result: dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "task_type": task_type,
        "enabled": enabled,
        "state": "skipped",
        "queued": 0,
        "started_at": iso(),
    }

    if not enabled and not force:
        result["reason"] = "scheduler_task_disabled"
        result["completed_at"] = iso()
        await store.audit("scheduler", "scheduler_task_skipped", task_id, result)
        return result
    if due_only and not force and not _is_due(task):
        result["reason"] = "scheduler_task_not_due"
        result["completed_at"] = iso()
        await store.audit("scheduler", "scheduler_task_skipped", task_id, result)
        return result

    if task_type == "standing_order_scan":
        orders = await store.list_standing_orders()
        order = next((item for item in orders if str(item.get("id")) == str(task.get("target_ref"))), None)
        if not order:
            result["state"] = "failed"
            result["reason"] = "standing_order_missing"
            result["completed_at"] = iso()
            await store.add_repair_item(
                "scheduler",
                f"Scheduler task {task_id} references missing standing order: {task.get('target_ref')}",
                "Restore the standing order or edit the scheduler task target_ref.",
            )
            await store.audit("scheduler", "scheduler_task_failed", task_id, result)
            return result
        if force:
            order = dict(order)
            order["enabled"] = 1
        standing_result = await run_standing_order_once(settings, store, order)
        result.update(
            {
                "state": standing_result["state"],
                "queued": int(standing_result.get("queued") or 0),
                "standing_order": standing_result,
                "completed_at": iso(),
            }
        )
        if result["state"] == "completed":
            await store.mark_scheduler_task_run(task_id, int(task.get("interval_seconds") or 0))
            await store.audit("scheduler", "scheduler_task_run", task_id, result)
        return result

    result["state"] = "failed"
    result["reason"] = f"unsupported_task_type:{task_type}"
    result["completed_at"] = iso()
    await store.add_repair_item("scheduler", f"Unsupported scheduler task type: {task_type}", "Implement a scheduler task handler or change the task_type.")
    await store.audit("scheduler", "scheduler_task_failed", task_id, result)
    return result


async def trigger_scheduler_task(settings: Settings, task_id: str, store: StateStore | None = None) -> dict[str, Any]:
    owns_store = store is None
    store = store or StateStore(settings)
    if owns_store:
        await store.initialize()
    await store.migrate_standing_orders_to_scheduler_tasks()
    task = await store.get_scheduler_task(task_id)
    if not task:
        result = {"task_id": task_id, "state": "failed", "reason": "scheduler_task_missing", "queued": 0, "started_at": iso(), "completed_at": iso()}
        await store.add_repair_item("scheduler", f"Manual trigger requested missing scheduler task: {task_id}", "Refresh scheduler tasks and retry with a valid task id.")
        await store.audit("scheduler", "scheduler_task_trigger_failed", task_id, result)
        return result
    result = await run_scheduler_task_once(settings, store, task, force=True)
    await store.audit("scheduler", "scheduler_task_triggered", task_id, result)
    return result
