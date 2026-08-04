from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

from orchestrator.autopilot.watchers import scan_autopilot_roots_once
from orchestrator.benchmarks.latency import run_selection_latency_benchmark
from orchestrator.cli.delegation import register_delegation_cli
from orchestrator.cli.governance import register_governance_cli
from orchestrator.cli.budget_cli import register_budget_cli
from orchestrator.config import Settings
from orchestrator.discovery.auth import probe_auth_and_quota, quota_snapshots
from orchestrator.discovery.budget_probes import run_all_probes, probe_to_dict
from orchestrator.discovery.model_lifecycle import probe_model_lifecycle
from orchestrator.discovery.projects import discover_projects
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.governance.job_classifier import classify_with_fallback
from orchestrator.hermes.brain_bridge import route_for_hermes_prompt
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.notifications.subscribers.runner import run_all_subscribers_once
from orchestrator.process.recovery import repair_core_services
from orchestrator.process.repair_retry import retry_open_repairs
from orchestrator.reports.acceptance_battery import run_acceptance_battery
from orchestrator.reports.owner_receipt import export_owner_receipt
from orchestrator.scheduler.migration import migrate_legacy_scheduled_tasks
from orchestrator.scheduler.tasks import run_scheduler_once, trigger_scheduler_task
from orchestrator.skills.detector import detect_skill_route
from orchestrator.skills.gate import prompt_for_context
from orchestrator.skills.models import SkillHookPlan
from orchestrator.skills.runtime import run_hook_event
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore
from orchestrator.ui.dashboard_status import build_dashboard_status
from orchestrator.usage.flow import build_token_flow_payload


async def _refresh(settings: Settings) -> dict[str, int]:
    store = StateStore(settings)
    await store.initialize()
    services, caps = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(caps)
    projects = discover_projects([Path.home(), Path.home() / "Documents", Path("D:/SharedRoot")], max_depth=3)
    await store.upsert_projects(projects)
    auth_state = probe_auth_and_quota()
    await store.record_quota_snapshots(quota_snapshots(auth_state))
    # Model lifecycle: detect retired/new models, auto-fix vision config
    lifecycle = await probe_model_lifecycle(settings, fix_config=True)
    await store.record_discovery("refresh", {
        "services": len(services),
        "capabilities": len(caps),
        "projects": len(projects),
        "model_lifecycle": {
            "retired": lifecycle["retired_count"],
            "discovered": lifecycle["discovered_count"],
            "vision_fix": lifecycle.get("vision_fix"),
        },
    }, "CLI refresh completed with model lifecycle probe.")
    return {
        "services": len(services),
        "capabilities": len(caps),
        "projects": len(projects),
        "models_retired": lifecycle["retired_count"],
        "models_discovered": lifecycle["discovered_count"],
    }


async def _dispatch(settings: Settings, text: str, full: bool = False) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    services, caps = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(caps)
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    result = await dispatcher.dispatch_text(text)
    payload = result.model_dump(mode="json")
    if full:
        return payload
    receipt = result.receipt or {}
    return {
        "state": result.state,
        "dispatch_id": result.dispatch_id,
        "intent_id": result.intent_id,
        "adapter_name": result.adapter_name,
        "model": receipt.get("model"),
        "output_path": str(result.output_path) if result.output_path else None,
        "receipt_path": receipt.get("receipt_path"),
        "error": result.error,
        "result_preview": (result.result_text or "")[:500] if result.result_text else None,
    }


async def _prove_adapter(settings: Settings, adapter_name: str, prompt: str, capability_id: str | None, allow_subscription: bool) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    services, caps = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(caps)
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    result = await dispatcher.prove_adapter(adapter_name, prompt, capability_id=capability_id, allow_subscription=allow_subscription)
    return result.model_dump(mode="json")


async def _acceptance_battery(settings: Settings) -> dict[str, object]:
    return await run_acceptance_battery(settings)


async def _add_selection(settings: Settings, path: str, kind: str, source: str = "cli") -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    selection_path = Path(path).expanduser()
    payload: dict[str, object] = {
        "path": str(selection_path),
        "exists": selection_path.exists(),
        "source": source,
    }
    if selection_path.exists():
        payload["is_dir"] = selection_path.is_dir()
        payload["is_file"] = selection_path.is_file()
    selection = await store.add_selection(kind, payload)
    await store.record_discovery(
        "selection",
        {"id": selection.id, "kind": selection.kind, "path": str(selection_path)},
        "CLI selection queued.",
    )
    return selection.model_dump(mode="json")


