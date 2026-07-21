from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from orchestrator.backlog.intake import intake_discovered_work
from orchestrator.config import Settings
from orchestrator.delegation.work_cycle import run_delegated_work_cycle
from orchestrator.state.store import StateStore


def _settings_from_args(args: argparse.Namespace) -> Settings:
    settings = getattr(args, "settings", None)
    if isinstance(settings, Settings):
        return settings
    settings = Settings.load()
    if getattr(args, "home", None):
        home = Path(args.home)
        return Settings(
            home=home,
            state_path=home / "state.sqlite",
            notifications_path=home / "notifications.jsonl",
            log_dir=home / "logs",
            repo_root=settings.repo_root,
        )
    return settings


def _operation_task(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--operation-task-json must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not payload:
        raise SystemExit("--operation-task-json must be a non-empty JSON object")
    return payload


async def cmd_work_submit(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()
    try:
        item = await store.enqueue_delegated_work_item(
            {
                "raw_text": args.text,
                "consumer": args.consumer,
                "job_class": args.job_class,
                "project_root": args.project_root,
                "mode": args.mode,
                "operation_task": _operation_task(args.operation_task_json),
                "min_quality_score": args.min_quality_score,
                "source": "owner_cli",
            }
        )
        return {"state": "produced", "work_item": item}
    finally:
        await store.close()


async def cmd_work_cycle(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()
    try:
        return await run_delegated_work_cycle(
            settings,
            store,
            limit=args.limit,
            min_surplus_percent=args.min_surplus_percent,
            surplus_window_seconds=args.surplus_window_seconds,
        )
    finally:
        await store.close()


async def cmd_work_status(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()
    try:
        items = await store.list_delegated_work_items(limit=args.last, newest_first=True)
        return {"state": "produced", "work_items": items, "count": len(items)}
    finally:
        await store.close()


async def cmd_work_retry(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()
    try:
        item = await store.get_delegated_work_item(args.work_item_id)
        if item is None:
            raise SystemExit(f"work item not found: {args.work_item_id}")
        if str(item.get("state")) not in {"failed", "held"}:
            raise SystemExit(f"work item {args.work_item_id} is not retryable from state {item.get('state')}")
        retried = await store.update_delegated_work_item(args.work_item_id, "queued", error=None)
        return {"state": "repaired", "work_item": retried}
    finally:
        await store.close()


async def cmd_work_discover(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()
    try:
        roots = [Path(value) for value in args.root] if args.root else None
        return await intake_discovered_work(settings, store, roots=roots, limit=args.limit)
    finally:
        await store.close()


def register_delegation_cli(subparsers: argparse._SubParsersAction) -> None:
    submit = subparsers.add_parser("work-submit", help="Queue a validated work item for specialist delegation")
    submit.add_argument("--text", required=True, help="Worker instruction with an explicit output contract")
    submit.add_argument("--consumer", required=True, help="Named consumer of the output")
    submit.add_argument("--job-class", required=True, help="Existing worker job class")
    submit.add_argument("--operation-task-json", required=True, help="Deterministic validator contract as JSON")
    submit.add_argument("--project-root", default=None)
    submit.add_argument("--mode", choices=["immediate", "discretionary"], default="immediate")
    submit.add_argument("--min-quality-score", type=float, default=1.0)
    submit.set_defaults(func=cmd_work_submit)

    cycle = subparsers.add_parser("work-cycle", help="Run queued delegated work through the existing router")
    cycle.add_argument("--limit", type=int, default=1)
    cycle.add_argument("--min-surplus-percent", type=float, default=70.0)
    cycle.add_argument("--surplus-window-seconds", type=int, default=7_200)
    cycle.set_defaults(func=cmd_work_cycle)

    status = subparsers.add_parser("work-status", help="Show delegated work lifecycle state and runtime receipts")
    status.add_argument("--last", type=int, default=20)
    status.set_defaults(func=cmd_work_status)

    retry = subparsers.add_parser("work-retry", help="Requeue a held or failed work item after repairing its mechanism")
    retry.add_argument("work_item_id")
    retry.set_defaults(func=cmd_work_retry)

    discover = subparsers.add_parser("work-discover", help="Discover unfinished source-backed project work and queue a bounded intake")
    discover.add_argument("--root", action="append", help="Project root to scan; repeat to scan multiple roots")
    discover.add_argument("--limit", type=int, default=3, help="Maximum newly discovered items to promote")
    discover.set_defaults(func=cmd_work_discover)
