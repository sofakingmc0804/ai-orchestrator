#!/usr/bin/env python3
"""CLI commands for governance module."""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.routing.brain import route_brain
from orchestrator.state.store import StateStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def settings_from_args(args: argparse.Namespace) -> Settings:
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


async def cmd_workers(args: argparse.Namespace) -> None:
    """List or show workers."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()

    if args.show:
        # Show single worker
        row = await store.db.fetchrow("SELECT * FROM worker_cards WHERE worker_id = ?", args.show)
        if not row:
            print(f"Worker not found: {args.show}")
            return

        print(f"\n{'='*60}")
        print(f"Worker: {row['worker_id']}")
        print(f"{'='*60}")
        print(f"Base Model:     {row['base_model']}")
        print(f"Surface:        {row['surface']}")
        print(f"Provider:       {row['provider_id']}")
        print(f"Contract:       {row['contract_type']}")
        print(f"Context:        {row['context_window']:,} tokens")
        print(f"Hardware Fit:   {row['hardware_fit']}")
        print(f"Approval Req:   {'Yes' if row['approval_required'] else 'No'}")
        print(f"Last Verified:  {row['last_verified']}")

        stats = json.loads(row['stats_json'] or '{}')
        if stats:
            print(f"\nStats:")
            for k, v in sorted(stats.items()):
                print(f"  {k}: {v}")

        best_jobs = json.loads(row['best_jobs_json'] or '[]')
        if best_jobs:
            print(f"\nBest Jobs: {', '.join(best_jobs)}")

        avoid_jobs = json.loads(row['avoid_jobs_json'] or '[]')
        if avoid_jobs:
            print(f"Avoid Jobs: {', '.join(avoid_jobs)}")

        print(f"{'='*60}\n")
    else:
        # List workers
        query = "SELECT * FROM worker_cards"
        params: list[Any] = []

        if args.job_class:
            # Filter workers suitable for job class
            job = await store.db.fetchrow("SELECT * FROM job_classes WHERE job_class = ?", args.job_class)
            if not job:
                print(f"Job class not found: {args.job_class}")
                return

            required_caps = json.loads(job['required_capabilities_json'])
            query += " WHERE "
            # Simple filter: workers with capabilities matching job requirements
            # (Full implementation would do proper matching)
            print(f"Filtering workers for job class: {args.job_class}")

        if args.limit:
            query += f" LIMIT {args.limit}"

        rows = await store.db.fetch(query, *params)

        print(f"\n{'='*100}")
        print(f"{'Worker ID':<40} {'Base Model':<25} {'Surface':<20} {'Contract':<20}")
        print(f"{'='*100}")

        for row in rows:
            print(f"{row['worker_id']:<40} {row['base_model']:<25} {row['surface']:<20} {row['contract_type']:<20}")

        print(f"{'='*100}")
        print(f"Total: {len(rows)} workers\n")

    await store.close()


async def cmd_budget(args: argparse.Namespace) -> None:
    """Show budget/quota state."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()

    rows = await store.db.fetch("SELECT * FROM budget_probes ORDER BY provider_id, probe_type")

    if not rows:
        print("\nNo budget probes found. Run refresh-workers first.\n")
        await store.close()
        return

    print(f"\n{'='*100}")
    print(f"{'Provider':<25} {'Type':<20} {'Remaining':>12} {'Limit':>10} {'% Left':>8} {'Reset At':<25} {'Status':<10}")
    print(f"{'='*100}")

    for row in rows:
        remaining = row['remaining'] or 0
        limit = row['limit'] or 0
        pct = (remaining / limit * 100) if limit > 0 else 0

        # Status color coding (text-based)
        if pct > 50:
            status = "OK"
        elif pct > 20:
            status = "LOW"
        else:
            status = "CRIT"

        print(f"{row['provider_id']:<25} {row['probe_type']:<20} {remaining:>12,} {limit:>10,} {pct:>7.1f}% {row['reset_at'] or 'N/A':<25} {status:<10}")

    print(f"{'='*100}\n")

    await store.close()


async def cmd_receipts(args: argparse.Namespace) -> None:
    """Show receipt history."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()

    query = """
        SELECT r.*, i.raw_text, i.parsed_payload
        FROM receipts r
        LEFT JOIN intents i ON r.dispatch_id = i.id
        ORDER BY r.dispatch_id DESC
        LIMIT ?
    """
    params: list[Any] = [args.last or 20]

    if args.job_class:
        # Filter by job class (requires job_class column in receipts - added in Phase 4)
        print(f"Note: job_class filtering requires Phase 4 receipt schema upgrade")

    rows = await store.db.fetch(query, *params)

    if not rows:
        print("\nNo receipts found.\n")
        await store.close()
        return

    print(f"\n{'='*100}")
    print(f"{'Dispatch ID':<35} {'Service':<15} {'Model':<20} {'Tokens':>12} {'Success':<10}")
    print(f"{'='*100}")

    for row in rows:
        tokens = (row['tokens_in'] or 0) + (row['tokens_out'] or 0)
        success = "yes" if row['success'] else "no"
        print(f"{row['dispatch_id']:<35} {row['service'] or 'N/A':<15} {row['model'] or 'N/A':<20} {tokens:>12,} {success:<10}")

    print(f"{'='*100}")
    print(f"Total: {len(rows)} receipts\n")

    await store.close()


async def cmd_route(args: argparse.Namespace) -> None:
    """Route a job to best worker (dry-run or execute)."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()

    payload = await route_brain(store, text=args.text, job_class=args.job_class)
    if getattr(args, "execute", False) and payload.get("state") == "produced":
        dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
        result = await dispatcher.dispatch_text(args.text, job_class_override=args.job_class)
        payload["execute_result"] = result.model_dump(mode="json")
    print(json.dumps(payload, indent=2))
    await store.close()


def register_governance_cli(subparsers: argparse._SubParsersAction) -> None:
    """Register governance CLI commands."""

    # workers command
    p_workers = subparsers.add_parser("workers", help="List or show workers")
    p_workers.add_argument("--limit", type=int, default=20, help="Max workers to list")
    p_workers.add_argument("--job-class", help="Filter by job class")
    p_workers.add_argument("--show", metavar="WORKER_ID", help="Show single worker details")
    p_workers.set_defaults(func=cmd_workers)

    # budget command
    p_budget = subparsers.add_parser("budget", help="Show budget/quota state")
    p_budget.set_defaults(func=cmd_budget)

    # receipts command
    p_receipts = subparsers.add_parser("receipts", help="Show receipt history")
    p_receipts.add_argument("--last", type=int, default=20, help="Last N receipts")
    p_receipts.add_argument("--job-class", help="Filter by job class (Phase 4)")
    p_receipts.set_defaults(func=cmd_receipts)

    # route command
    p_route = subparsers.add_parser("route", help="Route job to best worker")
    p_route.add_argument("--job-class", required=True, help="Job class to route")
    p_route.add_argument("--text", required=True, help="Intent text")
    p_route.add_argument("--dry-run", action="store_true", help="Show decision without executing")
    p_route.add_argument("--execute", action="store_true", dest="execute", help="Execute dispatch (Phase 2)")
    p_route.add_argument("--approve", action="store_true", help="Allow workers requiring approval")
    p_route.set_defaults(func=cmd_route)
