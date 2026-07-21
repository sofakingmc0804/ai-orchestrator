from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.delegation import work_cycle
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.models import DispatchResult
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.process.supervisor import DELEGATED_WORK_TASK_ID, ensure_delegated_work_scheduler_task
from orchestrator.scheduler import tasks as scheduler_tasks
from orchestrator.scheduler.tasks import run_scheduler_task_once
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=tmp_path,
    )


def _proof_operation_task() -> dict[str, object]:
    return {
        "task_id": "delegated-work-proof",
        "domain_id": "routing_triage",
        "validator": "required_fields_and_terms",
        "expected": {
            "terminal_state": ["produced"],
            "must_contain": {
                "summary": ["delegated", "runtime"],
                "next_action": ["receipt"],
            },
        },
    }


@pytest.mark.asyncio
async def test_delegated_work_item_round_trips_with_owner_and_consumer(tmp_path: Path) -> None:
    store = StateStore(_settings(tmp_path))
    await store.initialize()

    item = await store.enqueue_delegated_work_item(
        {
            "raw_text": "Return the required JSON proof.",
            "consumer": "runtime-verification",
            "job_class": "routing_triage",
            "mode": "immediate",
            "operation_task": _proof_operation_task(),
        }
    )

    rows = await store.list_delegated_work_items(states={"queued"})

    assert item["id"].startswith("wrk_")
    assert rows[0]["id"] == item["id"]
    assert rows[0]["consumer"] == "runtime-verification"
    assert rows[0]["state"] == "queued"
    assert rows[0]["operation_task"] == _proof_operation_task()


@pytest.mark.asyncio
async def test_failed_work_item_can_requeue_after_a_repair(tmp_path: Path) -> None:
    store = StateStore(_settings(tmp_path))
    await store.initialize()
    item = await _queued_proof_item(store, mode="immediate")

    await store.update_delegated_work_item(str(item["id"]), "failed", error="adapter_unavailable")
    retried = await store.update_delegated_work_item(str(item["id"]), "queued", error=None)

    assert retried is not None
    assert retried["state"] == "queued"
    assert retried["error"] is None
    assert retried["completed_at"] is None


@pytest.mark.asyncio
async def test_delegated_work_status_can_return_newest_items_first(tmp_path: Path) -> None:
    store = StateStore(_settings(tmp_path))
    await store.initialize()
    first = await _queued_proof_item(store, mode="immediate")
    second = await _queued_proof_item(store, mode="immediate")

    rows = await store.list_delegated_work_items(limit=2, newest_first=True)

    assert rows[0]["id"] == second["id"]
    assert rows[1]["id"] == first["id"]


