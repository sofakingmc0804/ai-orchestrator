from __future__ import annotations

from typing import Any

from orchestrator.state.store import StateStore


async def build_token_flow_payload(store: StateStore, limit: int = 50) -> dict[str, Any]:
    rows = await store.list_token_usage(limit=limit)
    summary = await store.token_usage_summary(limit=max(limit, 1000))
    totals = {
        "attempts": len(rows),
        "tokens_in": sum(int(row.get("tokens_in") or 0) for row in rows),
        "tokens_out": sum(int(row.get("tokens_out") or 0) for row in rows),
        "tokens_total": sum(int(row.get("tokens_total") or 0) for row in rows),
        "successes": sum(int(bool(row.get("success"))) for row in rows),
    }
    return {
        "state": "produced",
        "limit": limit,
        "totals": totals,
        "by_provider_model": summary,
        "attempts": rows,
    }
