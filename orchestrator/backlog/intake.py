from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from orchestrator.backlog.discovery import discover_backlog_candidates
from orchestrator.config import Settings
from orchestrator.state.store import StateStore


def default_backlog_roots() -> list[Path]:
    profile = Path(os.getenv("USERPROFILE", str(Path.home())))
    shared = Path(os.getenv("ORCHESTRATOR_SHARED_DRIVE_ROOT", r"D:\SharedRoot\Workspace"))
    candidates = [
        profile / "dev",
        profile / "example-rebuild",
        shared / "Programming Projects",
        shared / "Operations",
        shared / "Government Contracts",
        shared / "Website Domain and Hosting",
    ]
    return [path for path in candidates if path.is_dir()]


def _source_backed_instruction(candidate: dict[str, Any]) -> str:
    source_task = str(candidate.get("source_task_id") or f"line {candidate.get('source_line') or 'unknown'}")
    return "\n".join(
        [
            "Execute this source-backed project task within the stated project root.",
            f"Source file: {candidate['source_path']}",
            f"Source task: {source_task}",
            f"Title: {candidate['title']}",
            f"Description: {candidate['description']}",
            f"Project root: {candidate['project_root']}",
            "The project root is the only filesystem authority for this task and may not be a Git repository. Do not search outside the project root to locate another repository or context.",
            "Action boundary: inspect, test, build, and make only task-required local project changes. Do not send messages, alter external systems, or operate outside the project root.",
            "Return exactly one valid JSON object with terminal_state='produced', a summary containing source and task, evidence of commands or files checked, and a next_action containing evidence. Do not use Markdown fences or interim/background-status language.",
        ]
    )


def _source_backed_operation_task(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": f"backlog-{candidate['id']}",
        "domain_id": "repo_coding",
        "validator": "required_fields_and_terms",
        "expected": {
            "terminal_state": ["produced"],
            "must_contain": {
                "summary": ["source", "task"],
                "next_action": ["evidence"],
            },
        },
    }


def _priority(candidate: dict[str, Any]) -> tuple[int, str, str]:
    status_rank = {"active": 0, "in_progress": 0, "in-progress": 0, "ready": 1, "queued": 2, "blocked": 3, "invalid": 4}
    return (
        status_rank.get(str(candidate.get("source_status") or ""), 5),
        str(candidate.get("project_root") or ""),
        str(candidate.get("title") or ""),
    )


async def intake_discovered_work(
    settings: Settings,
    store: StateStore,
    *,
    roots: list[Path] | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """Persist source work and feed a bounded number of unpromoted items to the work queue."""
    active_roots = roots if roots is not None else default_backlog_roots()
    source_candidates = discover_backlog_candidates(active_roots)
    persisted = [await store.upsert_discovered_work_item(candidate) for candidate in source_candidates]
    promoted = 0
    work_items: list[str] = []
    for candidate in sorted(persisted, key=_priority):
        if promoted >= max(1, min(int(limit), 20)):
            break
        if str(candidate.get("state")) != "discovered":
            continue
        work = await store.enqueue_delegated_work_item(
            {
                "source": "backlog_discovery",
                "raw_text": _source_backed_instruction(candidate),
                "consumer": f"Matt / {Path(str(candidate['project_root'])).name} project backlog",
                "job_class": "repo_coding",
                "project_root": candidate["project_root"],
                "mode": "immediate",
                "operation_task": _source_backed_operation_task(candidate),
                "min_quality_score": 1.0,
            }
        )
        await store.mark_discovered_work_promoted(str(candidate["id"]), str(work["id"]))
        promoted += 1
        work_items.append(str(work["id"]))
    result = {
        "state": "produced",
        "roots": [str(path) for path in active_roots],
        "discovered": len(persisted),
        "promoted": promoted,
        "work_items": work_items,
    }
    await store.audit("backlog_discovery", "intake_ran", "discovered_work_items", result)
    return result
