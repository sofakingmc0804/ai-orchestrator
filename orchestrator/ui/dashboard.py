from __future__ import annotations

import json
from typing import Any

from orchestrator.state.store import StateStore


LADDER_ORDER = ["health", "live_quota", "measured_quality", "marginal_cost", "flat_rate_floor"]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def build_quality_leaderboard_payload(store: StateStore, limit: int = 2000) -> dict[str, Any]:
    rows = await store.list_operation_quality_scores(limit=limit)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("proof_kind") or "") != "live":
            continue
        domain = str(row.get("operation_domain") or "unknown")
        worker_id = str(row.get("worker_id") or "")
        if not worker_id:
            continue
        board = grouped.setdefault(domain, {})
        item = board.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "operation_domain": domain,
                "samples": 0,
                "score_total": 0.0,
                "latest_dispatch_id": row.get("dispatch_id"),
                "latest_created_at": row.get("created_at"),
            },
        )
        item["samples"] += 1
        item["score_total"] += float(row.get("composite_score") or 0.0)
        if str(row.get("created_at") or "") > str(item.get("latest_created_at") or ""):
            item["latest_dispatch_id"] = row.get("dispatch_id")
            item["latest_created_at"] = row.get("created_at")

    leaderboards: list[dict[str, Any]] = []
    for domain, board in grouped.items():
        workers: list[dict[str, Any]] = []
        for item in board.values():
            samples = max(int(item["samples"]), 1)
            item["avg_quality_score"] = round(float(item.pop("score_total")) / samples, 4)
            workers.append(item)
        workers.sort(key=lambda item: (-float(item["avg_quality_score"]), str(item["worker_id"])))
        leaderboards.append({"operation_domain": domain, "workers": workers})
    leaderboards.sort(key=lambda item: str(item["operation_domain"]))
    return {
        "state": "produced",
        "proof_kind": "live",
        "proof_table": "operation_quality_scores",
        "leaderboards": leaderboards,
    }


async def build_failover_events_payload(store: StateStore, limit: int = 50) -> dict[str, Any]:
    attempts = await store.list_dispatch_attempts(limit=max(limit * 5, 50))
    by_dispatch: dict[str, list[dict[str, Any]]] = {}
    for row in attempts:
        dispatch_id = str(row.get("dispatch_id") or "")
        if not dispatch_id:
            continue
        item = dict(row)
        item["detail"] = _json_dict(item.get("detail"))
        by_dispatch.setdefault(dispatch_id, []).append(item)

    events: list[dict[str, Any]] = []
    latest_ladder: list[dict[str, Any]] = []
    for dispatch_id, dispatch_attempts in by_dispatch.items():
        ordered = sorted(dispatch_attempts, key=lambda row: int(row.get("attempt_number") or 0))
        failed = [row for row in ordered if str(row.get("state") or "") == "failed"]
        if not failed and len({str(row.get("adapter_name") or "") for row in ordered}) <= 1:
            continue
        final = ordered[-1]
        event = {
            "dispatch_id": dispatch_id,
            "intent_id": final.get("intent_id"),
            "attempts": len(ordered),
            "failed_attempts": len(failed),
            "adapters": [str(row.get("adapter_name") or "") for row in ordered if row.get("adapter_name")],
            "final_state": final.get("state"),
            "last_error": (failed[-1].get("error") if failed else None),
            "last_seen_at": final.get("completed_at") or final.get("started_at"),
        }
        events.append(event)
        if not latest_ladder:
            latest_ladder = [
                {
                    "attempt_number": int(row.get("attempt_number") or 0),
                    "adapter_name": row.get("adapter_name"),
                    "state": row.get("state"),
                    "error": row.get("error"),
                    "repair_action": (row.get("detail") or {}).get("repair_action"),
                    "started_at": row.get("started_at"),
                    "completed_at": row.get("completed_at"),
                }
                for row in ordered
            ]

    events.sort(key=lambda row: str(row.get("last_seen_at") or ""), reverse=True)
    return {
        "state": "produced",
        "proof_kind": "live",
        "proof_table": "dispatch_attempts",
        "ladder_order": LADDER_ORDER,
        "latest_attempt_ladder": latest_ladder,
        "recent_failover_events": events[:limit],
    }


async def build_governance_receipts_payload(store: StateStore, limit: int = 25) -> dict[str, Any]:
    receipts = await store.list_skill_hook_receipts(limit=limit)
    by_decision: dict[str, int] = {}
    terminal_states: dict[str, int] = {}
    for receipt in receipts:
        decision = str(receipt.get("decision") or "unknown")
        by_decision[decision] = by_decision.get(decision, 0) + 1
        terminal = str(receipt.get("terminal_state_requirement") or "unspecified")
        terminal_states[terminal] = terminal_states.get(terminal, 0) + 1
    return {
        "state": "produced",
        "proof_kind": "live",
        "proof_table": "skill_hook_receipts",
        "summary": {
            "receipts": len(receipts),
            "by_decision": by_decision,
            "terminal_states": terminal_states,
        },
        "receipts": receipts,
    }
