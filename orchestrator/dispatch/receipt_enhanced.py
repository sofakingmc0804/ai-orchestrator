"""Receipt system enhancements for Phase 4.

Extends receipt recording to include worker_id, job_class, routing_reasoning,
and budget_state_json for complete audit trail.

Author: Hermes Agent
Date: 2026-06-10
Phase: 4 (Receipt System)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_receipt_data(
    dispatch_id: str,
    adapter_name: str,
    intent: Any,
    result: dict[str, Any],
    decision: Any,
    job_class: str,
    budget_probes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build receipt data with Phase 4 fields.

    Args:
        dispatch_id: Dispatch identifier
        adapter_name: Selected adapter/worker
        intent: Intent object
        result: Adapter result dict
        decision: RoutingDecision object
        job_class: Classified job class
        budget_probes: Current budget probe state

    Returns:
        Receipt dict ready for DB insertion
    """
    # Extract worker_id from chosen candidate
    chosen_candidate: dict[str, Any] = next(
        (c for c in (decision.candidates_considered or []) if str(c.get("adapter_name")) == adapter_name),
        {}
    )
    worker_id = chosen_candidate.get("worker_id") or chosen_candidate.get("adapter_name")

    # Build budget state summary
    budget_state = {}
    if budget_probes:
        for probe in budget_probes:
            budget_state[probe['provider_id']] = {
                'remaining': probe['remaining'],
                'limit': probe['limit'],
                'ok': probe['ok'],
            }

    # Calculate tokens
    tokens_in = int(result.get("usage", {}).get("prompt_tokens") or result.get("tokens_in") or 0)
    tokens_out = int(result.get("usage", {}).get("completion_tokens") or result.get("tokens_out") or 0)

    # Build receipt
    receipt = {
        "dispatch_id": dispatch_id,
        "service": adapter_name,
        "capability": intent.parsed_payload.get("required_capability"),
        "model": result.get("model"),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_class": chosen_candidate.get("billing_class"),
        "success": 1 if result.get("ok") else 0,
        "output_summary": (result.get("text") or "")[:500],  # First 500 chars
        "full_receipt": json.dumps(result, default=str),
        # Phase 4 fields
        "worker_id": worker_id,
        "job_class": job_class,
        "routing_reasoning": decision.reasoning,
        "budget_state_json": json.dumps(budget_state),
        "created_at": utc_now(),
    }

    return receipt
