"""Budget probe scheduler — runs probes every 15 minutes.

Integrates with the orchestrator scheduler to refresh budget state.
Results stored in `budget_probes` table for routing decisions.

Author: Hermes Agent
Date: 2026-06-10
Phase: 3 (Budget Probes)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from orchestrator.discovery.budget_probes import run_all_probes, probe_to_dict

if TYPE_CHECKING:
    from orchestrator.state.store import StateStore


async def run_budget_probes(store: "StateStore") -> dict:
    """Run all budget probes and store results.

    Args:
        store: State store for persisting probe results

    Returns:
        Dict with probe summary
    """
    results = run_all_probes()

    # Store each probe result
    stored = 0
    failed = 0

    for result in results:
        probe_data = probe_to_dict(result)

        # Upsert into budget_probes table
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

    return {
        "probed_at": results[0].probed_at if results else None,
        "stored": stored,
        "failed": failed,
        "total": len(results),
        "providers": [r.provider_id for r in results],
    }


async def run_budget_probes_once(store: "StateStore") -> dict:
    """Run budget probes once (for CLI/manual trigger).

    Args:
        store: State store

    Returns:
        Probe results summary
    """
    return await run_budget_probes(store)


def register_budget_probe_scheduler(scheduler_tasks: list) -> list:
    """Register budget probe task with scheduler.

    Args:
        scheduler_tasks: Existing scheduler task list

    Returns:
        Updated task list with budget probe task
    """
    from orchestrator.scheduler.tasks import SchedulerTask

    # Add 15-minute budget probe task
    budget_probe_task = {
        "name": "budget-probe-refresh",
        "task_type": "budget_probe",
        "schedule_kind": "interval",
        "interval_seconds": 900,  # 15 minutes
        "enabled": True,
        "payload": {},
    }

    return scheduler_tasks + [budget_probe_task]
