from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.scheduler.migration import assess_legacy_task_risk, migrate_legacy_scheduled_tasks, read_legacy_manifest
from orchestrator.scheduler.tasks import run_due_scheduler_once, run_scheduler_once, run_standing_order_once, trigger_scheduler_task
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=Path.cwd(),
    )


@pytest.mark.asyncio
async def test_running_dispatch_recovers_as_interrupted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.record_dispatch(
        {
            "id": "dsp_running",
            "intent_id": "int_missing",
            "adapter_name": "ollama-http",
            "envelope": {"intent": {"raw_text": "work"}},
            "state": "running",
            "started_at": "2026-06-05T00:00:00+00:00",
        }
    )

    recovered = await store.recover_interrupted_dispatches()

    assert recovered[0]["dispatch_id"] == "dsp_running"
    dispatches = await store.list_dispatches()
    assert dispatches[0]["state"] == "interrupted"
    repairs = await store.list_repair_queue()
    assert repairs[0]["failure_source"] == "ollama-http"


@pytest.mark.asyncio
async def test_audit_log_records_state_changes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.audit("tester", "did_work", "target", {"ok": True})
    rows = await store.list_audit_log()
    assert rows[0]["actor"] == "tester"
    assert rows[0]["action"] == "did_work"


@pytest.mark.asyncio
async def test_standing_orders_persist(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    order_id = await store.upsert_standing_order(str(tmp_path), "autopilot: enabled", enabled=True)
    await store.mark_standing_order_fired(order_id)
    rows = await store.list_standing_orders()
    assert rows[0]["id"] == order_id
    assert rows[0]["enabled"] == 1
    assert rows[0]["last_fired_at"]


@pytest.mark.asyncio
async def test_disabled_standing_order_is_skipped_without_firing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    order_id = await store.upsert_standing_order(str(folder), "autopilot: enabled", enabled=False)
    order = (await store.list_standing_orders())[0]

    result = await run_standing_order_once(settings, store, order)

    assert result["state"] == "skipped"
    assert result["queued"] == 0
    rows = await store.list_standing_orders()
    assert rows[0]["id"] == order_id
    assert rows[0]["last_fired_at"] is None
    selections = await store.list_selections()
    assert selections == []


@pytest.mark.asyncio
async def test_enabled_standing_order_uses_stored_policy_and_queues_selection(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    target = folder / "input.txt"
    target.write_text("work", encoding="utf-8")
    policy = "autopilot: enabled\ndefault_intent: Summarize this file.\nmax_consequence: low\n"
    order_id = await store.upsert_standing_order(str(folder), policy, enabled=True)

    result = await run_scheduler_once(settings, store)

    assert result["orders"] == 1
    assert result["completed"] == 1
    assert result["queued"] == 1
    rows = await store.list_standing_orders()
    assert rows[0]["id"] == order_id
    assert rows[0]["last_fired_at"]
    selections = await store.list_selections()
    assert selections[0]["payload"]["source"] == "autopilot"
    assert selections[0]["payload"]["policy_file"] == f"standing_order:{order_id}"
    assert selections[0]["payload"]["intent_text"] == "Summarize this file."
    audit = await store.list_audit_log(limit=20)
    assert any(row["action"] == "scheduler_run_once" for row in audit)
    assert any(row["action"] == "standing_order_run" for row in audit)


@pytest.mark.asyncio
async def test_missing_standing_order_folder_creates_repair_item(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    missing = tmp_path / "missing"
    await store.upsert_standing_order(str(missing), "autopilot: enabled", enabled=True)

    result = await run_scheduler_once(settings, store)

    assert result["failed"] == 1
    repairs = await store.list_repair_queue()
    assert repairs[0]["failure_source"] == "scheduler"


@pytest.mark.asyncio
async def test_standing_orders_migrate_to_scheduler_tasks(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    order_id = await store.upsert_standing_order(str(folder), "autopilot: enabled", enabled=True)

    result = await store.migrate_standing_orders_to_scheduler_tasks()

    assert result["created"] == 1
    tasks = await store.list_scheduler_tasks()
    assert tasks[0]["id"] == f"task_{order_id}"
    assert tasks[0]["task_type"] == "standing_order_scan"
    assert tasks[0]["target_ref"] == order_id
    assert tasks[0]["enabled"] == 1


@pytest.mark.asyncio
async def test_paused_scheduler_task_skips_until_manual_trigger(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "input.txt").write_text("work", encoding="utf-8")
    order_id = await store.upsert_standing_order(str(folder), "autopilot: enabled\ndefault_intent: Classify this file.\n", enabled=True)
    task_id = await store.upsert_scheduler_task(
        name="paused",
        task_type="standing_order_scan",
        target_ref=order_id,
        payload={"folder_path": str(folder)},
        enabled=False,
    )

    skipped = await run_scheduler_once(settings, store)
    assert skipped["skipped"] == 1
    assert await store.list_selections() == []

    triggered = await trigger_scheduler_task(settings, task_id, store)
    assert triggered["state"] == "completed"
    assert triggered["queued"] == 1
    selections = await store.list_selections()
    assert selections[0]["payload"]["source"] == "autopilot"


@pytest.mark.asyncio
async def test_interval_scheduler_task_updates_next_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "input.txt").write_text("work", encoding="utf-8")
    order_id = await store.upsert_standing_order(str(folder), "autopilot: enabled", enabled=True)
    task_id = await store.upsert_scheduler_task(
        name="interval",
        task_type="standing_order_scan",
        target_ref=order_id,
        payload={"folder_path": str(folder)},
        schedule_kind="interval",
        interval_seconds=60,
        enabled=True,
    )

    result = await trigger_scheduler_task(settings, task_id, store)

    assert result["state"] == "completed"
    task = await store.get_scheduler_task(task_id)
    assert task is not None
    assert task["last_run_at"]
    assert task["next_run_at"]


@pytest.mark.asyncio
async def test_due_scheduler_skips_interval_task_before_next_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "input.txt").write_text("work", encoding="utf-8")
    order_id = await store.upsert_standing_order(str(folder), "autopilot: enabled", enabled=True)
    task_id = await store.upsert_scheduler_task(
        name="interval",
        task_type="standing_order_scan",
        target_ref=order_id,
        payload={"folder_path": str(folder)},
        schedule_kind="interval",
        interval_seconds=300,
        enabled=True,
    )
    await trigger_scheduler_task(settings, task_id, store)

    result = await run_due_scheduler_once(settings, store)

    assert result["skipped"] == 1
    assert result["runs"][0]["reason"] == "scheduler_task_not_due"


@pytest.mark.asyncio
async def test_due_scheduler_summarizes_skips_without_per_task_audit_churn(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_scheduler_task(
        name="disabled",
        task_type="standing_order_scan",
        target_ref="unused-disabled",
        enabled=False,
    )
    await store.upsert_scheduler_task(
        name="not due",
        task_type="budget_probes_cron",
        target_ref="budget",
        schedule_kind="interval",
        interval_seconds=300,
        enabled=True,
        next_run_at="2999-01-01T00:00:00+00:00",
    )

    result = await run_due_scheduler_once(settings, store)

    assert result["skipped"] == 2
    audit = await store.list_audit_log(limit=20)
    actions = [row["action"] for row in audit]
    assert "scheduler_due_run_once" in actions
    assert "scheduler_task_skipped" not in actions


@pytest.mark.asyncio
async def test_standing_order_migration_is_quiet_when_nothing_changed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    folder = tmp_path / "watched"
    folder.mkdir()
    await store.upsert_standing_order(str(folder), "autopilot: enabled", enabled=True)

    await store.migrate_standing_orders_to_scheduler_tasks()
    first_audit = await store.list_audit_log(limit=20)
    first_count = sum(1 for row in first_audit if row["action"] == "standing_orders_migrated")
    await store.migrate_standing_orders_to_scheduler_tasks()
    second_audit = await store.list_audit_log(limit=20)
    second_count = sum(1 for row in second_audit if row["action"] == "standing_orders_migrated")

    assert first_count == 1
    assert second_count == first_count


def test_read_legacy_manifest_normalizes_scheduled_tasks(tmp_path: Path) -> None:
    manifest = tmp_path / "scheduled-tasks.json"
    manifest.write_text(
        '{"scheduledTasks":[{"id":"daily-brief","cronExpression":"0 21 * * *","filePath":"C:/x/SKILL.md"}]}',
        encoding="utf-8",
    )

    tasks = read_legacy_manifest(manifest)

    assert tasks[0]["id"] == "daily-brief"


def test_legacy_task_risk_flags_high_risk_permissions(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "SKILL.md"
    risk = assess_legacy_task_risk(
        {
            "id": "daily-brief",
            "enabled": True,
            "filePath": str(missing),
            "chromePermissionMode": "skip_all_permission_checks",
            "userSelectedFolders": ["C:\\Users\\Couch"],
            "approvedPermissions": [
                {"toolName": "mcp__Desktop_Commander__write_file"},
                {"toolName": "mcp__windows-mcp__powershell"},
                {"toolName": "mcp__gmail__gmail_create_draft"},
            ],
        }
    )

    assert risk["risk_level"] == "high"
    assert "gmail_draft_permission" in risk["risk_flags"]
    assert "chrome_permission_checks_skipped" in risk["risk_flags"]
    assert "broad_user_profile_scope" in risk["risk_flags"]
    assert "skill_file_missing" in risk["risk_flags"]


@pytest.mark.asyncio
async def test_legacy_scheduler_migration_imports_disabled_tasks_idempotently(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    manifest = tmp_path / "all-scheduled-tasks.json"
    manifest.write_text(
        """
        {
          "scheduledTasks": [
            {
              "id": "daily-brief",
              "cronExpression": "0 21 * * *",
              "enabled": true,
              "filePath": "C:/Users/Couch/Documents/Claude/Scheduled/daily-brief/SKILL.md",
              "model": "claude-sonnet-4-6",
              "approvedPermissions": [{"toolName": "mcp__Desktop_Commander__write_file"}]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    first = await migrate_legacy_scheduled_tasks(settings, store, [manifest])
    second = await migrate_legacy_scheduled_tasks(settings, store, [manifest])

    assert first["imported"] == 1
    assert second["imported"] == 1
    tasks = [task for task in await store.list_scheduler_tasks() if task["task_type"] == "legacy_claude_scheduled_task"]
    assert len(tasks) == 1
    assert tasks[0]["enabled"] == 0
    assert tasks[0]["review_state"] == "needs_review"
    assert tasks[0]["schedule_kind"] == "cron"
    assert tasks[0]["payload"]["cron_expression"] == "0 21 * * *"
    assert tasks[0]["payload"]["migration_policy"] == "imported_disabled_no_automatic_resurrection"
    assert "filesystem_write" in tasks[0]["payload"]["risk_flags"]
    assert tasks[0]["payload"]["risk_level"] in {"medium", "high"}


@pytest.mark.asyncio
async def test_legacy_scheduler_task_cannot_enable_without_owner_review(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    task_id = await store.upsert_scheduler_task(
        name="legacy",
        task_type="legacy_claude_scheduled_task",
        target_ref="legacy",
        payload={"legacy_id": "legacy"},
        schedule_kind="cron",
        enabled=False,
        review_state="needs_review",
    )

    with pytest.raises(PermissionError):
        await store.set_scheduler_task_enabled(task_id, True)

    task = await store.get_scheduler_task(task_id)
    assert task is not None
    assert task["enabled"] == 0
    audit = await store.list_audit_log(limit=10)
    assert any(row["action"] == "scheduler_task_enable_denied" for row in audit)


@pytest.mark.asyncio
async def test_legacy_scheduler_task_activation_records_review_and_enables(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    task_id = await store.upsert_scheduler_task(
        name="legacy",
        task_type="legacy_claude_scheduled_task",
        target_ref="legacy",
        payload={"legacy_id": "legacy"},
        schedule_kind="cron",
        enabled=False,
        review_state="needs_review",
    )

    result = await store.activate_scheduler_task(task_id, reviewer="owner", note="approved for test")

    assert result["enabled"] is True
    task = await store.get_scheduler_task(task_id)
    assert task is not None
    assert task["enabled"] == 1
    assert task["review_state"] == "owner_approved"
    assert task["reviewed_by"] == "owner"
    assert task["review_note"] == "approved for test"
