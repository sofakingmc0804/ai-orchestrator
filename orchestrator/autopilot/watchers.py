from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.autopilot.event_sources import decompose_file_event
from orchestrator.autopilot.policy_engine import autopilot_enabled, policy_for_folder, standing_order_from_policy
from orchestrator.config import Settings
from orchestrator.state.store import StateStore


def _seen_path(settings: Settings) -> Path:
    path = settings.home / "autopilot_seen.json"
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    return path


def _read_seen(settings: Settings) -> dict[str, float]:
    try:
        data = json.loads(_seen_path(settings).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_seen(settings: Settings, seen: dict[str, float]) -> None:
    _seen_path(settings).write_text(json.dumps(seen, indent=2), encoding="utf-8")


async def scan_autopilot_folder_once(
    settings: Settings,
    store: StateStore,
    folder: Path,
    policy_override: dict[str, Any] | None = None,
    policy_label: str | None = None,
) -> dict[str, Any]:
    folder = folder.expanduser().resolve()
    disk_policy, policy_path = policy_for_folder(folder)
    policy = policy_override or disk_policy
    policy_source = policy_label or (str(policy_path) if policy_path else None)
    order = standing_order_from_policy(folder, policy)
    if not autopilot_enabled(policy):
        return {"folder": str(folder), "enabled": False, "policy_file": policy_source, "queued": 0, "events": []}
    seen = _read_seen(settings)
    events: list[dict[str, Any]] = []
    queued = 0
    for path in sorted(folder.iterdir()):
        if path.name == ".orchestrator-policy.yaml" or not path.is_file():
            continue
        key = str(path)
        mtime = path.stat().st_mtime
        if seen.get(key) == mtime:
            continue
        event = decompose_file_event(path, policy)
        selection = await store.add_selection(
            "file",
            {
                "path": str(path),
                "exists": True,
                "source": "autopilot",
                "policy_file": policy_source,
                "intent_text": event["intent_text"],
                "max_consequence": event["max_consequence"],
            },
        )
        event["selection_id"] = selection.id
        events.append(event)
        seen[key] = mtime
        queued += 1
    _write_seen(settings, seen)
    await store.audit("autopilot", "scan_once", str(folder), {"standing_order": order, "queued": queued, "events": events})
    return {"folder": str(folder), "enabled": True, "policy_file": policy_source, "queued": queued, "events": events}


async def scan_autopilot_roots_once(settings: Settings, roots: list[Path]) -> dict[str, Any]:
    store = StateStore(settings)
    await store.initialize()
    results = [await scan_autopilot_folder_once(settings, store, root) for root in roots]
    return {"roots": results, "queued": sum(int(r.get("queued") or 0) for r in results)}