async def _approval(settings: Settings, intent_id: str, approved: bool) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    result = await dispatcher.approve_intent(intent_id, approved)
    return result.model_dump(mode="json")


async def _repair_services(settings: Settings) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return await repair_core_services(store)


async def _retry_repairs(settings: Settings) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return await retry_open_repairs(settings, store)


async def _notify_once(settings: Settings, include_tray: bool = False) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    result = await run_all_subscribers_once(settings, include_tray=include_tray)
    for subscriber in result.get("subscribers", []):
        if not isinstance(subscriber, dict):
            continue
        if int(subscriber.get("delivered") or 0) > 0:
            await store.resolve_repair_items(
                f"notification_subscriber:{subscriber.get('subscriber')}",
                {"delivered": int(subscriber.get("delivered") or 0)},
            )
        if int(subscriber.get("failures") or 0) > 0:
            await store.add_repair_item(
                f"notification_subscriber:{subscriber.get('subscriber')}",
                f"{subscriber.get('failures')} notification delivery failure(s)",
                "Inspect subscriber receipt JSONL and repair the local notification provider.",
            )
    await store.audit("subscriber", "notify_once", "notifications", result)
    return result


async def _benchmark_latency(settings: Settings) -> dict[str, object]:
    return await run_selection_latency_benchmark(settings)


async def _autopilot_scan(settings: Settings, roots: list[str]) -> dict[str, object]:
    return await scan_autopilot_roots_once(settings, [Path(root) for root in roots])


async def _add_standing_order(settings: Settings, folder: str, policy: str, enabled: bool) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    order_id = await store.upsert_standing_order(folder, policy, enabled)
    return {"id": order_id, "folder_path": folder, "enabled": enabled}


async def _run_scheduler_once(settings: Settings) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return await run_scheduler_once(settings, store)


async def _migrate_legacy_scheduler(settings: Settings) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return await migrate_legacy_scheduled_tasks(settings, store)


async def _add_scheduler_task(settings: Settings, folder: str, policy: str, enabled: bool, interval_seconds: int, name: str | None, task_id: str | None) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    order_id = await store.upsert_standing_order(folder, policy, enabled)
    scheduler_task_id = await store.upsert_scheduler_task(
        name=name or f"Standing order: {Path(folder).name or order_id}",
        task_type="standing_order_scan",
        target_ref=order_id,
        payload={"folder_path": folder},
        schedule_kind="interval" if interval_seconds > 0 else "manual",
        interval_seconds=interval_seconds,
        enabled=enabled,
        task_id=task_id,
    )
    return {"id": scheduler_task_id, "standing_order_id": order_id, "folder_path": folder, "enabled": enabled, "interval_seconds": interval_seconds}


async def _set_scheduler_task_enabled(settings: Settings, task_id: str, enabled: bool) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    await store.set_scheduler_task_enabled(task_id, enabled)
    return {"id": task_id, "enabled": enabled}


async def _activate_scheduler_task(settings: Settings, task_id: str, note: str) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return await store.activate_scheduler_task(task_id, reviewer="owner", note=note)


async def _review_scheduler_task(settings: Settings, task_id: str, review_state: str, note: str) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return await store.review_scheduler_task(task_id, review_state, reviewer="owner", note=note)


async def _trigger_scheduler_task(settings: Settings, task_id: str) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return await trigger_scheduler_task(settings, task_id, store)


async def _status(settings: Settings) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    auth_state = probe_auth_and_quota()
    await store.record_quota_snapshots(quota_snapshots(auth_state))
    return {
        "services": await store.list_services(),
        "capabilities": await store.list_capabilities(),
        "projects": await store.list_projects(),
        "approvals": await store.list_pending_approvals(),
        "quota_ledger": await store.list_quota_ledger(),
        "quota_state": await store.latest_quota_state(),
        "repair_queue": await store.list_repair_queue(),
        "dispatches": await store.list_dispatches(limit=50),
        "dispatch_attempts": await store.list_dispatch_attempts(limit=100),
        "standing_orders": await store.list_standing_orders(),
        "scheduler_tasks": await store.list_scheduler_tasks(),
        "working_memory": await store.list_working_memory(),
        "audit_log": await store.list_audit_log(limit=50),
        "activity": await store.list_activity(limit=50),
        "auth_quota": auth_state,
        "state_path": str(settings.state_path),
        "notifications_path": str(settings.notifications_path),
    }


