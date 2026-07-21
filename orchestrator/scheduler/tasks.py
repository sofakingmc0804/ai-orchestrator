from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.backlog.intake import intake_discovered_work
from orchestrator.autopilot.watchers import scan_autopilot_folder_once
from orchestrator.autopilot.policy_engine import parse_policy_yaml
from orchestrator.config import Settings
from orchestrator.delegation.work_cycle import run_delegated_work_cycle
from orchestrator.evaluation.tournament import run_deterministic_tournament
from orchestrator.scheduler.budget_probes_cron import run_budget_probes_once
from orchestrator.scheduler.job_application_mailbox import run_job_application_mailbox_task
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


async def _run_operation_tournament_task(
    settings: Settings,
    store: StateStore,
    task: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    operation_task = payload.get("operation_task") if isinstance(payload.get("operation_task"), dict) else None
    workers = payload.get("workers") if isinstance(payload.get("workers"), list) else []
    worker_outputs = payload.get("worker_outputs") if isinstance(payload.get("worker_outputs"), dict) else {}
    if not operation_task or not workers or not worker_outputs:
        result["state"] = "failed"
        result["reason"] = "operation_tournament_payload_incomplete"
        result["completed_at"] = iso()
        await store.add_repair_item(
            "scheduler",
            f"Operation tournament task {task.get('id')} is missing operation_task, workers, or worker_outputs.",
            "Update the scheduler task payload with a deterministic operation task, workers, and captured outputs.",
        )
        await store.audit("scheduler", "scheduler_task_failed", str(task.get("id") or ""), result)
        return result

    async def output_runner(worker: dict[str, Any], _operation_task: dict[str, Any]) -> str:
        worker_id = str(worker.get("worker_id") or "")
        return str(worker_outputs.get(worker_id) or "")

    tournament = await run_deterministic_tournament(
        store,
        operation_task,
        [dict(worker) for worker in workers if isinstance(worker, dict)],
        output_runner,
        operation_domain=str(payload.get("operation_domain") or operation_task.get("domain_id") or "unknown"),
        run_id=str(result["run_id"]),
    )
    result.update(
        {
            "state": "completed",
            "queued": 0,
            "tournament": tournament,
            "completed_at": iso(),
        }
    )
    await store.mark_scheduler_task_run(str(task.get("id") or ""), int(task.get("interval_seconds") or 0))
    await store.audit("scheduler", "scheduler_task_run", str(task.get("id") or ""), result)
    return result


async def _run_budget_probes_task(
    settings: Settings,
    store: StateStore,
    task: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    try:
        budget_result = await run_budget_probes_once(store)
    except Exception as exc:
        result["state"] = "failed"
        result["reason"] = "budget_probes_cron_failed"
        result["error"] = str(exc)
        result["completed_at"] = iso()
        await store.add_repair_item(
            "scheduler:budget_probes_cron",
            f"Budget probe scheduler task {task.get('id')} failed: {exc}",
            "Repair the subscription/API probe path, then trigger task_budget_probes_cron.",
        )
        await store.audit("scheduler", "scheduler_task_failed", str(task.get("id") or ""), result)
        return result

    result.update(
        {
            "state": "completed",
            "queued": 0,
            "budget_probes": budget_result,
            "completed_at": iso(),
        }
    )
    await store.mark_scheduler_task_run(str(task.get("id") or ""), int(task.get("interval_seconds") or 0))
    await store.audit("scheduler", "scheduler_task_run", str(task.get("id") or ""), result)
    return result


async def _run_delegated_work_cycle_task(
    settings: Settings,
    store: StateStore,
    task: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    try:
        delegated_work = await run_delegated_work_cycle(
            settings,
            store,
            limit=int(payload.get("limit") or 1),
            min_surplus_percent=float(payload.get("min_surplus_percent") or 70.0),
            surplus_window_seconds=int(payload.get("surplus_window_seconds") or 7_200),
        )
    except Exception as exc:
        result["state"] = "failed"
        result["reason"] = "delegated_work_cycle_failed"
        result["error"] = str(exc)
        result["completed_at"] = iso()
        await store.add_repair_item(
            "scheduler:delegated_work_cycle",
            f"Delegated work cycle {task.get('id')} failed: {exc}",
            "Repair the delegated work cycle and trigger task_delegated_work_cycle again.",
        )
        await store.audit("scheduler", "scheduler_task_failed", str(task.get("id") or ""), result)
        return result

    result.update(
        {
            "state": "completed",
            "queued": int(delegated_work.get("completed") or 0),
            "delegated_work": delegated_work,
            "completed_at": iso(),
        }
    )
    await store.mark_scheduler_task_run(str(task.get("id") or ""), int(task.get("interval_seconds") or 0))
    await store.audit("scheduler", "scheduler_task_run", str(task.get("id") or ""), result)
    return result


async def _run_backlog_discovery_task(
    settings: Settings,
    store: StateStore,
    task: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    roots = [Path(str(value)) for value in payload.get("roots", []) if str(value).strip()] if isinstance(payload.get("roots"), list) else None
    try:
        backlog_discovery = await intake_discovered_work(
            settings,
            store,
            roots=roots,
            limit=int(payload.get("limit") or 3),
        )
    except Exception as exc:
        result["state"] = "failed"
        result["reason"] = "backlog_discovery_failed"
        result["error"] = str(exc)
        result["completed_at"] = iso()
        await store.add_repair_item(
            "scheduler:backlog_discovery",
            f"Backlog discovery task {task.get('id')} failed: {exc}",
            "Repair the source-backed backlog intake and rerun task_backlog_discovery.",
        )
        await store.audit("scheduler", "scheduler_task_failed", str(task.get("id") or ""), result)
        return result
    result.update(
        {
            "state": "completed",
            "queued": int(backlog_discovery.get("promoted") or 0),
            "backlog_discovery": backlog_discovery,
            "completed_at": iso(),
        }
    )
    await store.mark_scheduler_task_run(str(task.get("id") or ""), int(task.get("interval_seconds") or 0))
    await store.audit("scheduler", "scheduler_task_run", str(task.get("id") or ""), result)
    return result


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
    workspace_id = str(task.get("workspace_id") or "")
    enabled = bool(task.get("enabled"))
    result: dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "task_type": task_type,
        "workspace_id": workspace_id,
        "enabled": enabled,
        "state": "skipped",
        "queued": 0,
        "started_at": iso(),
    }

    if not enabled and not force:
        result["reason"] = "scheduler_task_disabled"
        result["completed_at"] = iso()
        return result
    if workspace_id not in {"personal", "example", "system", "unclassified_legacy"}:
        result["state"] = "failed"
        result["reason"] = "scheduler_workspace_identity_missing"
        result["completed_at"] = iso()
        await store.add_repair_item(
            "scheduler",
            f"Scheduler task {task_id} has no valid workspace identity.",
            "Assign personal, example, system, or unclassified_legacy before the task can run.",
        )
        await store.audit("scheduler", "scheduler_task_failed", task_id, result)
        return result
    if due_only and not force and not _is_due(task):
        result["reason"] = "scheduler_task_not_due"
        result["completed_at"] = iso()
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

    if task_type == "operation_tournament":
        return await _run_operation_tournament_task(settings, store, task, result)

    if task_type == "budget_probes_cron":
        return await _run_budget_probes_task(settings, store, task, result)

    if task_type == "delegated_work_cycle":
        return await _run_delegated_work_cycle_task(settings, store, task, result)

    if task_type == "backlog_discovery":
        return await _run_backlog_discovery_task(settings, store, task, result)

    if task_type == "job_application_mailbox":
        return await run_job_application_mailbox_task(settings, store, task)

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
