from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.state.store import StateStore


DEFAULT_LEGACY_MANIFESTS = [
    Path.home() / "example-rebuild" / "build" / "configs" / "dell-scheduled-tasks.json",
    Path.home() / "example-rebuild" / "build" / "configs" / "msi-scheduled-tasks.json",
    Path.home() / "example-rebuild" / "dist" / "configs" / "all-scheduled-tasks.json",
    Path.home() / "AppData" / "Roaming" / "Claude" / "scheduled-tasks.json",
]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return slug[:72] or "legacy"


def default_legacy_manifest_paths() -> list[Path]:
    paths = list(DEFAULT_LEGACY_MANIFESTS)
    claude_root = Path.home() / "AppData" / "Roaming" / "Claude" / "local-agent-mode-sessions"
    if claude_root.exists():
        paths.extend(sorted(claude_root.rglob("scheduled-tasks.json"))[:50])
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def read_legacy_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    tasks = data.get("scheduledTasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return []
    normalized: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        normalized.append(task)
    return normalized


def assess_legacy_task_risk(task: dict[str, Any]) -> dict[str, Any]:
    permissions = task.get("approvedPermissions", [])
    permission_names = [
        str(item.get("toolName") or "")
        for item in permissions
        if isinstance(item, dict)
    ]
    flags: list[str] = []
    if task.get("enabled") is True:
        flags.append("legacy_manifest_enabled_true")
    if any("gmail_create_draft" in name for name in permission_names):
        flags.append("gmail_draft_permission")
    if any("gmail" in name for name in permission_names):
        flags.append("gmail_access")
    if any("write_file" in name.lower() for name in permission_names):
        flags.append("filesystem_write")
    if any("powershell" in name.lower() for name in permission_names):
        flags.append("powershell_access")
    if str(task.get("chromePermissionMode") or "").lower() == "skip_all_permission_checks":
        flags.append("chrome_permission_checks_skipped")
    selected = task.get("userSelectedFolders", [])
    if isinstance(selected, list) and any(str(path).lower().rstrip("\\/") in {"c:\\users\\couch", "c:/users/couch"} for path in selected):
        flags.append("broad_user_profile_scope")
    file_path = str(task.get("filePath") or "")
    if file_path and not Path(file_path).exists():
        flags.append("skill_file_missing")
    if not task.get("cronExpression"):
        flags.append("missing_cron_expression")

    if any(flag in flags for flag in ["gmail_draft_permission", "chrome_permission_checks_skipped", "broad_user_profile_scope"]):
        level = "high"
    elif any(flag in flags for flag in ["gmail_access", "filesystem_write", "powershell_access", "skill_file_missing"]):
        level = "medium"
    else:
        level = "low"
    return {
        "risk_level": level,
        "risk_flags": flags,
        "permission_names": permission_names,
        "skill_file_exists": bool(file_path and Path(file_path).exists()),
    }


async def migrate_legacy_scheduled_tasks(
    settings: Settings,
    store: StateStore | None = None,
    manifest_paths: list[Path] | None = None,
) -> dict[str, Any]:
    owns_store = store is None
    store = store or StateStore(settings)
    if owns_store:
        await store.initialize()

    paths = manifest_paths or default_legacy_manifest_paths()
    imported = 0
    discovered = 0
    sources: list[dict[str, Any]] = []
    for path in paths:
        tasks = read_legacy_manifest(path)
        if not tasks:
            sources.append({"path": str(path), "tasks": 0, "imported": 0, "exists": path.exists()})
            continue
        source_imported = 0
        source_slug = _slug(path.stem)
        for task in tasks:
            discovered += 1
            legacy_id = str(task["id"])
            scheduler_task_id = f"task_legacy_{source_slug}_{_slug(legacy_id)}"
            risk = assess_legacy_task_risk(task)
            payload = {
                "legacy_id": legacy_id,
                "source_manifest": str(path),
                "cron_expression": task.get("cronExpression"),
                "file_path": task.get("filePath"),
                "model": task.get("model"),
                "created_at_ms": task.get("createdAt"),
                "approved_permissions": task.get("approvedPermissions", []),
                "user_selected_folders": task.get("userSelectedFolders", []),
                "risk_level": risk["risk_level"],
                "risk_flags": risk["risk_flags"],
                "permission_names": risk["permission_names"],
                "skill_file_exists": risk["skill_file_exists"],
                "raw": task,
                "migration_policy": "imported_disabled_no_automatic_resurrection",
            }
            await store.upsert_scheduler_task(
                name=f"Legacy Claude task: {legacy_id}",
                task_type="legacy_claude_scheduled_task",
                target_ref=legacy_id,
                payload=payload,
                schedule_kind="cron" if task.get("cronExpression") else "manual",
                interval_seconds=0,
                enabled=False,
                task_id=scheduler_task_id,
                review_state="needs_review",
            )
            imported += 1
            source_imported += 1
        sources.append({"path": str(path), "tasks": len(tasks), "imported": source_imported, "exists": True})
    result = {
        "sources": sources,
        "discovered": discovered,
        "imported": imported,
        "enabled": 0,
        "policy": "legacy scheduled tasks are imported disabled; manual owner review required before enabling",
    }
    await store.audit("scheduler", "legacy_scheduled_tasks_migrated", "scheduler_tasks", result)
    return result
