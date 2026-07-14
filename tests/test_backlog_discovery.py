from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.backlog.discovery import discover_backlog_candidates
from orchestrator.backlog import discovery
from orchestrator.backlog.intake import _priority, default_backlog_roots, intake_discovered_work
from orchestrator.config import Settings
from orchestrator.process.supervisor import BACKLOG_DISCOVERY_TASK_ID, ensure_backlog_discovery_scheduler_task
from orchestrator.scheduler import tasks as scheduler_tasks
from orchestrator.scheduler.tasks import run_scheduler_task_once
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    home = tmp_path / ".orchestrator"
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=tmp_path,
    )


def test_discovery_extracts_ready_task_with_source_evidence(tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.yaml"
    ledger.write_text(
        "tasks:\n  - id: TASK-1\n    title: Verify runtime\n    status: ready\n    description: Run pytest.\n",
        encoding="utf-8",
    )

    candidates = discover_backlog_candidates([tmp_path])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["kind"] == "task_ledger"
    assert candidate["source_path"] == str(ledger)
    assert candidate["source_task_id"] == "TASK-1"
    assert candidate["source_line"] == 2
    assert candidate["title"] == "Verify runtime"
    assert candidate["description"] == "Run pytest."
    assert candidate["source_status"] == "ready"
    assert candidate["state"] == "discovered"


def test_discovery_preserves_malformed_ledger_as_repair_candidate(tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.yaml"
    ledger.write_text(
        "tasks:\n  - id: TASK-1\n    title: broken\n  TASK-2:\n",
        encoding="utf-8",
    )

    candidates = discover_backlog_candidates([tmp_path])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["kind"] == "malformed_task_ledger"
    assert candidate["source_path"] == str(ledger)
    assert candidate["source_line"] is not None
    assert candidate["state"] == "discovered"
    assert "yaml" in candidate["description"].lower()


def test_source_file_lookup_uses_targeted_rg_patterns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.yaml"
    ledger.write_text("tasks: []\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(stdout=f"{ledger}\n", stderr="", returncode=0)

    monkeypatch.setattr(discovery.shutil, "which", lambda _name: "rg")
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)

    sources = list(discovery._source_files(tmp_path))

    assert sources == [ledger]
    assert calls[0][0:2] == ["rg", "--files"]
    assert "TASKS.yaml" in calls[0]


def test_source_file_lookup_does_not_fall_back_to_a_slow_walk_after_rg_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "TASKS.yaml").write_text("tasks: []\n", encoding="utf-8")

    def timed_out(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise discovery.subprocess.TimeoutExpired("rg", 20)

    monkeypatch.setattr(discovery.shutil, "which", lambda _name: "rg")
    monkeypatch.setattr(discovery.subprocess, "run", timed_out)

    assert list(discovery._source_files(tmp_path)) == []


def test_in_progress_source_tasks_sort_ahead_of_ready_work() -> None:
    candidates = [
        {"source_status": "ready", "project_root": "C:/ready", "title": "ready"},
        {"source_status": "in-progress", "project_root": "C:/active", "title": "active"},
    ]

    ordered = sorted(candidates, key=_priority)

    assert ordered[0]["source_status"] == "in-progress"


def test_default_backlog_roots_target_shared_project_areas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    shared = tmp_path / "shared"
    for path in [profile / "dev", profile / "example-rebuild", shared / "Programming Projects", shared / "Operations"]:
        path.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("ORCHESTRATOR_SHARED_DRIVE_ROOT", str(shared))

    roots = default_backlog_roots()

    assert shared not in roots
    assert shared / "Programming Projects" in roots
    assert shared / "Operations" in roots


@pytest.mark.asyncio
async def test_discovered_candidate_upsert_is_idempotent(tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.yaml"
    ledger.write_text(
        "tasks:\n  - id: TASK-1\n    title: Verify runtime\n    status: ready\n    description: Run pytest.\n",
        encoding="utf-8",
    )
    candidate = discover_backlog_candidates([tmp_path])[0]
    store = StateStore(_settings(tmp_path))
    await store.initialize()

    first = await store.upsert_discovered_work_item(candidate)
    second = await store.upsert_discovered_work_item(candidate)
    rows = await store.list_discovered_work_items()

    assert first["id"] == second["id"]
    assert len(rows) == 1
    assert rows[0]["fingerprint"] == candidate["fingerprint"]


@pytest.mark.asyncio
async def test_intake_promotes_ready_candidate_once(tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.yaml"
    ledger.write_text(
        "tasks:\n  - id: TASK-1\n    title: Verify runtime\n    status: ready\n    description: Run pytest.\n",
        encoding="utf-8",
    )
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()

    first = await intake_discovered_work(settings, store, roots=[tmp_path], limit=3)
    second = await intake_discovered_work(settings, store, roots=[tmp_path], limit=3)
    work_items = await store.list_delegated_work_items()
    candidates = await store.list_discovered_work_items()

    assert first["promoted"] == 1
    assert second["promoted"] == 0
    assert len(work_items) == 1
    assert work_items[0]["source"] == "backlog_discovery"
    assert "TASK-1" in work_items[0]["raw_text"]
    assert "Do not search outside the project root" in work_items[0]["raw_text"]
    assert candidates[0]["state"] == "promoted"
    assert candidates[0]["delegated_work_id"] == work_items[0]["id"]


@pytest.mark.asyncio
async def test_intake_repromotes_source_task_after_a_runtime_failure(tmp_path: Path) -> None:
    (tmp_path / "TASKS.yaml").write_text(
        "tasks:\n  - id: TASK-1\n    title: Verify runtime\n    status: ready\n    description: Run pytest.\n",
        encoding="utf-8",
    )
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()

    first = await intake_discovered_work(settings, store, roots=[tmp_path], limit=1)
    first_work_id = first["work_items"][0]
    await store.update_delegated_work_item(str(first_work_id), "failed", error="hermes timed out after 180s")

    second = await intake_discovered_work(settings, store, roots=[tmp_path], limit=1)
    work_items = await store.list_delegated_work_items()
    candidates = await store.list_discovered_work_items()

    assert second["promoted"] == 1
    assert len(work_items) == 2
    assert candidates[0]["state"] == "promoted"
    assert candidates[0]["delegated_work_id"] != first_work_id


@pytest.mark.asyncio
async def test_backlog_discovery_scheduler_task_runs_intake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    task_id = await ensure_backlog_discovery_scheduler_task(store)
    task = await store.get_scheduler_task(task_id)

    async def fake_intake(_settings: Settings, _store: StateStore, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced", "discovered": 2, "promoted": 1, "work_items": ["wrk_backlog"]}

    monkeypatch.setattr(scheduler_tasks, "intake_discovered_work", fake_intake)
    result = await run_scheduler_task_once(settings, store, task, force=True)

    assert task_id == BACKLOG_DISCOVERY_TASK_ID
    assert task is not None
    assert task["task_type"] == "backlog_discovery"
    assert result["state"] == "completed"
    assert result["backlog_discovery"]["promoted"] == 1
