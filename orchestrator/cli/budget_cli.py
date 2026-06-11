"""Budget probe CLI command."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.discovery.budget_probes import run_all_probes, probe_to_dict
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


async def cmd_refresh_budget(args: argparse.Namespace) -> None:
    """Run budget probes manually and store results."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()

    # Run probes
    results = run_all_probes()

    # Store results
    stored = 0
    failed = 0

    for result in results:
        probe_data = probe_to_dict(result)

        await store.db.execute("""
            INSERT INTO budget_probes (
                id, provider_id, probe_type, remaining, "limit",
                reset_at, probed_at, ok, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                remaining = excluded.remaining,
                "limit" = excluded."limit",
                reset_at = excluded.reset_at,
                probed_at = excluded.probed_at,
                ok = excluded.ok,
                error = excluded.error
        """, (
            probe_data["id"],
            probe_data["provider_id"],
            probe_data["probe_type"],
            probe_data["remaining"],
            probe_data["limit"],
            probe_data["reset_at"],
            probe_data["probed_at"],
            probe_data["ok"],
            probe_data["error"],
        ))

        if result.ok:
            stored += 1
        else:
            failed += 1

    await store.db.commit()
    await store.close()

    # Print summary
    print(f"\n{'='*80}")
    print(f"Budget Probe Refresh - {utc_now()}")
    print(f"{'='*80}")
    print(f"{'Provider':<20} {'Status':<10} {'Remaining':>12} {'Limit':>10} {'% Left':>8}")
    print(f"{'='*80}")

    for result in results:
        status = "OK" if result.ok else "FAIL"
        remaining = result.remaining if result.remaining is not None else 0
        limit = result.limit if result.limit is not None else 0
        pct = (remaining / limit * 100) if limit > 0 else 0

        print(f"{result.provider_id:<20} {status:<10} {remaining:>12,} {limit:>10,} {pct:>7.1f}%")

        if result.error:
            print(f"  Error: {result.error}")

    print(f"{'='*80}")
    print(f"Stored: {stored} | Failed: {failed} | Total: {len(results)}")
    print(f"{'='*80}\n")


async def cmd_budget_status(args: argparse.Namespace) -> None:
    """Show current budget state from DB (no probe)."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()

    rows = await store.db.fetch("SELECT * FROM budget_probes ORDER BY provider_id, probe_type")

    if not rows:
        print("\nNo budget probes in DB. Run 'orchestrator cli refresh-budget' first.\n")
        await store.close()
        return

    print(f"\n{'='*80}")
    print(f"Budget State (from DB) - Last probed: {rows[0]['probed_at']}")
    print(f"{'='*80}")
    print(f"{'Provider':<20} {'Type':<20} {'Remaining':>12} {'Limit':>10} {'% Left':>8} {'Status':<10}")
    print(f"{'='*80}")

    for row in rows:
        remaining = row['remaining'] or 0
        limit = row['limit'] or 0
        pct = (remaining / limit * 100) if limit > 0 else 0

        if pct > 50:
            status = "OK"
        elif pct > 20:
            status = "LOW"
        else:
            status = "CRIT"

        print(f"{row['provider_id']:<20} {row['probe_type']:<20} {remaining:>12,} {limit:>10,} {pct:>7.1f}% {status:<10}")

    print(f"{'='*80}\n")

    await store.close()


def register_budget_cli(subparsers: argparse._SubParsersAction) -> None:
    """Register budget CLI commands."""

    # refresh-budget command
    p_refresh = subparsers.add_parser("refresh-budget", help="Run budget probes and refresh quota state")
    p_refresh.set_defaults(func=cmd_refresh_budget)

    # budget-status command (alias for 'budget' from governance.py)
    p_status = subparsers.add_parser("budget-status", help="Show current budget state from DB")
    p_status.set_defaults(func=cmd_budget_status)
