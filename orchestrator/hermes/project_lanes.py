"""Stable per-project starting lanes layered over the ranked failover ladder."""

from __future__ import annotations

import hashlib
from typing import Any

from orchestrator.hermes.capacity_lanes import hermes_lane_for_selected


def _provider(row: dict[str, Any]) -> str:
    return str(row.get("provider_id") or row.get("provider") or row.get("surface") or "").strip().lower()


def _eligible(row: dict[str, Any]) -> bool:
    """Keep stable assignment inside the executable capacity boundary."""

    return hermes_lane_for_selected(row).get("lane_state") in {"supported", "dispatch_only"}


def assign_project_lane(project_id: str, providers: list[str]) -> dict[str, Any]:
    """Assign a stable provider family while keeping the ranked ladder as fallback."""

    normalized = sorted({str(provider).strip().lower() for provider in providers if str(provider).strip()})
    if not normalized:
        return {
            "project_id": project_id,
            "assigned_provider": None,
            "available_providers": [],
            "assignment_key": "",
        }
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    index = int(digest[:16], 16) % len(normalized)
    return {
        "project_id": project_id,
        "assigned_provider": normalized[index],
        "available_providers": normalized,
        "assignment_key": digest[:16],
    }


def select_project_worker(project_id: str | None, ladder: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Prefer the stable project provider when present, otherwise keep ranking."""

    eligible_ladder = [row for row in ladder if _eligible(row)]
    first = eligible_ladder[0] if eligible_ladder else None
    if not project_id:
        return first, {
            "project_id": None,
            "assigned_provider": None,
            "available_providers": sorted({_provider(row) for row in eligible_ladder if _provider(row)}),
            "assignment_key": "",
            "selection_reason": "no_project_id",
        }
    providers = [_provider(row) for row in eligible_ladder if _provider(row)]
    assignment = assign_project_lane(project_id, providers)
    assigned = assignment["assigned_provider"]
    for row in eligible_ladder:
        if _provider(row) == assigned:
            return row, {**assignment, "selection_reason": "assigned_provider"}
    if first is not None:
        return first, {**assignment, "selection_reason": "fallback_to_ranked_ladder"}
    return None, {**assignment, "selection_reason": "no_eligible_worker"}
