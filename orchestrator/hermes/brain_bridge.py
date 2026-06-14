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