async def _spec_status(settings: Settings) -> dict[str, object]:
    store = StateStore(settings)
    return await evaluate_spec_status(settings, store)


def _dashboard_status(settings: Settings, host: str, port: int, write_receipt: bool) -> dict[str, object]:
    return build_dashboard_status(settings, host=host, port=port, write_receipt=write_receipt)


async def _owner_receipt(settings: Settings) -> dict[str, object]:
    return await export_owner_receipt(settings)


async def _token_flow(settings: Settings, limit: int = 50) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    payload = await build_token_flow_payload(store, limit=limit)
    payload["state_path"] = str(settings.state_path)
    return payload


async def _token_backfill(settings: Settings, limit: int = 10000) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    result = await store.backfill_token_usage_from_receipts(limit=limit)
    flow = await build_token_flow_payload(store, limit=50)
    return {"state": "repaired", "state_path": str(settings.state_path), "backfill": result, "token_flow": flow["totals"]}


async def _skill_route(settings: Settings, text: str, cwd: str | None = None) -> dict[str, object]:
    plan = detect_skill_route(text, cwd=cwd or settings.repo_root, source_event="cli")
    return {"state": "produced", "skill_hook_plan": plan.receipt_payload()}


def _infer_hermes_job_class(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--orchestrator-job-class" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--orchestrator-job-class="):
            return arg.split("=", 1)[1]
    if not argv:
        return None
    mode = argv[0].strip().lower()
    if mode in {"ask", "chat", "prompt", "complete"}:
        return "routing_triage"
    if mode == "run":
        return "repo_coding"
    return None


async def _hermes_route(
    settings: Settings,
    text: str | None,
    job_class: str | None,
    argv_json: str | None,
    argv_b64: str | None = None,
    text_b64: str | None = None,
) -> dict[str, object]:
    if text_b64:
        text = base64.b64decode(text_b64.encode("ascii")).decode("utf-8")
    if not text:
        raise ValueError("hermes-route requires --text or --text-b64")
    argv: list[str] = []
    if argv_b64:
        argv_json = base64.b64decode(argv_b64.encode("ascii")).decode("utf-8")
    if argv_json:
        parsed = json.loads(argv_json)
        if isinstance(parsed, list):
            argv = [str(item) for item in parsed]
    if job_class is None:
        job_class = _infer_hermes_job_class(argv)
    return await route_for_hermes_prompt(settings, text=text, job_class=job_class, argv=argv)


async def _hook_gate(settings: Settings) -> dict[str, object]:
    raw = sys.stdin.read()
    event = json.loads(raw) if raw.strip() else {}
    return await run_hook_event(event, settings=settings)


async def _skill_hook_receipts(settings: Settings, limit: int = 20) -> dict[str, object]:
    store = StateStore(settings)
    await store.initialize()
    return {"state": "produced", "receipts": await store.list_skill_hook_receipts(limit=limit)}


def _skill_hook_plan_payload(plan: SkillHookPlan) -> dict[str, object]:
    payload = plan.receipt_payload()
    prompt_display = prompt_for_context(plan.prompt)
    payload["prompt"] = prompt_display
    payload["prompt_chars"] = len(plan.prompt)
    payload["prompt_lines"] = plan.prompt.count("\n") + 1 if plan.prompt else 0
    payload["prompt_omitted"] = prompt_display != plan.prompt
    return payload


