from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.state.store import StateStore


def _parse_windows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw = snapshot.get("usage_windows_json") or "[]"
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _as_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def has_expiring_measured_surplus(
    snapshots: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    min_surplus_percent: float = 70.0,
    surplus_window_seconds: int = 7_200,
) -> bool:
    """True only for a fresh, measured provider window that would soon expire."""
    current = now or datetime.now(timezone.utc)
    for snapshot in snapshots:
        if str(snapshot.get("status") or "") != "ok":
            continue
        if str(snapshot.get("confidence") or "") != "provider_usage_window":
            continue
        for window in _parse_windows(snapshot):
            try:
                remaining_percent = float(window.get("remaining_percent"))
            except (TypeError, ValueError):
                continue
            reset_at = _as_utc(window.get("reset_at") or snapshot.get("reset_at"))
            if reset_at is None:
                continue
            seconds_until_reset = (reset_at - current).total_seconds()
            if 0 < seconds_until_reset <= surplus_window_seconds and remaining_percent >= min_surplus_percent:
                return True
    return False


def _validation_from_result(result: Any) -> dict[str, Any]:
    receipt = result.receipt if isinstance(getattr(result, "receipt", None), dict) else {}
    value = receipt.get("operation_quality_score")
    return value if isinstance(value, dict) else {}


def _project_key(item: dict[str, Any]) -> str:
    value = str(item.get("project_root") or "").strip()
    return value.replace("/", "\\").rstrip("\\").casefold() if value else ""


async def run_delegated_work_cycle(
    settings: Settings,
    store: StateStore | None = None,
    *,
    limit: int = 1,
    min_surplus_percent: float = 70.0,
    surplus_window_seconds: int = 7_200,
) -> dict[str, Any]:
    """Dispatch queued owner work and reserve expiring surplus for discretionary work."""
    owns_store = store is None
    state = store or StateStore(settings)
    if owns_store:
        await state.initialize()

    limit = max(1, min(int(limit), 20))
    candidates = await state.list_delegated_work_items(states={"queued", "held", "awaiting_approval"}, limit=1_000)
    active_project_roots = {
        key
        for key in (
            _project_key(item)
            for item in await state.list_delegated_work_items(states={"running"}, limit=1_000)
        )
        if key
    }
    snapshots = await state.list_subscription_usage_snapshots()
    result: dict[str, Any] = {
        "state": "produced",
        "queued_considered": len(candidates),
        "completed": 0,
        "held": 0,
        "failed": 0,
        "deferred": 0,
        "awaiting_approval": 0,
        "work_items": [],
    }
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        project_key = _project_key(candidate)
        if project_key and project_key in active_project_roots:
            result["deferred"] += 1
            result["work_items"].append(
                {"id": str(candidate["id"]), "state": "deferred", "reason": "project_work_in_progress"}
            )
            continue
        if len(items) >= limit:
            continue
        items.append(candidate)
        if project_key:
            active_project_roots.add(project_key)
    dispatcher = Dispatcher(settings, state, NotificationSpine(settings.notifications_path, state))

    for item in items:
        item_id = str(item["id"])
        source = str(item.get("source") or "delegated_work")
        project_key = _project_key(item)
        if str(item.get("state")) == "awaiting_approval" and source != "backlog_discovery":
            result["awaiting_approval"] += 1
            result["work_items"].append({"id": item_id, "state": "awaiting_approval"})
            continue
        if str(item.get("mode")) == "discretionary" and not has_expiring_measured_surplus(
            snapshots,
            min_surplus_percent=min_surplus_percent,
            surplus_window_seconds=surplus_window_seconds,
        ):
            updated = await state.update_delegated_work_item(
                item_id,
                "held",
                error="no_expiring_measured_surplus",
            )
            result["held"] += 1
            result["work_items"].append({"id": item_id, "state": "held", "reason": "no_expiring_measured_surplus"})
            if updated is None:
                result["failed"] += 1
            continue

        await state.update_delegated_work_item(item_id, "running", error=None)
        project_root_text = str(item.get("project_root") or "")
        project_root = Path(project_root_text) if project_root_text else None
        if project_root is not None and not project_root.exists():
            updated = await state.update_delegated_work_item(
                item_id,
                "failed",
                error=f"project_root_missing:{project_root}",
            )
            result["failed"] += 1
            result["work_items"].append({"id": item_id, "state": "failed", "error": updated.get("error") if updated else "work_item_missing"})
            continue

        try:
            dispatch = await dispatcher.dispatch_text(
                str(item["raw_text"]),
                project_root=project_root,
                job_class_override=str(item["job_class"]),
                source=source,
                operation_task=item.get("operation_task") if isinstance(item.get("operation_task"), dict) else None,
                auto_approve_local=source == "backlog_discovery",
                preferred_adapter="hermes-agent" if source == "backlog_discovery" else None,
            )
        except Exception as exc:
            updated = await state.update_delegated_work_item(item_id, "failed", error=f"dispatch_exception:{exc}")
            result["failed"] += 1
            result["work_items"].append({"id": item_id, "state": "failed", "error": updated.get("error") if updated else str(exc)})
            continue

        if dispatch.state == "awaiting_approval":
            await state.update_delegated_work_item(item_id, "awaiting_approval")
            result["awaiting_approval"] += 1
            result["work_items"].append({"id": item_id, "state": "awaiting_approval", "intent_id": dispatch.intent_id})
            continue

        validation = _validation_from_result(dispatch)
        score = float(validation.get("composite_score") or 0.0)
        output_path = Path(dispatch.output_path) if dispatch.output_path else None
        receipt_path_text = str((dispatch.receipt or {}).get("receipt_path") or "")
        receipt_path = Path(receipt_path_text) if receipt_path_text else None
        if dispatch.state != "completed":
            error = str(dispatch.error or f"dispatch_state:{dispatch.state}")
        elif output_path is None or not output_path.is_file():
            error = "dispatch_output_missing"
        elif receipt_path is None or not receipt_path.is_file():
            error = "dispatch_receipt_missing"
        elif score < float(item["min_quality_score"]):
            error = f"quality_score_below_threshold:{score}<{item['min_quality_score']}"
        else:
            error = ""

        if error:
            updated = await state.update_delegated_work_item(
                item_id,
                "failed",
                dispatch_id=dispatch.dispatch_id or None,
                output_path=str(output_path) if output_path else None,
                receipt_path=str(receipt_path) if receipt_path else None,
                validation=validation,
                error=error,
            )
            result["failed"] += 1
            result["work_items"].append({"id": item_id, "state": "failed", "dispatch_id": dispatch.dispatch_id, "error": updated.get("error") if updated else error})
            continue

        await state.update_delegated_work_item(
            item_id,
            "completed",
            dispatch_id=dispatch.dispatch_id,
            output_path=str(output_path),
            receipt_path=str(receipt_path),
            validation=validation,
            error=None,
        )
        result["completed"] += 1
        result["work_items"].append({
            "id": item_id,
            "state": "completed",
            "dispatch_id": dispatch.dispatch_id,
            "output_path": str(output_path),
            "receipt_path": str(receipt_path),
            "composite_score": score,
        })

    await state.audit("delegated_work", "cycle_ran", "delegated_work_items", result)
    return result
