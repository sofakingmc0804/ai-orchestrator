"""Budget probe CLI command."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.discovery.budget_probes import run_all_probes, probe_to_dict
from orchestrator.discovery.subscription_usage import build_api_budget_payload, build_subscription_usage_payload
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
    """Run legacy API budget probes manually and store results."""
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
    """Show split subscription/API budget state from DB (no probe)."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()

    subscriptions = await build_subscription_usage_payload(store, refresh=False)
    api_budgets = await build_api_budget_payload(store)

    print(f"\n{'='*96}")
    print("Subscription Usage - account/profile scoped")
    print(f"{'='*96}")
    print(f"{'Service':<24} {'Account':<24} {'App tokens':>12} {'Windows':>8} {'Current/left':>20} {'Proof':<24}")
    print(f"{'='*96}")
    rows = subscriptions.get("subscriptions") or []
    if not rows:
        print("No subscription snapshots in DB. Run 'orchestrator cli refresh-subscriptions' first.")
    for row in rows:
        print(
            f"{str(row.get('service_id') or ''):<24} "
            f"{str(row.get('account_id') or '')[:24]:<24} "
            f"{int(row.get('tokens_used_by_app') or 0):>12,} "
            f"{len(_windows(row)):>8} "
            f"{_fmt_window_summary(row):>20} "
            f"{str(row.get('confidence') or 'unproved'):<24}"
        )
    print(f"{'='*96}")
    print("API budgets are policy caps. No cap means uncapped API access for orchestrator policy purposes.")
    print(f"Policy rows: {len(api_budgets.get('policies') or [])} | Legacy probes retained: {len(api_budgets.get('legacy_probes') or [])}")
    print(f"{'='*96}\n")

    await store.close()


async def cmd_refresh_subscriptions(args: argparse.Namespace) -> None:
    """Refresh subscription usage through local CLI account surfaces."""
    settings = settings_from_args(args)
    store = StateStore(settings)
    await store.initialize()
    payload = await build_subscription_usage_payload(store, refresh=True)
    print(f"\n{'='*96}")
    print(f"Subscription Refresh - {utc_now()}")
    print(f"{'='*96}")
    print(f"{'Service':<24} {'Account':<24} {'App tokens':>12} {'Windows':>8} {'Current/left':>20} {'Proof':<24}")
    print(f"{'='*96}")
    for row in payload.get("subscriptions") or []:
        print(
            f"{str(row.get('service_id') or ''):<24} "
            f"{str(row.get('account_id') or '')[:24]:<24} "
            f"{int(row.get('tokens_used_by_app') or 0):>12,} "
            f"{len(_windows(row)):>8} "
            f"{_fmt_window_summary(row):>20} "
            f"{str(row.get('confidence') or 'unproved'):<24}"
        )
        if row.get("error"):
            print(f"  {row.get('error')}")
    refresh = payload.get("refresh") or {}
    print(f"{'='*96}")
    print(f"Stored: {refresh.get('stored', 0)} | Unproved: {refresh.get('failed', 0)} | Total: {refresh.get('total', 0)}")
    print(f"{'='*96}\n")
    await store.close()


def _fmt_int(value: Any) -> str:
    if value is None:
        return "unproved"


def _windows(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("usage_windows_json") or "[]"
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _fmt_window_summary(row: dict[str, Any]) -> str:
    windows = _windows(row)
    if windows:
        first = windows[0]
        if first.get("current") is not None:
            return f"{first.get('current')} {str(first.get('unit') or 'units')[:8]}"
        if first.get("remaining_percent") is not None:
            return f"{float(first['remaining_percent']):.1f}% left"
    if row.get("tokens_remaining") is not None:
        return f"{_fmt_int(row.get('tokens_remaining'))} tokens"
    return str(row.get("status") or "unproved")[:20]
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "unproved"


def register_budget_cli(subparsers: argparse._SubParsersAction) -> None:
    """Register budget CLI commands."""

    # refresh-budget command
    p_refresh = subparsers.add_parser("refresh-budget", help="Run budget probes and refresh quota state")
    p_refresh.set_defaults(func=cmd_refresh_budget)

    # budget-status command (alias for 'budget' from governance.py)
    p_status = subparsers.add_parser("budget-status", help="Show current budget state from DB")
    p_status.set_defaults(func=cmd_budget_status)

    p_refresh_subscriptions = subparsers.add_parser("refresh-subscriptions", help="Refresh subscription usage from logged-in CLIs")
    p_refresh_subscriptions.set_defaults(func=cmd_refresh_subscriptions)