async def _skill_hook_plan(settings: Settings) -> dict[str, object]:
    plan_dir = settings.home / "skill-hooks" / "plans"
    if not plan_dir.exists():
        return {"state": "produced", "plan_path": None, "skill_hook_plan": None}
    paths = sorted(plan_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            plan = SkillHookPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        return {"state": "produced", "plan_path": str(path), "skill_hook_plan": _skill_hook_plan_payload(plan)}
    return {"state": "produced", "plan_path": None, "skill_hook_plan": None}


def main() -> None:
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument("--home", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh")
    model_lifecycle = sub.add_parser("model-lifecycle")
    model_lifecycle.add_argument("--fix", action="store_true", help="Auto-fix Hermes config when vision model is retired")
    sub.add_parser("status")
    sub.add_parser("spec-status")
    dashboard_status = sub.add_parser("dashboard-status")
    dashboard_status.add_argument("--host", default=os.getenv("ORCHESTRATOR_HOST", "127.0.0.1"))
    dashboard_status.add_argument("--port", type=int, default=int(os.getenv("ORCHESTRATOR_PORT", "8765")))
    dashboard_status.add_argument("--no-receipt", action="store_true")
    sub.add_parser("owner-receipt")
    sub.add_parser("acceptance-battery")
    token_flow = sub.add_parser("token-flow")
    token_flow.add_argument("--limit", type=int, default=50)
    token_backfill = sub.add_parser("token-backfill")
    token_backfill.add_argument("--limit", type=int, default=10000)
    skill_route = sub.add_parser("skill-route")
    skill_route.add_argument("--text", required=True)
    skill_route.add_argument("--cwd", default=None)
    hermes_route = sub.add_parser("hermes-route")
    hermes_route.add_argument("--text", default=None)
    hermes_route.add_argument("--text-b64", default=None)
    hermes_route.add_argument("--job-class", default=None)
    hermes_route.add_argument("--argv-json", default=None)
    hermes_route.add_argument("--argv-b64", default=None)
    sub.add_parser("hook-gate")
    skill_hook_receipts = sub.add_parser("skill-hook-receipts")
    skill_hook_receipts.add_argument("--last", type=int, default=20)
    sub.add_parser("skill-hook-plan")
    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("text")
    dispatch.add_argument("--full", action="store_true", help="Print the full dispatch payload instead of a compact receipt summary")
    prove_adapter = sub.add_parser("prove-adapter")
    prove_adapter.add_argument("adapter_name")
    prove_adapter.add_argument("--prompt", default="Adapter proof: answer with OK.")
    prove_adapter.add_argument("--capability", default=None)
    prove_adapter.add_argument("--allow-subscription", action="store_true")
    add_selection = sub.add_parser("add-selection")
    add_selection.add_argument("path")
    add_selection.add_argument("--kind", default="file", choices=["file", "folder", "url", "text"])
    add_selection.add_argument("--source", default="cli")
    approve = sub.add_parser("approve")
    approve.add_argument("intent_id")
    reject = sub.add_parser("reject")
    reject.add_argument("intent_id")
    sub.add_parser("repair-services")
    sub.add_parser("retry-repairs")
    notify = sub.add_parser("notify-once")
    notify.add_argument("--include-tray", action="store_true")
    sub.add_parser("benchmark-latency")
    autopilot = sub.add_parser("autopilot-scan")
    autopilot.add_argument("roots", nargs="+")
    standing = sub.add_parser("add-standing-order")
    standing.add_argument("folder")
    standing.add_argument("--policy", default="autopilot: disabled")
    standing.add_argument("--enabled", action="store_true")
    sub.add_parser("run-scheduler-once")
    sub.add_parser("migrate-legacy-scheduler")
    scheduler_task = sub.add_parser("add-scheduler-task")
    scheduler_task.add_argument("folder")
    scheduler_task.add_argument("--policy", default="autopilot: enabled")
    scheduler_task.add_argument("--enabled", action="store_true")
    scheduler_task.add_argument("--interval-seconds", type=int, default=0)
    scheduler_task.add_argument("--name", default=None)
    scheduler_task.add_argument("--task-id", default=None)
    pause_task = sub.add_parser("pause-scheduler-task")
    pause_task.add_argument("task_id")
    resume_task = sub.add_parser("resume-scheduler-task")
    resume_task.add_argument("task_id")
    activate_task = sub.add_parser("activate-scheduler-task")
    activate_task.add_argument("task_id")
    activate_task.add_argument("--note", default="Owner approved activation.")
    review_task = sub.add_parser("review-scheduler-task")
    review_task.add_argument("task_id")
    review_task.add_argument("--state", required=True, choices=["needs_review", "owner_approved", "rejected"])
    review_task.add_argument("--note", default="")
    trigger_task = sub.add_parser("trigger-scheduler-task")
    trigger_task.add_argument("task_id")

    # Governance CLI commands (Phase 2)
    register_governance_cli(sub)

    # Budget CLI commands (Phase 3)
    register_budget_cli(sub)

    # Capacity-aware delegated work controls
    register_delegation_cli(sub)

    args = parser.parse_args()

    settings = Settings.load()
    if args.home:
        home = Path(args.home)
        settings = Settings(home=home, state_path=home / "state.sqlite", notifications_path=home / "notifications.jsonl", log_dir=home / "logs", repo_root=settings.repo_root)

    registered_func = getattr(args, "func", None)
    if registered_func is not None:
        args.settings = settings
        registered_result = asyncio.run(registered_func(args))
        if registered_result is not None:
            print(json.dumps(registered_result, indent=2, default=str))
        return

    result: dict[str, Any]
    if args.cmd == "refresh":
        result = asyncio.run(_refresh(settings))
    elif args.cmd == "model-lifecycle":
        result = asyncio.run(probe_model_lifecycle(settings, fix_config=args.fix))
    elif args.cmd == "spec-status":
        result = asyncio.run(_spec_status(settings))
    elif args.cmd == "dashboard-status":
        result = _dashboard_status(settings, args.host, args.port, not args.no_receipt)
    elif args.cmd == "owner-receipt":
        result = asyncio.run(_owner_receipt(settings))
    elif args.cmd == "acceptance-battery":
        result = asyncio.run(_acceptance_battery(settings))
    elif args.cmd == "token-flow":
        result = asyncio.run(_token_flow(settings, args.limit))
    elif args.cmd == "token-backfill":
        result = asyncio.run(_token_backfill(settings, args.limit))
    elif args.cmd == "skill-route":
        result = asyncio.run(_skill_route(settings, args.text, args.cwd))
    elif args.cmd == "hermes-route":
        result = asyncio.run(_hermes_route(settings, args.text, args.job_class, args.argv_json, args.argv_b64, args.text_b64))
    elif args.cmd == "hook-gate":
        result = asyncio.run(_hook_gate(settings))
    elif args.cmd == "skill-hook-receipts":
        result = asyncio.run(_skill_hook_receipts(settings, args.last))
    elif args.cmd == "skill-hook-plan":
        result = asyncio.run(_skill_hook_plan(settings))
    elif args.cmd == "dispatch":
        result = asyncio.run(_dispatch(settings, args.text, args.full))
    elif args.cmd == "prove-adapter":
        result = asyncio.run(_prove_adapter(settings, args.adapter_name, args.prompt, args.capability, args.allow_subscription))
    elif args.cmd == "add-selection":
        result = asyncio.run(_add_selection(settings, args.path, args.kind, args.source))
    elif args.cmd == "approve":
        result = asyncio.run(_approval(settings, args.intent_id, True))
    elif args.cmd == "reject":
        result = asyncio.run(_approval(settings, args.intent_id, False))
    elif args.cmd == "repair-services":
        result = asyncio.run(_repair_services(settings))
    elif args.cmd == "retry-repairs":
        result = asyncio.run(_retry_repairs(settings))
    elif args.cmd == "notify-once":
        result = asyncio.run(_notify_once(settings, args.include_tray))
    elif args.cmd == "benchmark-latency":
        result = asyncio.run(_benchmark_latency(settings))
    elif args.cmd == "autopilot-scan":
        result = asyncio.run(_autopilot_scan(settings, args.roots))
    elif args.cmd == "add-standing-order":
        result = asyncio.run(_add_standing_order(settings, args.folder, args.policy, args.enabled))
    elif args.cmd == "run-scheduler-once":
        result = asyncio.run(_run_scheduler_once(settings))
    elif args.cmd == "migrate-legacy-scheduler":
        result = asyncio.run(_migrate_legacy_scheduler(settings))
    elif args.cmd == "add-scheduler-task":
        result = asyncio.run(_add_scheduler_task(settings, args.folder, args.policy, args.enabled, args.interval_seconds, args.name, args.task_id))
    elif args.cmd == "pause-scheduler-task":
        result = asyncio.run(_set_scheduler_task_enabled(settings, args.task_id, False))
    elif args.cmd == "resume-scheduler-task":
        result = asyncio.run(_set_scheduler_task_enabled(settings, args.task_id, True))
    elif args.cmd == "activate-scheduler-task":
        result = asyncio.run(_activate_scheduler_task(settings, args.task_id, args.note))
    elif args.cmd == "review-scheduler-task":
        result = asyncio.run(_review_scheduler_task(settings, args.task_id, args.state, args.note))
    elif args.cmd == "trigger-scheduler-task":
        result = asyncio.run(_trigger_scheduler_task(settings, args.task_id))
    else:
        result = asyncio.run(_status(settings))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
