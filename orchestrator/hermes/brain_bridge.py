from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.routing.brain import route_brain
from orchestrator.state.store import StateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_path(settings: Settings, route_id: str) -> Path:
    safe_route = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in route_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return settings.home / "receipts" / f"hermes-brain-route-{stamp}-{safe_route}-{uuid.uuid4().hex[:8]}.json"


def _selected_worker(route_payload: dict[str, Any]) -> dict[str, Any] | None:
    ladder = route_payload.get("ranked_ladder") or []
    if not isinstance(ladder, list) or not ladder:
        return None
    first = ladder[0]
    return first if isinstance(first, dict) else None


def _hermes_provider_for_selected(selected: dict[str, Any] | None) -> str | None:
    if not selected:
        return None
    surface = str(selected.get("surface") or "").strip()
    provider = str(selected.get("provider_id") or selected.get("provider") or "").strip()
    key = provider or surface
    if key == "ollama-local" or surface in {"ollama-local", "ollama-http", "ollama-cli"}:
        return "custom"
    if key == "ollama-cloud" or surface == "ollama-cloud":
        return "ollama-cloud"
    return None


def _selected_payload(selected: dict[str, Any] | None) -> dict[str, Any]:
    if not selected:
        return {}
    return {
        "worker_id": selected.get("worker_id"),
        "adapter_name": selected.get("adapter_name"),
        "provider_id": selected.get("provider_id") or selected.get("provider"),
        "surface": selected.get("surface"),
        "contract_type": selected.get("contract_type"),
        "model_id": selected.get("model_id"),
        "hermes_provider": _hermes_provider_for_selected(selected),
    }


async def route_for_hermes_prompt(
    settings: Settings,
    *,
    text: str,
    job_class: str | None = None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    store = StateStore(settings)
    await store.initialize()
    await store.bootstrap_workspace_roots()
    await store.seed_default_mode_packs()
    route_payload = await route_brain(store, text=text, job_class=job_class)
    selected = _selected_payload(_selected_worker(route_payload))
    created_at = _utc_now()
    receipt = {
        "state": route_payload.get("state"),
        "terminal_state": route_payload.get("state"),
        "proof_kind": "live",
        "created_at": created_at,
        "source": "hermes-brain-bridge",
        "consumer": "Hermes shim before prompt execution",
        "workspace_id": "unclassified_legacy",
        "input": {
            "text_chars": len(text),
            "job_class": job_class,
            "argv": argv or [],
        },
        "selected": selected,
        "route": route_payload,
        "completion_test": "Hermes prompt path persisted this receipt before execution.",
    }
    path = _receipt_path(settings, str(route_payload.get("route_id") or "route"))
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt["receipt_path"] = str(path)
    path.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    await store.audit(
        "hermes-brain-bridge",
        "route",
        str(route_payload.get("route_id") or "unknown-route"),
        {
            "proof_kind": "live",
            "receipt_path": str(path),
            "state": route_payload.get("state"),
            "selected": selected,
        },
    )
    return receipt


async def prepare_workspace_hermes_turn(
    settings: Settings,
    *,
    workspace_id: str,
    mode_pack_id: str,
    skill_capability_checks: dict[str, bool],
    text: str,
    job_class: str | None,
    argv: list[str] | None,
    consumer: str,
) -> dict[str, Any]:
    """Create the governed envelope Hermes needs before it submits a turn.

    The function intentionally does not call a model.  It freezes the chosen
    mode and records the route first; Hermes reports real usage through
    ``complete_workspace_hermes_turn`` when the provider turn ends.
    """

    store = StateStore(settings)
    await store.initialize()
    await store.bootstrap_workspace_roots()
    await store.seed_default_mode_packs()
    workspace_session = await store.start_workspace_session(
        workspace_id=workspace_id,
        mode_pack_id=mode_pack_id,
        skill_capability_checks=skill_capability_checks,
    )
    work_packet = await store.create_work_packet(
        workspace_id=workspace_id,
        intent=text,
        payload={"argv": argv or [], "text": text},
        mode_pack_id=mode_pack_id,
        consumer=consumer,
        state="routing",
    )
    route_payload = await route_brain(store, text=text, job_class=job_class)
    selected = _selected_payload(_selected_worker(route_payload))
    route_id = str(route_payload.get("route_id") or work_packet["id"])
    receipt = {
        "state": route_payload.get("state"),
        "proof_kind": "live",
        "source": "hermes-workspace-bridge",
        "created_at": _utc_now(),
        "workspace_id": workspace_id,
        "workspace_session_id": workspace_session["id"],
        "work_packet_id": work_packet["id"],
        "mode_pack_id": mode_pack_id,
        "input": {"text_chars": len(text), "job_class": job_class, "argv": argv or []},
        "selected": selected,
        "route": route_payload,
        "completion_test": "Hermes must report usage against this work packet after the provider turn ends.",
    }
    path = _receipt_path(settings, route_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt["receipt_path"] = str(path)
    path.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    await store.audit(
        "hermes-workspace-bridge",
        "workspace_turn_prepared",
        route_id,
        {
            "workspace_id": workspace_id,
            "workspace_session_id": workspace_session["id"],
            "work_packet_id": work_packet["id"],
            "receipt_path": str(path),
        },
    )
    return {
        "state": route_payload.get("state"),
        "workspace_id": workspace_id,
        "workspace_session": workspace_session,
        "work_packet": work_packet,
        "selected": selected,
        "route": route_payload,
        "receipt_path": str(path),
    }


async def complete_workspace_hermes_turn(
    settings: Settings,
    *,
    workspace_id: str,
    work_packet_id: str,
    provider: str,
    model: str,
    route: str,
    tokens_in: int,
    tokens_out: int,
    estimated_cost_usd: float | None,
    actual_cost_usd: float | None,
    quota_source: str,
    context_pressure: float | None,
    quota_state: dict[str, Any] | None = None,
    measurement_source: str = "hermes_runtime_state",
) -> dict[str, Any]:
    """Persist actual provider usage reported by Hermes for one prepared turn."""

    store = StateStore(settings)
    await store.initialize()
    event = await store.record_resource_event(
        workspace_id=workspace_id,
        work_packet_id=work_packet_id,
        provider=provider,
        model=model,
        route=route,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost_usd=estimated_cost_usd,
        actual_cost_usd=actual_cost_usd,
        quota_source=quota_source or "unknown",
        quota_state=quota_state,
        context_pressure=context_pressure,
        measurement_source=measurement_source,
    )
    await store.audit(
        "hermes-workspace-bridge",
        "workspace_turn_metered",
        str(event["id"]),
        {"workspace_id": workspace_id, "work_packet_id": work_packet_id, "provider": provider, "model": model, "measurement_source": measurement_source},
    )
    return {"state": "metered", "workspace_id": workspace_id, "work_packet_id": work_packet_id, "resource_event": event}