@pytest.mark.asyncio
async def test_dispatch_text_persists_supplied_operation_task(tmp_path: Path) -> None:
    class AccurateAdapter:
        async def dispatch(self, _envelope: dict[str, Any]) -> dict[str, Any]:
            return {
                "ok": True,
                "model": "accurate-local",
                "text": json.dumps(
                    {
                        "terminal_state": "produced",
                        "summary": "delegated runtime proof",
                        "next_action": "read receipt",
                    }
                ),
                "raw": {"provider": "fake-local", "proof": "live"},
            }

    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.db.execute(
        "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
        "repo_coding",
        '["coding", "tools"]',
        "{}",
        0,
        "local_resource",
    )
    await store.db.execute(
        """
        INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
          capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        "accurate-local@ollama-local",
        "accurate-local",
        "accurate-local",
        "ollama-local",
        "ollama-local",
        "local_resource",
        '["coding"]',
        '["tools"]',
        '["text"]',
        '{"coding": 8, "speed": 8, "stability": 8}',
        '["repo_coding"]',
        "[]",
    )
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.adapters = {"ollama-http": AccurateAdapter()}  # type: ignore[assignment]

    result = await dispatcher.dispatch_text(
        "Return the required JSON proof.",
        job_class_override="repo_coding",
        source="delegated_work",
        operation_task=_proof_operation_task(),
    )

    assert result.state == "completed"
    assert result.receipt["operation_quality_score"]["composite_score"] == 1.0


@pytest.mark.asyncio
async def test_source_backlog_authorization_executes_bounded_local_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    captured: dict[str, object] = {}

    async def fake_execute(*_args: object, **_kwargs: object) -> DispatchResult:
        captured.update(_kwargs)
        return DispatchResult(dispatch_id="dsp_backlog", intent_id="int_backlog", adapter_name="hermes-agent", state="completed")

    monkeypatch.setattr(dispatcher, "_execute_intent", fake_execute)
    result = await dispatcher.dispatch_text(
        "Execute a bounded local project change.",
        project_root=tmp_path,
        source="backlog_discovery",
        auto_approve_local=True,
    )

    assert result.state == "completed"
    assert captured["preferred_adapter"] == "hermes-agent"


@pytest.mark.asyncio
async def test_source_backlog_dispatch_passes_project_root_to_the_worker(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class CaptureAdapter:
        async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
            captured.update(envelope)
            return {
                "ok": True,
                "model": "desktop-hermes",
                "text": json.dumps(
                    {
                        "terminal_state": "produced",
                        "summary": "delegated runtime proof",
                        "next_action": "read receipt",
                    }
                ),
                "raw": {"provider": "governed-desktop", "proof": "live"},
            }

    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.adapters = {"hermes-agent": CaptureAdapter()}  # type: ignore[assignment]

    result = await dispatcher.dispatch_text(
        "Execute the source-backed local task.",
        project_root=tmp_path,
        job_class_override="repo_coding",
        source="backlog_discovery",
        operation_task=_proof_operation_task(),
        auto_approve_local=True,
    )

    assert result.state == "completed"
    assert captured["project_root"] == str(tmp_path)


@pytest.mark.asyncio
async def test_backlog_work_cycle_retries_approval_paused_item_under_source_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDispatcher:
        def __init__(self, settings: Settings, _store: StateStore, _notifications: NotificationSpine) -> None:
            self.settings = settings

        async def dispatch_text(self, _text: str, **kwargs: object) -> DispatchResult:
            assert kwargs["source"] == "backlog_discovery"
            assert kwargs["auto_approve_local"] is True
            assert kwargs["preferred_adapter"] == "hermes-agent"
            output_path = self.settings.home / "backlog-result.json"
            receipt_path = self.settings.home / "backlog-receipt.json"
            output_path.write_text('{"terminal_state":"produced"}', encoding="utf-8")
            receipt_path.write_text('{"proof_kind":"live"}', encoding="utf-8")
            return DispatchResult(
                dispatch_id="dsp_backlog",
                intent_id="int_backlog",
                adapter_name="hermes-agent",
                state="completed",
                output_path=output_path,
                receipt={"receipt_path": str(receipt_path), "operation_quality_score": {"composite_score": 1.0}},
            )

    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    item = await store.enqueue_delegated_work_item(
        {
            "source": "backlog_discovery",
            "raw_text": "Execute local source-backed work.",
            "consumer": "Matt / test backlog",
            "job_class": "repo_coding",
            "project_root": str(tmp_path),
            "mode": "immediate",
            "operation_task": _proof_operation_task(),
        }
    )
    await store.update_delegated_work_item(str(item["id"]), "awaiting_approval")
    monkeypatch.setattr(work_cycle, "Dispatcher", FakeDispatcher)

    result = await work_cycle.run_delegated_work_cycle(settings, store)
    row = await store.get_delegated_work_item(str(item["id"]))

    assert result["completed"] == 1
    assert row is not None
    assert row["state"] == "completed"


@pytest.mark.asyncio
async def test_work_cycle_defers_same_project_while_source_work_is_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    active = await store.enqueue_delegated_work_item(
        {
            "source": "backlog_discovery",
            "raw_text": "Finish the first local source task.",
            "consumer": "Matt / test backlog",
            "job_class": "repo_coding",
            "project_root": str(tmp_path),
            "mode": "immediate",
            "operation_task": _proof_operation_task(),
        }
    )
    queued = await store.enqueue_delegated_work_item(
        {
            "source": "backlog_discovery",
            "raw_text": "Do not overlap the first local source task.",
            "consumer": "Matt / test backlog",
            "job_class": "repo_coding",
            "project_root": str(tmp_path),
            "mode": "immediate",
            "operation_task": _proof_operation_task(),
        }
    )
    await store.update_delegated_work_item(str(active["id"]), "running")

    class UnexpectedDispatcher:
        def __init__(self, *_args: object) -> None:
            pass

        async def dispatch_text(self, *_args: object, **_kwargs: object) -> DispatchResult:
            raise AssertionError("source work in the same project must not overlap")

    monkeypatch.setattr(work_cycle, "Dispatcher", UnexpectedDispatcher)
    result = await work_cycle.run_delegated_work_cycle(settings, store)
    row = await store.get_delegated_work_item(str(queued["id"]))

    assert result["deferred"] == 1
    assert result["work_items"] == [{"id": str(queued["id"]), "state": "deferred", "reason": "project_work_in_progress"}]
    assert row is not None
    assert row["state"] == "queued"


@pytest.mark.asyncio
async def test_work_cycle_skips_busy_project_and_runs_next_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    active = await store.enqueue_delegated_work_item(
        {
            "source": "backlog_discovery",
            "raw_text": "Finish the active local task.",
            "consumer": "Matt / test backlog",
            "job_class": "repo_coding",
            "project_root": str(tmp_path),
            "mode": "immediate",
            "operation_task": _proof_operation_task(),
        }
    )
    blocked = await store.enqueue_delegated_work_item(
        {
            "source": "backlog_discovery",
            "raw_text": "Wait for the active local task.",
            "consumer": "Matt / test backlog",
            "job_class": "repo_coding",
            "project_root": str(tmp_path),
            "mode": "immediate",
            "operation_task": _proof_operation_task(),
        }
    )
    runnable = await store.enqueue_delegated_work_item(
        {
            "source": "backlog_discovery",
            "raw_text": "Run the next project's local task.",
            "consumer": "Matt / test backlog",
            "job_class": "repo_coding",
            "project_root": str(other_project),
            "mode": "immediate",
            "operation_task": _proof_operation_task(),
        }
    )
    await store.update_delegated_work_item(str(active["id"]), "running")

    class FakeDispatcher:
        def __init__(self, active_settings: Settings, *_args: object) -> None:
            self.settings = active_settings

        async def dispatch_text(self, _text: str, **_kwargs: object) -> DispatchResult:
            output_path = self.settings.home / "next-project-result.json"
            receipt_path = self.settings.home / "next-project-receipt.json"
            output_path.write_text('{"terminal_state":"produced"}', encoding="utf-8")
            receipt_path.write_text('{"proof_kind":"live"}', encoding="utf-8")
            return DispatchResult(
                dispatch_id="dsp_next_project",
                intent_id="int_next_project",
                adapter_name="hermes-agent",
                state="completed",
                output_path=output_path,
                receipt={"receipt_path": str(receipt_path), "operation_quality_score": {"composite_score": 1.0}},
            )

    monkeypatch.setattr(work_cycle, "Dispatcher", FakeDispatcher)
    result = await work_cycle.run_delegated_work_cycle(settings, store, limit=1)
    blocked_row = await store.get_delegated_work_item(str(blocked["id"]))
    runnable_row = await store.get_delegated_work_item(str(runnable["id"]))

    assert result["deferred"] == 1
    assert result["completed"] == 1
    assert blocked_row is not None and blocked_row["state"] == "queued"
    assert runnable_row is not None and runnable_row["state"] == "completed"


async def _queued_proof_item(store: StateStore, mode: str) -> dict[str, object]:
    return await store.enqueue_delegated_work_item(
        {
            "raw_text": "Return the required JSON proof.",
            "consumer": "runtime-verification",
            "job_class": "routing_triage",
            "mode": mode,
            "operation_task": _proof_operation_task(),
        }
    )


@pytest.mark.asyncio
async def test_immediate_work_dispatches_and_completes_after_full_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDispatcher:
        def __init__(self, settings: Settings, _store: StateStore, _notifications: NotificationSpine) -> None:
            self.settings = settings

        async def dispatch_text(self, _text: str, **_kwargs: object) -> DispatchResult:
            output_path = self.settings.home / "proof-result.json"
            receipt_path = self.settings.home / "proof-receipt.json"
            output_path.write_text('{"terminal_state":"produced"}', encoding="utf-8")
            receipt_path.write_text('{"proof_kind":"live"}', encoding="utf-8")
            return DispatchResult(
                dispatch_id="dsp_delegated_proof",
                intent_id="int_delegated_proof",
                adapter_name="ollama-http",
                state="completed",
                output_path=output_path,
                receipt={
                    "receipt_path": str(receipt_path),
                    "operation_quality_score": {"composite_score": 1.0},
                },
            )

    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    item = await _queued_proof_item(store, mode="immediate")
    monkeypatch.setattr(work_cycle, "Dispatcher", FakeDispatcher)

    result = await work_cycle.run_delegated_work_cycle(settings, store)

    row = await store.get_delegated_work_item(str(item["id"]))
    assert result["completed"] == 1
    assert row is not None
    assert row["state"] == "completed"
    assert row["dispatch_id"] == "dsp_delegated_proof"
    assert row["validation"]["composite_score"] == 1.0


@pytest.mark.asyncio
async def test_discretionary_work_holds_without_expiring_measured_surplus(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    item = await _queued_proof_item(store, mode="discretionary")

    result = await work_cycle.run_delegated_work_cycle(settings, store)

    row = await store.get_delegated_work_item(str(item["id"]))
    assert result["held"] == 1
    assert row is not None
    assert row["state"] == "held"
    assert row["error"] == "no_expiring_measured_surplus"


@pytest.mark.asyncio
async def test_delegated_work_scheduler_task_runs_the_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    task_id = await ensure_delegated_work_scheduler_task(store)
    task = await store.get_scheduler_task(task_id)

    async def fake_cycle(_settings: Settings, _store: StateStore, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced", "completed": 1, "held": 0, "failed": 0, "awaiting_approval": 0}

    monkeypatch.setattr(scheduler_tasks, "run_delegated_work_cycle", fake_cycle)
    result = await run_scheduler_task_once(settings, store, task, force=True)

    assert task_id == DELEGATED_WORK_TASK_ID
    assert task is not None
    assert task["task_type"] == "delegated_work_cycle"
    assert task["enabled"] == 1
    assert result["state"] == "completed"
    assert result["delegated_work"]["completed"] == 1


def test_cli_exposes_delegated_work_commands() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = subprocess.run(
        [sys.executable, "-m", "orchestrator.cli.main", "--help"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "work-submit" in output
    assert "work-cycle" in output
    assert "work-status" in output
    assert "work-retry" in output
    assert "work-discover" in output
