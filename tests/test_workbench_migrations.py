from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import aiosqlite
import pytest
from pydantic import BaseModel, ConfigDict

from orchestrator.config import Settings
from orchestrator.state import migration_runner as migration_module
from orchestrator.state.migration_runner import (
    MigrationApplyError,
    MigrationBackupError,
    MigrationCatalogError,
    MigrationChecksumError,
    MigrationRunner,
    split_sql_statements,
)
from orchestrator.state.store import StateStore
from orchestrator.workbench.events import EventDefinition, EventRegistry, FrameEffect
from orchestrator.workbench.models import EventActor, EventCause, EventDraft


ROOT = Path(__file__).resolve().parents[1]
V1_FIXTURE = ROOT / "tests" / "fixtures" / "state_v1.sql"
SHA_A = "a" * 64
SHA_B = "b" * 64
WORKBENCH_TABLES = {
    "workbench_events",
    "workbench_tasks",
    "workbench_branches",
    "projection_metadata",
    "frame_nodes",
    "frame_edges",
    "frame_proposals",
    "decision_requests",
    "service_runs",
    "service_run_inputs",
    "evidence_refs",
    "learning_proposals",
    "replacement_bootstrap_proofs",
    "quarantined_components",
    "workbench_command_manifests",
}


def settings_for(tmp_path: Path) -> Settings:
    home = tmp_path / "runtime"
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=ROOT,
    )


def install_v1_fixture(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.state_path) as db:
        db.executescript(V1_FIXTURE.read_text(encoding="utf-8"))


def query_all(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as db:
        return db.execute(sql, params).fetchall()


def backup_paths(settings: Settings) -> list[Path]:
    backup_dir = settings.home / "backups"
    return sorted(backup_dir.glob("state-pre-migration-*.sqlite")) if backup_dir.exists() else []


@pytest.mark.asyncio
async def test_fresh_initialize_applies_migration_and_records_immutable_catalog(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    await StateStore(settings).initialize()

    tables = {row[0] for row in query_all(settings.state_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert WORKBENCH_TABLES <= tables
    rows = query_all(
        settings.state_path,
        "SELECT version, name, status, checksum FROM schema_migrations ORDER BY version",
    )
    assert rows[0] == (1, "legacy_baseline", "applied", None)
    assert rows[1][0:3] == (2, "workbench_core", "applied")
    assert isinstance(rows[1][3], str) and len(rows[1][3]) == 64
    assert rows[2][0:3] == (3, "workbench_command_manifests", "applied")
    assert isinstance(rows[2][3], str) and len(rows[2][3]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["shape", "compatibility_commit"])
async def test_compatibility_cancellation_rolls_back_and_records_no_backup_repair(
    tmp_path: Path, phase: str
) -> None:
    class CancelCompatibility(MigrationRunner):
        async def _ensure_catalog_shape(self, db: aiosqlite.Connection) -> None:
            if phase == "shape":
                await db.execute("ALTER TABLE schema_migrations ADD COLUMN name TEXT")
                raise asyncio.CancelledError
            await super()._ensure_catalog_shape(db)

        async def _commit_compatibility(self, db: aiosqlite.Connection) -> None:
            if phase == "compatibility_commit":
                raise asyncio.CancelledError
            await super()._commit_compatibility(db)

    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(asyncio.CancelledError):
            await CancelCompatibility(settings).apply(db)
        assert not db.in_transaction

    columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(schema_migrations)")}
    assert columns == {"version", "applied_at"}
    repairs = query_all(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'",
    )
    assert len(repairs) == 1
    detail = json.loads(str(repairs[0][0]))
    assert detail["backup_published"] is False
    assert detail["backup_retained"] is False


@pytest.mark.asyncio
async def test_cancellation_while_real_compatibility_commit_is_executing_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    commit_started = asyncio.Event()
    release_commit = threading.Event()
    loop = asyncio.get_running_loop()
    commit_wait_was_cancelled = False

    async with aiosqlite.connect(settings.state_path) as db:
        original_execute = db._execute
        intercepted = False

        async def execute_with_gated_real_commit(function: object, *args: object, **kwargs: object) -> object:
            nonlocal intercepted, commit_wait_was_cancelled
            if not intercepted and getattr(function, "__name__", "") == "commit":
                intercepted = True

                def gated_real_commit() -> object:
                    loop.call_soon_threadsafe(commit_started.set)
                    if not release_commit.wait(timeout=10):
                        raise TimeoutError("test did not release the queued compatibility commit")
                    return function(*args, **kwargs)  # type: ignore[operator]

                try:
                    return await original_execute(gated_real_commit)
                except asyncio.CancelledError:
                    commit_wait_was_cancelled = True
                    raise
            return await original_execute(function, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(db, "_execute", execute_with_gated_real_commit)
        apply_task = asyncio.create_task(MigrationRunner(settings).apply(db))
        await asyncio.wait_for(commit_started.wait(), timeout=10)
        apply_task.cancel()
        release_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await apply_task
        assert commit_wait_was_cancelled is False
        assert not db.in_transaction
        await db.execute("BEGIN IMMEDIATE")
        await db.rollback()

    columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(schema_migrations)")}
    assert columns == {"version", "applied_at", "name", "status", "checksum"}
    assert query_all(
        settings.state_path,
        "SELECT version,name,status,checksum FROM schema_migrations ORDER BY version",
    ) == [(1, "legacy_baseline", "applied", None)]
    repairs = query_all(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'",
    )
    assert len(repairs) == 1
    detail = json.loads(str(repairs[0][0]))
    assert detail["backup_published"] is False
    assert detail["backup_retained"] is False


@pytest.mark.asyncio
async def test_real_compatibility_commit_failure_after_cancel_is_recorded_without_replacing_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    commit_started = asyncio.Event()
    release_commit = threading.Event()
    loop = asyncio.get_running_loop()

    async with aiosqlite.connect(settings.state_path) as db:
        original_execute = db._execute
        intercepted = False

        async def execute_with_failing_commit(function: object, *args: object, **kwargs: object) -> object:
            nonlocal intercepted
            if not intercepted and getattr(function, "__name__", "") == "commit":
                intercepted = True

                def failing_commit() -> object:
                    loop.call_soon_threadsafe(commit_started.set)
                    if not release_commit.wait(timeout=10):
                        raise TimeoutError("test did not release commit")
                    raise sqlite3.OperationalError("commit exploded after cancellation")

                return await original_execute(failing_commit)
            return await original_execute(function, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(db, "_execute", execute_with_failing_commit)
        task = asyncio.create_task(MigrationRunner(settings).apply(db))
        await asyncio.wait_for(commit_started.wait(), timeout=10)
        task.cancel("owner cancelled")
        release_commit.set()
        with pytest.raises(asyncio.CancelledError, match="owner cancelled"):
            await task
        assert not db.in_transaction

    repairs = query_all(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'",
    )
    assert len(repairs) == 1
    detail = json.loads(str(repairs[0][0]))
    assert detail["error_type"] == "CancelledError"
    assert detail["cause_type"] == "OperationalError"
    assert "commit exploded after cancellation" in detail["cause"]
    assert any("OperationalError" in note for note in detail["notes"])
    assert detail["backup_published"] is False


@pytest.mark.asyncio
async def test_repeated_cancellation_during_cleanup_drains_and_preserves_first_cancel(tmp_path: Path) -> None:
    class RepeatedCancel(MigrationRunner):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings)
            self.original = asyncio.CancelledError("first cancellation")
            self.repair_started = asyncio.Event()
            self.release_repair = asyncio.Event()

        async def _ensure_catalog_shape(self, db: aiosqlite.Connection) -> None:
            await super()._ensure_catalog_shape(db)
            raise self.original

        async def _record_repair(
            self,
            db: aiosqlite.Connection,
            version: int | None,
            exc: BaseException,
            backup_path: Path | None,
        ) -> None:
            self.repair_started.set()
            await self.release_repair.wait()
            await super()._record_repair(db, version, exc, backup_path)

    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    runner = RepeatedCancel(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        task = asyncio.create_task(runner.apply(db))
        await asyncio.wait_for(runner.repair_started.wait(), timeout=10)
        task.cancel("second cancellation")
        await asyncio.sleep(0)
        task.cancel("third cancellation")
        runner.release_repair.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value is runner.original
        assert not db.in_transaction
    repairs = query_all(
        settings.state_path,
        "SELECT COUNT(*) FROM repair_queue WHERE failure_source='state_migration'",
    )
    assert repairs == [(1,)]


@pytest.mark.asyncio
async def test_v1_upgrade_preserves_every_owner_sentinel_value(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    before = {
        "projects": query_all(settings.state_path, "SELECT * FROM projects ORDER BY id"),
        "intents": query_all(settings.state_path, "SELECT * FROM intents ORDER BY id"),
        "repair_queue": query_all(settings.state_path, "SELECT * FROM repair_queue ORDER BY id"),
    }

    await StateStore(settings).initialize()

    after = {
        "projects": query_all(settings.state_path, "SELECT * FROM projects ORDER BY id"),
        "intents": query_all(settings.state_path, "SELECT * FROM intents ORDER BY id"),
        "repair_queue": query_all(settings.state_path, "SELECT * FROM repair_queue ORDER BY id"),
    }
    assert after == before


@pytest.mark.asyncio
async def test_second_initialize_is_idempotent_and_creates_no_backup(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    initial_backups = backup_paths(settings)

    await store.initialize()

    assert backup_paths(settings) == initial_backups
    assert query_all(settings.state_path, "SELECT COUNT(*) FROM schema_migrations WHERE version=2") == [(1,)]


@pytest.mark.asyncio
async def test_migration_schema_has_required_constraints_indexes_and_json_guards(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()

    event_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(workbench_events)")}
    assert {
        "event_id", "task_id", "sequence", "event_type", "event_schema_version", "actor_kind", "actor_id", "branch_id",
        "cause", "caused_by", "command_id", "command_sequence", "frame_version", "payload_json",
        "idempotency_key", "prior_checksum", "checksum", "created_at",
    } <= event_columns
    event_not_null = {row[1]: row[3] for row in query_all(settings.state_path, "PRAGMA table_info(workbench_events)")}
    assert event_not_null["prior_checksum"] == 1
    projection_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(projection_metadata)")}
    assert {
        "task_id", "projection_name", "branch_id", "head_sequence", "head_event_checksum",
        "canonical_state_json", "state_checksum", "updated_at",
    } <= projection_columns
    projection_pk = [row[1] for row in query_all(settings.state_path, "PRAGMA table_info(projection_metadata)") if row[5]]
    assert projection_pk == ["task_id", "projection_name", "branch_id"]
    for table in ("frame_nodes", "frame_edges", "frame_proposals", "decision_requests", "service_runs"):
        assert "branch_id" in {row[1] for row in query_all(settings.state_path, f"PRAGMA table_info({table})")}
    task_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(workbench_tasks)")}
    assert "state" in task_columns and "status" not in task_columns
    node_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(frame_nodes)")}
    assert {"node_id", "node_key", "supersedes_node_id", "kind", "text"} <= node_columns
    decision_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(decision_requests)")}
    assert {
        "decision_id", "state", "tier", "queue_order", "question", "free_form_allowed", "recommendation",
        "resolution_text", "outcome_deltas_json", "materiality_json",
    } <= decision_columns
    decision_indexes = {row[1] for row in query_all(settings.state_path, "PRAGMA index_list(decision_requests)")}
    assert {"idx_decision_requests_one_active", "idx_decision_requests_open_queue_order"} <= decision_indexes
    service_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(service_runs)")}
    assert {"run_id", "service", "role", "frame_version", "state", "receipt_event_id"} <= service_columns
    proposal_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(frame_proposals)")}
    assert {"base_head_checksum", "preview_state_checksum", "expected_head_sequence"} <= proposal_columns
    branch_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(workbench_branches)")}
    assert {"parent_branch_id", "forked_from_sequence", "forked_from_frame_version"} <= branch_columns

    index_names = {row[1] for row in query_all(settings.state_path, "PRAGMA index_list(frame_edges)")}
    assert {"idx_frame_edges_from", "idx_frame_edges_to"} <= index_names
    input_indexes = {row[1] for row in query_all(settings.state_path, "PRAGMA index_list(service_run_inputs)")}
    assert "idx_service_run_inputs_node" in input_indexes
    input_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(service_run_inputs)")}
    assert {"node_id", "input_frame_version"} <= input_columns

    with sqlite3.connect(settings.state_path) as db:
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('t','T','running','main',0,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) VALUES ('t','main','active','now')"
        )
        insert_single_event_manifest(db, "t", "cmd", "main", 1, "e")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
                "command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at) "
                "VALUES ('e','t',1,'x',1,'service','a','main','cmd',1,0,'not-json','k','','c','now')"
            )
        db.rollback()


@pytest.mark.asyncio
async def test_event_rows_are_append_only_and_cause_must_be_earlier_in_same_task(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('a','A','running','main',0,'now','now'),('b','B','running','main',0,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('a','main','active','now'),('b','main','active','now')"
        )
        insert_single_event_manifest(db, "a", "cmd-a1", "main", 1, "a1")
        db.execute(
            "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
            "command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at) "
            "VALUES ('a1','a',1,'x',1,'service','actor','main','cmd-a1',1,0,'{}','a1','','c1','now')"
        )
        insert_single_event_manifest(db, "b", "cmd-b1", "main", 1, "b1")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
                "cause,caused_by,command_id,command_sequence,frame_version,payload_json,idempotency_key,checksum,created_at) "
                "VALUES ('b1','b',1,'x',1,'service','actor','main','bad','a1','cmd-b1',1,0,'{}','b1','c2','now')"
            )
        db.execute(
            "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
            "command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at) "
            "VALUES ('b1','b',1,'x',1,'service','actor','main','cmd-b1',1,0,'{}','b1','','c2','now')"
        )
        insert_single_event_manifest(db, "a", "cmd-a2", "main", 2, "a2")
        db.execute(
            "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
            "cause,caused_by,command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,"
            "checksum,created_at) VALUES ('a2','a',2,'x',1,'service','actor','main','valid','a1','cmd-a2',1,0,'{}','a2','c1','c2','now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE workbench_events SET actor_id='changed' WHERE event_id='a1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM workbench_events WHERE event_id='a1'")


@pytest.mark.asyncio
async def test_frame_node_revisions_cannot_be_rewritten_or_deleted(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('t','Task','running','main',1,'now','now')"
        )
        db.execute("INSERT INTO workbench_branches(task_id,branch_id,status,created_at) VALUES ('t','main','active','now')")
        db.execute(
            "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,kind,text,status,provenance_json,"
            "created_at,updated_at) VALUES ('n1','t','main',1,'goal','goal','Original','confirmed','{}','now','now')"
        )

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE frame_nodes SET text='rewritten' WHERE node_id='n1'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM frame_nodes WHERE node_id='n1'")


@pytest.mark.asyncio
async def test_active_branch_must_exist_for_the_same_task(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('dangling','Task','running','missing',0,'now','now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.commit()
        db.rollback()

        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('a','A','running','main',0,'now','now'),('b','B','running','other',0,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('a','main','active','now'),('b','other','active','now')"
        )
        db.commit()
        db.execute("UPDATE workbench_tasks SET active_branch_id='other' WHERE id='a'")
        with pytest.raises(sqlite3.IntegrityError):
            db.commit()
        db.rollback()


@pytest.mark.asyncio
async def test_branch_ancestry_is_complete_acyclic_immutable_and_append_only(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('branches','Branches','running','main',2,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('branches','main','active','now')"
        )
        for sql in (
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,status,created_at) "
            "VALUES ('branches','partial-parent','main','active','now')",
            "INSERT INTO workbench_branches(task_id,branch_id,forked_from_sequence,forked_from_frame_version,status,created_at) "
            "VALUES ('branches','partial-cutoff',1,1,'active','now')",
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,forked_from_sequence,"
            "forked_from_frame_version,status,created_at) VALUES "
            "('branches','self','self',1,1,'active','now')",
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,forked_from_sequence,"
            "forked_from_frame_version,status,created_at) VALUES "
            "('branches','cycle-a','cycle-b',1,1,'active','now'),"
            "('branches','cycle-b','cycle-a',1,1,'active','now')",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,forked_from_sequence,"
            "forked_from_frame_version,status,created_at) "
            "VALUES ('branches','child','main',1,1,'active','now')"
        )
        for sql in (
            "UPDATE workbench_branches SET parent_branch_id=NULL WHERE task_id='branches' AND branch_id='child'",
            "UPDATE workbench_branches SET forked_from_sequence=99 WHERE task_id='branches' AND branch_id='child'",
            "UPDATE workbench_branches SET forked_from_frame_version=99 WHERE task_id='branches' AND branch_id='child'",
            "UPDATE workbench_branches SET created_at='rewritten' WHERE task_id='branches' AND branch_id='child'",
            "DELETE FROM workbench_branches WHERE task_id='branches' AND branch_id='child'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
        db.execute(
            "UPDATE workbench_branches SET status='abandoned' WHERE task_id='branches' AND branch_id='child'"
        )
        assert db.execute(
            "SELECT status,parent_branch_id,forked_from_sequence,forked_from_frame_version "
            "FROM workbench_branches WHERE task_id='branches' AND branch_id='child'"
        ).fetchone() == ("abandoned", "main", 1, 1)


@pytest.mark.asyncio
async def test_edges_and_service_inputs_enforce_type_task_and_frame(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('a','A','running','main',2,'now','now'),('b','B','running','main',2,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('a','main','active','now'),('b','main','active','now')"
        )
        for node_id, task_id in (("a1", "a"), ("a2", "a"), ("b1", "b")):
            db.execute(
                "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,kind,text,status,provenance_json,"
                "created_at,updated_at) VALUES (?,?, 'main',2,?,'goal',?,'confirmed','{}','now','now')",
                (node_id, task_id, node_id, node_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO frame_edges(id,task_id,branch_id,frame_version,from_node_id,to_node_id,edge_type,created_at) "
                "VALUES ('bad-type','a','main',2,'a1','a2','contains','now')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO frame_edges(id,task_id,branch_id,frame_version,from_node_id,to_node_id,edge_type,created_at) "
                "VALUES ('cross-task','a','main',2,'a1','b1','supports','now')"
            )
        db.execute(
            "INSERT INTO service_runs(run_id,task_id,service,role,frame_version,branch_id,state,updated_at) "
            "VALUES ('r','a','codex','implementer',2,'main','running','now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO service_run_inputs(task_id,run_id,node_id,input_frame_version,ordinal) "
                "VALUES ('a','r','a1',1,0)"
            )
        db.execute(
            "INSERT INTO service_run_inputs(task_id,run_id,node_id,input_frame_version,ordinal) "
            "VALUES ('a','r','a1',2,0)"
        )


@pytest.mark.asyncio
async def test_service_lifecycle_and_decision_kind_are_structured(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    decision_columns = {row[1] for row in query_all(settings.state_path, "PRAGMA table_info(decision_requests)")}
    assert "kind" in decision_columns

    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('t','Task','running','main',1,'now','now')"
        )
        db.execute("INSERT INTO workbench_branches(task_id,branch_id,status,created_at) VALUES ('t','main','active','now')")
        for ordinal, state in enumerate(("starting", "interrupted", "canceled")):
            db.execute(
                "INSERT INTO service_runs(run_id,task_id,service,role,frame_version,branch_id,state,updated_at) "
                "VALUES (?,?, 'codex','implementer',1,'main',?,'now')",
                (f"run-{ordinal}", "t", state),
            )
        decision_kinds = (
            "intent_clarification",
            "tool_approval",
            "external_action_approval",
            "evidence_checkpoint",
            "service_team_override",
            "frame_interpretation_confirmation",
        )
        for ordinal, kind in enumerate(decision_kinds):
            db.execute(
                """
                INSERT INTO decision_requests(
                    decision_id,task_id,branch_id,state,tier,kind,queue_order,semantic_identity,question,
                    affected_node_ids_json,options_json,free_form_allowed,changed_outcome,outcome_deltas_json,
                    materiality_json,consequence_if_unresolved,consequence_json,provenance_json,created_at
                ) VALUES (?, 't','main','queued','routine',?,?,?,'Question?','[]','[]',1,'outcome','{}','{}',
                          'consequence','{}','{}','now')
                """,
                (f"decision-{ordinal}", kind, ordinal, f"semantic-{ordinal}"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO decision_requests(
                    decision_id,task_id,branch_id,state,tier,kind,queue_order,semantic_identity,question,
                    affected_node_ids_json,options_json,free_form_allowed,changed_outcome,outcome_deltas_json,
                    materiality_json,consequence_if_unresolved,consequence_json,provenance_json,created_at
                ) VALUES ('bad-kind','t','main','resolved','routine','clarification',99,'bad','Question?',
                          '[]','[]',1,'outcome','{}','{}','consequence','{}','{}','now')
                """
            )
@pytest.mark.asyncio
async def test_service_runs_store_exact_native_recovery_identity(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('t','Task','running','main',1,'now','now')"
        )
        db.execute("INSERT INTO workbench_branches(task_id,branch_id,status,created_at) VALUES ('t','main','active','now')")
        db.execute(
            """
            INSERT INTO service_runs(
                run_id,task_id,service,role,frame_version,branch_id,state,adapter_provider,
                adapter_contract_revision,account_id,profile_id,model_id,capability_inventory_revision,
                transport_generation,native_session_id,native_thread_id,native_turn_id,native_request_id,
                native_tool_use_id,native_question_group_id,launch_origin,native_handle_json,updated_at
            ) VALUES (
                'run-1','t','claude','researcher',1,'main','waiting_owner','anthropic','contract-v3',
                'acct-1','profile-1','claude-opus','cap-v7','transport-v2','session-1','thread-1',
                'turn-1','request-1','tool-1','question-group-1','governed','{"opaque":"handle-1"}','now'
            )
            """
        )
        row = db.execute(
            """
            SELECT adapter_provider,adapter_contract_revision,account_id,profile_id,model_id,
                   capability_inventory_revision,transport_generation,native_session_id,native_thread_id,
                   native_turn_id,native_request_id,native_tool_use_id,native_question_group_id,launch_origin,
                   native_handle_json
            FROM service_runs WHERE run_id='run-1'
            """
        ).fetchone()
        assert row == (
            "anthropic", "contract-v3", "acct-1", "profile-1", "claude-opus", "cap-v7", "transport-v2",
            "session-1", "thread-1", "turn-1", "request-1", "tool-1", "question-group-1", "governed",
            '{"opaque":"handle-1"}',
        )
        db.execute(
            """
            INSERT INTO service_runs(
                run_id,task_id,service,role,frame_version,branch_id,state,adapter_provider,
                adapter_contract_revision,account_id,profile_id,model_id,capability_inventory_revision,
                transport_generation,native_session_id,native_thread_id,native_turn_id,native_request_id,
                native_tool_use_id,native_question_group_id,launch_origin,updated_at
            ) VALUES ('run-2','t','claude','researcher',1,'main','running','anthropic','contract-v3',
                      'acct-1','profile-1','claude-opus','cap-v7','transport-v2','session-1','thread-1',
                      'turn-2','request-2','tool-1','question-group-1','governed','now')
            """
        )
        assert db.execute(
            "SELECT native_request_id,native_tool_use_id,native_question_group_id FROM service_runs "
            "WHERE run_id='run-2'"
        ).fetchone() == ("request-2", "tool-1", "question-group-1")
        exact = (
            "SELECT COUNT(*) FROM service_runs WHERE adapter_provider=? AND account_id=? AND profile_id=? "
            "AND model_id=? AND native_thread_id=? AND native_turn_id=? AND native_request_id=?"
        )
        identity = ("anthropic", "acct-1", "profile-1", "claude-opus", "thread-1", "turn-1", "request-1")
        assert db.execute(exact, identity).fetchone() == (1,)
        for index in range(len(identity)):
            mismatch = list(identity)
            mismatch[index] = "mismatch"
            assert db.execute(exact, tuple(mismatch)).fetchone() == (0,)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO service_runs(
                    run_id,task_id,service,role,frame_version,branch_id,state,adapter_provider,
                    adapter_contract_revision,account_id,profile_id,model_id,capability_inventory_revision,
                    transport_generation,native_session_id,native_thread_id,native_turn_id,native_request_id,
                    launch_origin,updated_at
                ) VALUES ('run-duplicate','t','claude','reviewer',1,'main','running','anthropic','contract-v3',
                          'acct-1','profile-1','claude-opus','cap-v7','transport-v2','session-1','thread-1',
                          'turn-1','request-1','governed','now')
                """
            )
        for suffix, tool_use_id, question_group_id in (
            ("tool-without-request", "tool-orphan", None),
            ("question-without-request", None, "question-orphan"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    """
                    INSERT INTO service_runs(
                        run_id,task_id,service,role,frame_version,branch_id,state,adapter_provider,
                        adapter_contract_revision,account_id,profile_id,model_id,capability_inventory_revision,
                        transport_generation,native_session_id,native_thread_id,native_turn_id,native_tool_use_id,
                        native_question_group_id,launch_origin,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"run-{suffix}", "t", "claude", "reviewer", 1, "main", "running", "anthropic",
                        "contract-v3", "acct-1", "profile-1", "claude-opus", "cap-v7", "transport-v2",
                        f"session-{suffix}", "thread-chain", "turn-chain", tool_use_id, question_group_id,
                        "governed", "now",
                    ),
                )
        for column, request_id, tool_use_id, question_group_id in (
            ("request", " ", None, None),
            ("tool", "request-blank-tool", " ", None),
            ("question", "request-blank-question", None, " "),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    """
                    INSERT INTO service_runs(
                        run_id,task_id,service,role,frame_version,branch_id,state,adapter_provider,
                        adapter_contract_revision,account_id,profile_id,model_id,capability_inventory_revision,
                        transport_generation,native_session_id,native_thread_id,native_turn_id,native_request_id,
                        native_tool_use_id,native_question_group_id,launch_origin,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"run-blank-{column}", "t", "claude", "reviewer", 1, "main", "running", "anthropic",
                        "contract-v3", "acct-1", "profile-1", "claude-opus", "cap-v7", "transport-v2",
                        f"session-blank-{column}", "thread-blank", "turn-blank", request_id, tool_use_id,
                        question_group_id, "governed", "now",
                    ),
                )
        required_envelope = (
            "adapter_provider", "adapter_contract_revision", "account_id", "profile_id", "model_id",
            "capability_inventory_revision", "transport_generation", "native_session_id", "native_thread_id",
            "launch_origin",
        )
        base_envelope: dict[str, object] = {
            "adapter_provider": "anthropic",
            "adapter_contract_revision": "contract-v3",
            "account_id": "acct-1",
            "profile_id": "profile-1",
            "model_id": "claude-opus",
            "capability_inventory_revision": "cap-v7",
            "transport_generation": "transport-v2",
            "native_session_id": "session-envelope",
            "native_thread_id": "thread-envelope",
            "launch_origin": "governed",
        }
        for column in required_envelope:
            for label, missing in (("null", None), ("blank", " ")):
                envelope = dict(base_envelope)
                envelope[column] = missing
                if column != "native_session_id":
                    envelope["native_session_id"] = f"session-{column}-{label}"
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(
                        """
                        INSERT INTO service_runs(
                            run_id,task_id,service,role,frame_version,branch_id,state,adapter_provider,
                            adapter_contract_revision,account_id,profile_id,model_id,
                            capability_inventory_revision,transport_generation,native_session_id,native_thread_id,
                            launch_origin,native_handle_json,updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            f"missing-{column}-{label}", "t", "claude", "reviewer", 1, "main", "running",
                            envelope["adapter_provider"], envelope["adapter_contract_revision"],
                            envelope["account_id"], envelope["profile_id"], envelope["model_id"],
                            envelope["capability_inventory_revision"], envelope["transport_generation"],
                            envelope["native_session_id"], envelope["native_thread_id"],
                            envelope["launch_origin"], "{}", "now",
                        ),
                    )
        for suffix in ("one", "two"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    """
                    INSERT INTO service_runs(
                        run_id,task_id,service,role,frame_version,branch_id,state,adapter_provider,
                        adapter_contract_revision,account_id,profile_id,model_id,capability_inventory_revision,
                        transport_generation,native_session_id,native_thread_id,native_request_id,
                        launch_origin,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"run-null-turn-{suffix}", "t", "claude", "reviewer", 1, "main", "running",
                        "anthropic", "contract-v3", "acct-1", "profile-1", "claude-opus", "cap-v7",
                        "transport-v2", f"session-null-turn-{suffix}", "thread-null-turn",
                        "request-null-turn", "governed", "now",
                    ),
                )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO service_runs(
                    run_id,task_id,service,role,frame_version,branch_id,state,native_handle_json,updated_at
                ) VALUES ('run-handle-only','t','claude','reviewer',1,'main','running','{"opaque":"orphan"}','now')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO service_runs(
                    run_id,task_id,service,role,frame_version,branch_id,state,native_request_id,updated_at
                ) VALUES ('run-incomplete','t','claude','reviewer',1,'main','running','request-without-identity','now')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO service_runs(
                    run_id,task_id,service,role,frame_version,branch_id,state,launch_origin,native_handle_json,updated_at
                ) VALUES ('run-invalid','t','claude','reviewer',1,'main','running','unknown','not-json','now')
                """
            )


@pytest.mark.asyncio
async def test_completion_evidence_is_structured_same_task_and_round_trips(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('a','A','verifying','main',3,'now','now'),('b','B','running','main',3,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('a','main','active','now'),('b','main','active','now')"
        )
        insert_single_event_manifest(db, "a", "cmd-a", "main", 1, "event-a", frame_version=3)
        db.execute(
            """
            INSERT INTO workbench_events(
                event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at
            ) VALUES ('event-a','a',1,'evidence.observed',1,'service','run-a','main','cmd-a',1,3,'{}','idem-a','','sum-a','now')
            """
        )
        insert_single_event_manifest(db, "b", "cmd-b", "main", 1, "event-b", frame_version=3)
        db.execute(
            """
            INSERT INTO workbench_events(
                event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at
            ) VALUES ('event-b','b',1,'evidence.observed',1,'service','run-b','main','cmd-b',1,3,'{}',
                      'idem-b','','sum-b','now')
            """
        )
        insert_single_event_manifest(
            db, "a", "cmd-a-invalidated", "main", 2, "event-a-invalidated",
            frame_version=3, created_at="later",
        )
        db.execute(
            """
            INSERT INTO workbench_events(
                event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at
            ) VALUES ('event-a-invalidated','a',2,'evidence.invalidated',1,'service','run-a','main',
                      'cmd-a-invalidated',1,3,'{}','idem-a-invalidated','sum-a','sum-a-invalidated','later')
            """
        )
        db.execute(
            "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,kind,text,status,provenance_json,"
            "created_by_event_id,created_at,updated_at) VALUES "
            "('success-a','a','main',3,'success','success','Complete','confirmed','{}','event-a','now','now')"
        )
        db.execute(
            "INSERT INTO service_runs(run_id,task_id,service,role,frame_version,branch_id,state,updated_at) "
            "VALUES ('run-a','a','codex','verifier',3,'main','verifying','now')"
        )
        db.execute(
            """
            INSERT INTO evidence_refs(
                id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,
                observed_head_event_id,observed_head_sequence,predicate_id,
                predicate_text,predicate_json,expected_outcome,authority_kind,authority_locator,verifier_kind,
                verifier_identity,verification_status,observed_at,valid_until,observed_value_checksum,
                content_checksum,source_event_id,source_event_sequence,producing_run_id,metadata_json
            ) VALUES (
                'evidence-a','a','main','success-a',3,3,'event-a',1,'predicate-1','Output exists',
                '{"op":"exists"}','present','filesystem','C:/proof/result.json','script','verify-result-v1',
                'pass','2026-07-13T20:00:00Z','2026-07-14T20:00:00Z',?, ?,
                'event-a',1,'run-a','{"receipt":"r1"}'
            )
            """,
            (SHA_A, SHA_B),
        )
        row = db.execute(
            """
            SELECT branch_id,success_node_id,success_node_frame_version,frame_version,observed_head_event_id,
                   observed_head_sequence,predicate_id,predicate_text,
                   predicate_json,expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                   verification_status,observed_at,valid_until,observed_value_checksum,content_checksum,
                   source_event_id,source_event_sequence,producing_run_id,metadata_json
            FROM evidence_refs WHERE id='evidence-a'
            """
        ).fetchone()
        assert row[0:8] == ("main", "success-a", 3, 3, "event-a", 1, "predicate-1", "Output exists")
        assert row[14] == "pass"
        assert row[-2:] == ("run-a", '{"receipt":"r1"}')
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE evidence_refs SET predicate_text='rewritten' WHERE id='evidence-a'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM evidence_refs WHERE id='evidence-a'")
        db.execute(
            """
            UPDATE evidence_refs
            SET invalidated_at='later',invalidated_event_id='event-a-invalidated',
                invalidated_event_sequence=2,invalidation_reason='superseded'
            WHERE id='evidence-a'
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE evidence_refs SET invalidation_reason='rewritten' WHERE id='evidence-a'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,
                    observed_head_event_id,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,metadata_json
                ) VALUES ('cross','b','main','success-a',3,3,'event-b',1,'p','ok','filesystem','x','script','v',
                          'pass','now',?,'{}')
                """
                , (SHA_A,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,
                    observed_head_event_id,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,metadata_json
                ) VALUES ('wrong-frame','a','main','success-a',2,3,'event-a',1,'p','ok','filesystem','x','script','v',
                          'pass','now',?,'{}')
                """
                , (SHA_A,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,
                    observed_head_event_id,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,invalidated_at,metadata_json
                ) VALUES ('partial-invalidation','a','main','success-a',3,3,'event-a',1,'p','ok','filesystem',
                          'x','script','v',
                          'pass','now',?,'now','{}')
                """
                , (SHA_A,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,
                    observed_head_event_id,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,metadata_json
                ) VALUES ('bad-status','a','main','success-a',3,3,'event-a',1,'p','ok','filesystem','x','script','v',
                          'unverified','now',?,'{}')
                """
                , (SHA_A,)
            )
        evidence_values: dict[str, object] = {
            "id": "shape", "task_id": "a", "branch_id": "main", "success_node_id": "success-a",
            "success_node_frame_version": 3, "frame_version": 3, "observed_head_event_id": "event-a",
            "observed_head_sequence": 1, "predicate_id": "predicate-shape", "predicate_text": "exists",
            "expected_outcome": "present", "authority_kind": "filesystem", "authority_locator": "C:/proof",
            "verifier_kind": "script", "verifier_identity": "verifier-v1", "verification_status": "pass",
            "observed_at": "now", "observed_value_checksum": SHA_A, "content_checksum": SHA_B,
            "metadata_json": "{}",
        }
        evidence_insert = """
            INSERT INTO evidence_refs(
                id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,
                observed_head_event_id,observed_head_sequence,predicate_id,predicate_text,expected_outcome,
                authority_kind,authority_locator,verifier_kind,verifier_identity,verification_status,
                observed_at,observed_value_checksum,content_checksum,metadata_json
            ) VALUES (
                :id,:task_id,:branch_id,:success_node_id,:success_node_frame_version,:frame_version,
                :observed_head_event_id,:observed_head_sequence,:predicate_id,:predicate_text,:expected_outcome,
                :authority_kind,:authority_locator,:verifier_kind,:verifier_identity,:verification_status,
                :observed_at,:observed_value_checksum,:content_checksum,:metadata_json
            )
        """
        for field in (
            "predicate_id", "expected_outcome", "authority_kind", "authority_locator",
            "verifier_kind", "verifier_identity",
        ):
            candidate = dict(evidence_values)
            candidate["id"] = f"blank-{field}"
            candidate[field] = " "
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(evidence_insert, candidate)
        for field, value in (
            ("observed_value_checksum", "bad"), ("observed_value_checksum", SHA_A.upper()),
            ("content_checksum", "bad"), ("content_checksum", SHA_B.upper()),
        ):
            candidate = dict(evidence_values)
            candidate["id"] = f"bad-{field}-{value[:3]}"
            candidate[field] = value
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(evidence_insert, candidate)


@pytest.mark.asyncio
async def test_completion_evidence_rejects_nonexistent_wrong_kind_and_invisible_lineage(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('lineage','Lineage','verifying','child',3,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('lineage','main','active','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,forked_from_sequence,"
            "forked_from_frame_version,status,created_at) VALUES "
            "('lineage','child','main',3,3,'active','now'),"
            "('lineage','sibling','main',1,3,'active','now')"
        )
        events = (
            ("main-success-event", 1, 2, "frame.success.confirmed", "main", "{}"),
            ("sibling-event", 2, 3, "frame.success.confirmed", "sibling", "{}"),
            ("visible-source-event", 3, 3, "evidence.source.captured", "main", "{}"),
            ("child-head-event", 4, 3, "evidence.observed", "child", "{}"),
            ("late-main-event", 5, 3, "frame.success.confirmed", "main", "{}"),
            ("child-invalidation-event", 6, 3, "evidence.invalidated", "child", "{}"),
            ("sibling-invalidation-event", 7, 3, "evidence.invalidated", "sibling", "{}"),
        )
        event_sequences = {event_id: sequence for event_id, sequence, _, _, _, _ in events}
        for event_id, sequence, event_frame_version, event_type, branch_id, payload in events:
            insert_single_event_manifest(
                db, "lineage", f"cmd-{event_id}", branch_id, sequence, event_id,
                frame_version=event_frame_version,
            )
            db.execute(
                """
                INSERT INTO workbench_events(
                    event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                    command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
                    checksum,created_at
                    ) VALUES (?,?,?,?,1,'service','verifier',?,?,1,?, ?,?,?,?,'now')
                """,
                (
                    event_id, "lineage", sequence, event_type, branch_id, f"cmd-{event_id}",
                    event_frame_version, payload,
                    f"idem-{event_id}", f"prior-{sequence}", f"sum-{sequence}",
                ),
            )
        nodes = (
            ("success-main", "main", 2, "success-main", "success", "main-success-event"),
            ("wrong-kind", "child", 3, "wrong-kind", "action", "child-head-event"),
            ("success-sibling", "sibling", 3, "success-sibling", "success", "sibling-event"),
            ("success-late-main", "main", 3, "success-late-main", "success", "late-main-event"),
            ("success-unanchored", "child", 3, "success-unanchored", "success", None),
        )
        for node_id, branch_id, node_frame_version, node_key, kind, created_by in nodes:
            db.execute(
                """
                INSERT INTO frame_nodes(
                    node_id,task_id,branch_id,frame_version,node_key,kind,text,status,provenance_json,
                    created_by_event_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,'node','confirmed','{}',?,'now','now')
                """,
                (node_id, "lineage", branch_id, node_frame_version, node_key, kind, created_by),
            )
        db.execute(
            "INSERT INTO service_runs(run_id,task_id,service,role,frame_version,branch_id,state,updated_at) "
            "VALUES ('lineage-run','lineage','codex','verifier',3,'child','verifying','now')"
        )

        def insert_evidence(
            evidence_id: str,
            node_id: str = "success-main",
            success_node_frame_version: int = 2,
            head_event_id: str = "child-head-event",
            head_sequence: int = 4,
            source_event_id: str = "visible-source-event",
            invalidated_event_id: str | None = None,
        ) -> None:
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,
                    observed_head_event_id,observed_head_sequence,predicate_id,
                    predicate_text,expected_outcome,authority_kind,authority_locator,verifier_kind,
                    verifier_identity,verification_status,observed_at,observed_value_checksum,source_event_id,
                    source_event_sequence,producing_run_id,invalidated_at,invalidated_event_id,
                    invalidated_event_sequence,invalidation_reason,metadata_json
                ) VALUES (?,?,?,?,?,3,?,?,'predicate','exists','present','filesystem','proof','script','verifier',
                          'pass','now',?, ?,?,'lineage-run',?,?,?,?,'{}')
                """,
                (
                    evidence_id, "lineage", "child", node_id, success_node_frame_version,
                    head_event_id, head_sequence, SHA_A, source_event_id, event_sequences[source_event_id],
                    "later" if invalidated_event_id else None, invalidated_event_id,
                    event_sequences[invalidated_event_id] if invalidated_event_id else None,
                    "superseded" if invalidated_event_id else None,
                ),
            )

        insert_evidence("valid-ancestor")
        insert_evidence("valid-invalidation", invalidated_event_id="child-invalidation-event")
        for evidence_id, node_id, node_frame, head_event, head, source, invalidation in (
            ("nonexistent-head", "success-main", 2, "child-head-event", 99, "visible-source-event", None),
            ("wrong-head-branch", "success-main", 2, "sibling-event", 2, "visible-source-event", None),
            ("wrong-kind", "wrong-kind", 3, "child-head-event", 4, "visible-source-event", None),
            ("sibling-node", "success-sibling", 3, "child-head-event", 4, "visible-source-event", None),
            ("late-ancestor-node", "success-late-main", 3, "child-head-event", 4, "visible-source-event", None),
            ("unanchored-node", "success-unanchored", 3, "child-head-event", 4, "visible-source-event", None),
            ("sibling-source", "success-main", 2, "child-head-event", 4, "sibling-event", None),
            ("late-ancestor-source", "success-main", 2, "child-head-event", 4, "late-main-event", None),
            ("source-after-head", "success-main", 2, "child-head-event", 4, "child-invalidation-event", None),
            ("sibling-invalidation", "success-main", 2, "child-head-event", 4, "visible-source-event", "sibling-invalidation-event"),
            ("early-invalidation", "success-main", 2, "child-head-event", 4, "visible-source-event", "visible-source-event"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                insert_evidence(evidence_id, node_id, node_frame, head_event, head, source, invalidation)
        for sql in (
            "UPDATE workbench_branches SET forked_from_sequence=99 "
            "WHERE task_id='lineage' AND branch_id='child'",
            "UPDATE workbench_branches SET parent_branch_id='sibling' "
            "WHERE task_id='lineage' AND branch_id='child'",
            "DELETE FROM workbench_branches WHERE task_id='lineage' AND branch_id='child'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)


@pytest.mark.asyncio
async def test_quarantine_stages_before_activation_and_requires_replacement_proof(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('quarantine-task','Quarantine','repairing','main',1,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('quarantine-task','main','active','now')"
        )
        stage_payload = json.dumps(
            {
                "component_type": "scheduled_task",
                "component_id": "legacy-pinger",
                "replacement_build_id": "build-2",
                "manifest_checksum": "manifest-sum",
            },
            sort_keys=True,
        )
        proof_json = json.dumps({"validated": True}, sort_keys=True, separators=(",", ":"))
        proof_checksum = hashlib.sha256(proof_json.encode("utf-8")).hexdigest()
        proof_fields = {
            "proof_id": "proof-1",
            "receipt_id": "receipt-1",
            "component_type": "scheduled_task",
            "component_id": "legacy-pinger",
            "replacement_build_id": "build-2",
            "proof_kind": "live_bootstrap",
            "status": "succeeded",
            "proof_json": json.loads(proof_json),
            "proof_checksum": proof_checksum,
            "authority_kind": "runtime",
            "authority_locator": "receipt://one",
            "verifier_kind": "script",
            "verifier_identity": "verifier",
        }
        proof_payload = json.dumps(proof_fields, sort_keys=True)
        wrong_type_fields = dict(proof_fields)
        wrong_type_json = "{}"
        wrong_type_checksum = hashlib.sha256(wrong_type_json.encode("utf-8")).hexdigest()
        wrong_type_fields.update(
            proof_id="wrong-type-proof", receipt_id="wrong-type", proof_json={},
            proof_checksum=wrong_type_checksum, authority_locator="receipt://wrong",
        )
        wrong_type_payload = json.dumps(wrong_type_fields, sort_keys=True)
        activation_fields = {
            key: proof_fields[key]
            for key in (
                "proof_id", "receipt_id", "component_type", "component_id", "replacement_build_id",
                "proof_checksum",
            )
        }
        activation_payload = json.dumps(activation_fields, sort_keys=True)
        wrong_activation_payload = json.dumps(
            {
                **activation_fields,
                "replacement_build_id": "wrong-build",
            },
            sort_keys=True,
        )
        alternate_proof_json = json.dumps({"validated": "alternate"}, sort_keys=True, separators=(",", ":"))
        alternate_proof_checksum = hashlib.sha256(alternate_proof_json.encode("utf-8")).hexdigest()
        alternate_fields = {
            **proof_fields,
            "proof_id": "proof-alternate",
            "receipt_id": "receipt-alternate",
            "component_id": "other-pinger",
            "replacement_build_id": "other-build",
            "proof_json": json.loads(alternate_proof_json),
            "proof_checksum": alternate_proof_checksum,
            "authority_locator": "receipt://alternate",
        }
        alternate_activation_payload = json.dumps(
            {key: alternate_fields[key] for key in (
                "proof_id", "receipt_id", "component_type", "component_id", "replacement_build_id",
                "proof_checksum",
            )},
            sort_keys=True,
        )
        q2_stage_payload = json.dumps(
            {
                "component_type": "scheduled_task",
                "component_id": "legacy-pinger",
                "replacement_build_id": "build-3",
                "manifest_checksum": "sum-2",
            },
            sort_keys=True,
        )
        q2_proof_json = json.dumps({"validated": "build-3"}, sort_keys=True, separators=(",", ":"))
        q2_proof_checksum = hashlib.sha256(q2_proof_json.encode("utf-8")).hexdigest()
        q2_proof_fields = {
            **proof_fields,
            "proof_id": "proof-2",
            "receipt_id": "receipt-2",
            "replacement_build_id": "build-3",
            "proof_json": json.loads(q2_proof_json),
            "proof_checksum": q2_proof_checksum,
            "authority_locator": "receipt://two",
        }
        q2_activation_payload = json.dumps(
            {key: q2_proof_fields[key] for key in (
                "proof_id", "receipt_id", "component_type", "component_id", "replacement_build_id",
                "proof_checksum",
            )},
            sort_keys=True,
        )
        for event_id, sequence, event_type, payload in (
            ("stage-event", 1, "component.quarantine_staged", stage_payload),
            ("proof-event", 2, "component.replacement_bootstrap_succeeded", proof_payload),
            ("wrong-proof-event", 3, "component.health_observed", wrong_type_payload),
            ("activation-event", 4, "component.quarantine_activated", activation_payload),
            ("wrong-activation-event", 5, "component.quarantine_activated", wrong_activation_payload),
            ("q2-stage-event", 6, "component.quarantine_staged", q2_stage_payload),
            ("alternate-proof-event", 7, "component.replacement_bootstrap_succeeded",
             json.dumps(alternate_fields, sort_keys=True)),
            ("alternate-activation-event", 8, "component.quarantine_activated", alternate_activation_payload),
            ("q2-proof-event", 9, "component.replacement_bootstrap_succeeded",
             json.dumps(q2_proof_fields, sort_keys=True)),
            ("q2-activation-event", 10, "component.quarantine_activated", q2_activation_payload),
        ):
            insert_single_event_manifest(
                db, "quarantine-task", f"cmd-{event_id}", "main", sequence, event_id,
                frame_version=1,
            )
            db.execute(
                """
                INSERT INTO workbench_events(
                    event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                    command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
                    checksum,created_at
                ) VALUES (?,?,?,?,1,'service','repairer','main',?,1,1,?,?,?,?, 'now')
                """,
                (
                    event_id, "quarantine-task", sequence, event_type, f"cmd-{event_id}", payload,
                    f"idem-{event_id}", f"prior-{sequence}", f"sum-{sequence}",
                ),
            )
        db.execute(
            """
            INSERT INTO quarantined_components(
                id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,preserved_read,metadata_json
            ) VALUES ('q1','quarantine-task','main','scheduled_task','legacy-pinger','build-2',
                      'periodically pings a UI','candidate','{"action":"disable_after_proof"}',
                      'manifest-sum','now','stage-event',1,1,'{}')
            """
        )
        assert db.execute("SELECT state FROM quarantined_components WHERE id='q1'").fetchone() == ("candidate",)
        for sql in (
            "DELETE FROM quarantined_components WHERE id='q1'",
            "UPDATE quarantined_components SET behavior='rewritten' WHERE id='q1'",
            "UPDATE quarantined_components SET manifest_json='{}' WHERE id='q1'",
            "UPDATE quarantined_components SET staged_event_id='q2-stage-event',staged_event_sequence=6 WHERE id='q1'",
            "UPDATE quarantined_components SET baseline_row_count=1 WHERE id='q1'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                    manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,preserved_read,metadata_json
                ) VALUES ('duplicate','quarantine-task','main','scheduled_task','legacy-pinger','build-2',
                          'duplicate','candidate','{}','sum','now','stage-event',1,1,'{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE quarantined_components SET state='released',released_at='now',release_reason='skip' "
                "WHERE id='q1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                    manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,
                    replacement_bootstrap_proof_id,
                    replacement_bootstrap_receipt_id,activated_at,activated_event_id,activated_event_sequence,
                    preserved_read,metadata_json
                ) VALUES ('direct-active','quarantine-task','main','scheduled_task','other-pinger','build-2',
                          'skipped staging','active','{}','sum','now','stage-event',1,'proof-1','receipt-1','now',
                          'activation-event',4,1,'{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                    manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,legacy_data_store,
                    preserved_read,metadata_json
                ) VALUES ('legacy-missing-proof','quarantine-task','main','sqlite_store','legacy-db','build-2',
                          'legacy rows','candidate','{}','sum','now','stage-event',1,1,1,'{}')
                """
            )
        db.execute("UPDATE quarantined_components SET state='shadow_readonly' WHERE id='q1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                UPDATE quarantined_components
                SET behavior='rewritten during cutover',replacement_bootstrap_proof_id='proof-1',
                    replacement_bootstrap_receipt_id='receipt-1',activated_at='now',
                    activated_event_id='activation-event',activated_event_sequence=4,state='active'
                WHERE id='q1'
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE quarantined_components SET state='active' WHERE id='q1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO replacement_bootstrap_proofs(
                    proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                    proof_kind,status,
                    authority_kind,authority_locator,verifier_kind,verifier_identity,proof_event_id,
                    proof_event_sequence,proof_json,proof_checksum,created_at
                ) VALUES ('wrong-type-proof','wrong-type','quarantine-task','main','scheduled_task',
                          'legacy-pinger','build-2','live_bootstrap','succeeded','runtime','receipt://wrong','script','verifier',
                          'wrong-proof-event',3,?,?,'now')
                """,
                (wrong_type_json, wrong_type_checksum),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO replacement_bootstrap_proofs(
                    proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                    proof_kind,status,
                    authority_kind,authority_locator,verifier_kind,verifier_identity,proof_event_id,
                    proof_event_sequence,proof_json,proof_checksum,created_at
                ) VALUES ('wrong-build-proof','receipt-1','quarantine-task','main','scheduled_task',
                          'legacy-pinger','other-build','live_bootstrap','succeeded','runtime','receipt://one','script','verifier',
                          'proof-event',2,?,?,'now')
                """,
                (proof_json, proof_checksum),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO replacement_bootstrap_proofs(
                    proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                    proof_kind,status,
                    authority_kind,authority_locator,verifier_kind,verifier_identity,proof_event_id,
                    proof_event_sequence,proof_json,proof_checksum,created_at
                ) VALUES ('failed-proof','receipt-1','quarantine-task','main','scheduled_task',
                          'legacy-pinger','build-2','live_bootstrap','failed','runtime','receipt://one','script','verifier',
                          'proof-event',2,?,?,'now')
                """,
                (proof_json, proof_checksum),
            )
        for mutated_json, mutated_checksum in (
            ("{\"unrelated\":true}", proof_checksum),
            (proof_json, "0" * 64),
            (proof_json, proof_checksum.upper()),
            ('{"validated": true}', proof_checksum),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    """
                    INSERT INTO replacement_bootstrap_proofs(
                        proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                        proof_kind,status,authority_kind,authority_locator,verifier_kind,verifier_identity,
                        proof_event_id,proof_event_sequence,proof_json,proof_checksum,created_at
                    ) VALUES ('proof-1','receipt-1','quarantine-task','main','scheduled_task','legacy-pinger',
                              'build-2','live_bootstrap','succeeded','runtime','receipt://one','script','verifier',
                              'proof-event',2,?,?,'now')
                    """,
                    (mutated_json, mutated_checksum),
                )
        db.execute(
            """
            INSERT INTO replacement_bootstrap_proofs(
                proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                proof_kind,status,
                authority_kind,authority_locator,verifier_kind,verifier_identity,proof_event_id,
                proof_event_sequence,proof_json,proof_checksum,created_at
            ) VALUES ('proof-1','receipt-1','quarantine-task','main','scheduled_task','legacy-pinger','build-2',
                      'live_bootstrap','succeeded','runtime','receipt://one','script','verifier','proof-event',2,
                      ?,?,'now')
            """,
            (proof_json, proof_checksum),
        )
        db.execute(
            """
            INSERT INTO replacement_bootstrap_proofs(
                proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                proof_kind,status,authority_kind,authority_locator,verifier_kind,verifier_identity,
                proof_event_id,proof_event_sequence,proof_json,proof_checksum,created_at
            ) VALUES ('proof-alternate','receipt-alternate','quarantine-task','main','scheduled_task',
                      'other-pinger','other-build','live_bootstrap','succeeded','runtime','receipt://alternate',
                      'script','verifier','alternate-proof-event',7,?,?,'now')
            """,
            (alternate_proof_json, alternate_proof_checksum),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                UPDATE quarantined_components
                SET component_id='other-pinger',replacement_build_id='other-build',behavior='alternate behavior',
                    manifest_json='{"alternate":true}',manifest_checksum='other-manifest',
                    replacement_bootstrap_proof_id='proof-alternate',
                    replacement_bootstrap_receipt_id='receipt-alternate',activated_at='now',
                    activated_event_id='alternate-activation-event',activated_event_sequence=8,state='active'
                WHERE id='q1'
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                UPDATE quarantined_components
                SET replacement_bootstrap_proof_id='proof-1',replacement_bootstrap_receipt_id='receipt-1',
                    activated_at='now',activated_event_id='wrong-activation-event',activated_event_sequence=5,
                    state='active'
                WHERE id='q1'
                """
            )
        db.execute(
            """
            UPDATE quarantined_components
            SET replacement_bootstrap_proof_id='proof-1',replacement_bootstrap_receipt_id='receipt-1',
                activated_at='now',activated_event_id='activation-event',activated_event_sequence=4,state='active'
            WHERE id='q1'
            """
        )
        assert db.execute("SELECT state FROM quarantined_components WHERE id='q1'").fetchone() == ("active",)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE quarantined_components SET replacement_bootstrap_receipt_id='other' WHERE id='q1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE quarantined_components SET manifest_checksum='changed' WHERE id='q1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE quarantined_components SET state='shadow_readonly' WHERE id='q1'")
        db.execute(
            "UPDATE quarantined_components SET state='released',released_at='now',release_reason='cutover complete' "
            "WHERE id='q1'"
        )
        for sql in (
            "UPDATE quarantined_components SET released_at='rewritten' WHERE id='q1'",
            "UPDATE quarantined_components SET release_reason='rewritten' WHERE id='q1'",
            "UPDATE quarantined_components SET metadata_json='{\"changed\":true}' WHERE id='q1'",
            "DELETE FROM quarantined_components WHERE id='q1'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE quarantined_components SET state='candidate' WHERE id='q1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE replacement_bootstrap_proofs SET proof_checksum='changed' WHERE proof_id='proof-1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM replacement_bootstrap_proofs WHERE proof_id='proof-1'")
        db.execute(
            """
            INSERT INTO quarantined_components(
                id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,
                preserved_read,metadata_json
            ) VALUES ('q2','quarantine-task','main','scheduled_task','legacy-pinger','build-3',
                      'new staged build','candidate','{}','sum-2','later','q2-stage-event',6,1,'{}')
            """
        )
        assert db.execute(
            "SELECT replacement_build_id,state FROM quarantined_components WHERE id='q2'"
        ).fetchone() == ("build-3", "candidate")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('other-task','Other','repairing','main',1,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('other-task','main','active','now')"
        )
        other_stage_payload = json.dumps(
            {
                "component_type": "scheduled_task", "component_id": "legacy-pinger",
                "replacement_build_id": "other-build", "manifest_checksum": "other-manifest",
            },
            sort_keys=True,
        )
        insert_single_event_manifest(db, "other-task", "other-cmd", "main", 1, "other-stage", frame_version=1)
        db.execute(
            """
            INSERT INTO workbench_events(
                event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
                checksum,created_at
            ) VALUES ('other-stage','other-task',1,'component.quarantine_staged',1,'service','repairer','main',
                      'other-cmd',1,1,?,'other-idem','','other-sum','now')
            """,
            (other_stage_payload,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                    manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,
                    preserved_read,metadata_json
                ) VALUES ('q-other','other-task','main','scheduled_task','legacy-pinger','other-build',
                          'cross-task duplicate','candidate','{}','other-manifest','now','other-stage',1,1,'{}')
                """
            )
        db.execute(
            """
            INSERT INTO replacement_bootstrap_proofs(
                proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                proof_kind,status,authority_kind,authority_locator,verifier_kind,verifier_identity,
                proof_event_id,proof_event_sequence,proof_json,proof_checksum,created_at
            ) VALUES ('proof-2','receipt-2','quarantine-task','main','scheduled_task','legacy-pinger','build-3',
                      'live_bootstrap','succeeded','runtime','receipt://two','script','verifier',
                      'q2-proof-event',9,?,?,'now')
            """,
            (q2_proof_json, q2_proof_checksum),
        )
        db.execute("UPDATE quarantined_components SET state='shadow_readonly' WHERE id='q2'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                UPDATE quarantined_components
                SET replacement_bootstrap_proof_id='proof-1',replacement_bootstrap_receipt_id='receipt-1',
                    activated_at='later',activated_event_id='q2-activation-event',activated_event_sequence=10,
                    state='active' WHERE id='q2'
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                UPDATE quarantined_components
                SET replacement_bootstrap_proof_id='proof-2',replacement_bootstrap_receipt_id='receipt-2',
                    activated_at='later',activated_event_id='activation-event',activated_event_sequence=4,
                    state='active' WHERE id='q2'
                """
            )
        db.execute(
            """
            UPDATE quarantined_components
            SET replacement_bootstrap_proof_id='proof-2',replacement_bootstrap_receipt_id='receipt-2',
                activated_at='later',activated_event_id='q2-activation-event',activated_event_sequence=10,
                state='active' WHERE id='q2'
            """
        )
        assert db.execute("SELECT state FROM quarantined_components WHERE id='q2'").fetchone() == ("active",)


@pytest.mark.asyncio
async def test_quarantine_baseline_is_all_null_or_complete_verified_shape(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('baseline-task','Baseline','repairing','main',1,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('baseline-task','main','active','now')"
        )
        for sequence, component_id in enumerate(("partial", "empty", "bad-digest", "valid"), start=1):
            payload = json.dumps(
                {
                    "component_type": "sqlite_store", "component_id": component_id,
                    "replacement_build_id": "build", "manifest_checksum": f"manifest-{component_id}",
                },
                sort_keys=True,
            )
            insert_single_event_manifest(
                db, "baseline-task", f"cmd-{component_id}", "main", sequence, f"stage-{component_id}",
                frame_version=1,
            )
            db.execute(
                """
                INSERT INTO workbench_events(
                    event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                    command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
                    checksum,created_at
                ) VALUES (?,?,?,'component.quarantine_staged',1,'service','repairer','main',?,1,1,?,?,?,?, 'now')
                """,
                (
                    f"stage-{component_id}", "baseline-task", sequence, f"cmd-{component_id}", payload,
                    f"idem-{component_id}", f"prior-{sequence}", f"sum-{sequence}",
                ),
            )

        def insert_baseline(
            component_id: str,
            legacy: int,
            row_count: int | None,
            digest: str | None,
            backup_path: str | None,
            backup_checksum: str | None,
        ) -> None:
            sequence = {"partial": 1, "empty": 2, "bad-digest": 3, "valid": 4}[component_id]
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                    manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,
                    preserved_read,legacy_data_store,baseline_row_count,baseline_content_digest,
                    validated_backup_path,validated_backup_checksum,metadata_json
                ) VALUES (?,?,?,?,?,'build','legacy store','candidate','{}',?,'now',?,?,1,?,?,?,?,?,'{}')
                """,
                (
                    f"q-{component_id}", "baseline-task", "main", "sqlite_store", component_id,
                    f"manifest-{component_id}", f"stage-{component_id}", sequence, legacy, row_count,
                    digest, backup_path, backup_checksum,
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            insert_baseline("partial", 0, None, SHA_A, "C:/backup.sqlite", SHA_B)
        with pytest.raises(sqlite3.IntegrityError):
            insert_baseline("empty", 1, 1, "", "", "")
        with pytest.raises(sqlite3.IntegrityError):
            insert_baseline("bad-digest", 1, 1, "ABC", "C:/backup.sqlite", SHA_B.upper())
        insert_baseline("valid", 1, 1, SHA_A, "C:/backup.sqlite", SHA_B)
        assert db.execute(
            "SELECT baseline_row_count,baseline_content_digest,validated_backup_checksum "
            "FROM quarantined_components WHERE id='q-valid'"
        ).fetchone() == (1, SHA_A, SHA_B)


@pytest.mark.asyncio
async def test_quarantine_requires_stage_before_proof_before_activation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    proof_json = '{"validated":true}'
    proof_checksum = hashlib.sha256(proof_json.encode("utf-8")).hexdigest()
    identity = {
        "proof_id": "order-proof", "receipt_id": "order-receipt", "component_type": "service",
        "component_id": "order-component", "replacement_build_id": "build", "proof_kind": "live_bootstrap",
        "status": "succeeded", "proof_json": json.loads(proof_json), "proof_checksum": proof_checksum,
        "authority_kind": "runtime", "authority_locator": "receipt://order", "verifier_kind": "script",
        "verifier_identity": "verifier",
    }
    stage_payload = json.dumps(
        {
            "component_type": "service", "component_id": "order-component",
            "replacement_build_id": "build", "manifest_checksum": "manifest",
        },
        sort_keys=True,
    )
    activation_payload = json.dumps(
        {key: identity[key] for key in (
            "proof_id", "receipt_id", "component_type", "component_id", "replacement_build_id", "proof_checksum",
        )},
        sort_keys=True,
    )
    async with aiosqlite.connect(settings.state_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('order-task','Order','repairing','main',1,'now','now')"
        )
        await db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('order-task','main','active','now')"
        )
        for event_id, sequence, event_type, payload in (
            ("order-proof-event", 1, "component.replacement_bootstrap_succeeded", json.dumps(identity, sort_keys=True)),
            ("order-stage-event", 2, "component.quarantine_staged", stage_payload),
            ("order-activation-event", 3, "component.quarantine_activated", activation_payload),
        ):
            await db.execute(
                """
                INSERT INTO workbench_command_manifests(
                    task_id,command_id,target_branch_id,event_count,first_sequence,last_sequence,
                    first_event_id,last_event_id,starting_frame_version,expected_frame_version,
                    confirm_ordinal,drafts_checksum,manifest_checksum,created_at
                ) VALUES ('order-task',?,'main',1,?,?,?, ?,1,1,NULL,?,?,'now')
                """,
                (f"cmd-{event_id}", sequence, sequence, event_id, event_id, SHA_A, SHA_B),
            )
            await db.execute(
                """
                INSERT INTO workbench_events(
                    event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                    command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
                    checksum,created_at
                ) VALUES (?,?,?, ?,1,'service','repairer','main',?,1,1,?,?,?,?, 'now')
                """,
                (
                    event_id, "order-task", sequence, event_type, f"cmd-{event_id}", payload,
                    f"idem-{event_id}", f"prior-{sequence}", f"sum-{sequence}",
                ),
            )
        await db.execute(
            """
            INSERT INTO replacement_bootstrap_proofs(
                proof_id,receipt_id,task_id,branch_id,component_type,component_id,replacement_build_id,
                proof_kind,status,authority_kind,authority_locator,verifier_kind,verifier_identity,
                proof_event_id,proof_event_sequence,proof_json,proof_checksum,created_at
            ) VALUES ('order-proof','order-receipt','order-task','main','service','order-component','build',
                      'live_bootstrap','succeeded','runtime','receipt://order','script','verifier',
                      'order-proof-event',1,?,?,'now')
            """,
            (proof_json, proof_checksum),
        )
        await db.execute(
            """
            INSERT INTO quarantined_components(
                id,task_id,branch_id,component_type,component_id,replacement_build_id,behavior,state,
                manifest_json,manifest_checksum,staged_at,staged_event_id,staged_event_sequence,preserved_read,metadata_json
            ) VALUES ('order-q','order-task','main','service','order-component','build','order','candidate',
                      '{}','manifest','now','order-stage-event',2,1,'{}')
            """
        )
        await db.execute("UPDATE quarantined_components SET state='shadow_readonly' WHERE id='order-q'")
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                """
                UPDATE quarantined_components
                SET replacement_bootstrap_proof_id='order-proof',replacement_bootstrap_receipt_id='order-receipt',
                    activated_at='now',activated_event_id='order-activation-event',activated_event_sequence=3,
                    state='active' WHERE id='order-q'
                """
            )


def insert_manifest_task(db: sqlite3.Connection, task_id: str = "manifest-task") -> None:
    db.execute(
        "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
        "VALUES (?,?, 'running','main',0,'now','now')",
        (task_id, task_id),
    )
    db.execute(
        "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) VALUES (?,'main','active','now')",
        (task_id,),
    )


def insert_command_manifest(db: sqlite3.Connection, **overrides: object) -> None:
    values: dict[str, object] = {
        "task_id": "manifest-task",
        "command_id": "command-1",
        "target_branch_id": "main",
        "event_count": 2,
        "first_sequence": 1,
        "last_sequence": 2,
        "first_event_id": "event-1",
        "last_event_id": "event-2",
        "starting_frame_version": 0,
        "expected_frame_version": 0,
        "confirm_ordinal": None,
        "drafts_checksum": SHA_A,
        "manifest_checksum": SHA_B,
        "created_at": "created-1",
    }
    values.update(overrides)
    columns = tuple(values)
    db.execute(
        f"INSERT INTO workbench_command_manifests({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def insert_single_event_manifest(
    db: sqlite3.Connection,
    task_id: str,
    command_id: str,
    branch_id: str,
    sequence: int,
    event_id: str,
    *,
    frame_version: int = 0,
    created_at: str = "now",
) -> None:
    insert_command_manifest(
        db,
        task_id=task_id,
        command_id=command_id,
        target_branch_id=branch_id,
        event_count=1,
        first_sequence=sequence,
        last_sequence=sequence,
        first_event_id=event_id,
        last_event_id=event_id,
        starting_frame_version=frame_version,
        expected_frame_version=frame_version,
        created_at=created_at,
    )


def insert_manifest_event(
    db: sqlite3.Connection,
    event_id: str,
    sequence: int,
    command_id: str,
    command_sequence: int,
    *,
    task_id: str = "manifest-task",
    branch_id: str = "main",
    created_at: str = "created-1",
) -> None:
    db.execute(
        """
        INSERT INTO workbench_events(
            event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
            command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
            checksum,created_at
        ) VALUES (?,?,?,'manifest.test',1,'service','writer',?,?,?,0,'{}',?,?,?,?)
        """,
        (
            event_id, task_id, sequence, branch_id, command_id, command_sequence,
            f"idem-{task_id}-{command_id}-{command_sequence}", f"prior-{sequence}", f"sum-{sequence}", created_at,
        ),
    )


def test_command_manifest_checksum_contract_golden_vectors() -> None:
    migration_path = (
        ROOT / "orchestrator" / "state" / "migrations" / "0003_workbench_command_manifests.sql"
    )
    migration_sql = migration_path.read_text(encoding="utf-8")
    assert "workbench.command.drafts.v1" in migration_sql
    assert "workbench.command.manifest.v1" in migration_sql

    class NestedVector(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        count: int
        note: str | None

    class NestedPayload(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        title: str
        details: NestedVector

    class ConfirmPayload(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        confirmed: bool
        details: NestedVector

    registry = EventRegistry()
    registry.register(EventDefinition(
        event_type="fixture.nested", event_schema_version=1, payload_model=NestedPayload,
        frame_effect=FrameEffect.INHERIT, reducer=lambda state, event: state,
    ))
    registry.register(EventDefinition(
        event_type="fixture.confirmed", event_schema_version=1, payload_model=ConfirmPayload,
        frame_effect=FrameEffect.CONFIRM, reducer=lambda state, event: state,
    ))
    validated = (
        registry.validate(EventDraft(
            event_type="fixture.nested", actor=EventActor(kind="owner", actor_id="matt"),
            payload={"title": "Café", "details": {"count": 1, "note": None}},
        )),
        registry.validate(EventDraft(
            event_type="fixture.confirmed", actor=EventActor(kind="service", actor_id="codex"),
            cause=EventCause.OWNER_REQUEST, caused_by="event-1",
            payload={"confirmed": True, "details": {"count": 2, "note": "prêt"}},
        )),
    )
    drafts_envelope = {
        "domain": "workbench.command.drafts.v1",
        "task_id": "task-Ω",
        "command_id": "cmd-1",
        "drafts": [{
            "ordinal": ordinal,
            "event_type": item.draft.event_type,
            "event_schema_version": item.draft.event_schema_version,
            "actor_kind": item.draft.actor.kind,
            "actor_id": item.draft.actor.actor_id,
            "branch_id": item.draft.branch_id,
            "cause": item.draft.cause.value if item.draft.cause is not None else None,
            "caused_by": item.draft.caused_by,
            "payload": item.payload.model_dump(mode="json"),
        } for ordinal, item in enumerate(validated, start=1)],
    }
    drafts_canonical = json.dumps(
        drafts_envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert drafts_canonical == (
        '{"command_id":"cmd-1","domain":"workbench.command.drafts.v1","drafts":'
        '[{"actor_id":"matt","actor_kind":"owner","branch_id":"main","cause":null,'
        '"caused_by":null,"event_schema_version":1,"event_type":"fixture.nested","ordinal":1,'
        '"payload":{"details":{"count":1,"note":null},"title":"Café"}},'
        '{"actor_id":"codex","actor_kind":"service","branch_id":"main","cause":"owner_request",'
        '"caused_by":"event-1","event_schema_version":1,"event_type":"fixture.confirmed",'
        '"ordinal":2,"payload":{"confirmed":true,"details":{"count":2,"note":"prêt"}}}],'
        '"task_id":"task-Ω"}'
    )
    drafts_checksum = hashlib.sha256(drafts_canonical.encode("utf-8")).hexdigest()
    assert drafts_checksum == "196c225b4ee2d3cc000abaded415649c6fb831cf5cd09fd0bca2e70f967d62c0"

    manifest_envelope = {
        "domain": "workbench.command.manifest.v1",
        "task_id": "task-Ω", "command_id": "cmd-1", "target_branch_id": "main",
        "event_count": 2, "first_sequence": 1, "last_sequence": 2,
        "first_event_id": "event-1", "last_event_id": "event-2",
        "starting_frame_version": 0, "expected_frame_version": 0, "confirm_ordinal": None,
        "drafts_checksum": drafts_checksum, "created_at": "2026-07-14T12:00:00.000000Z",
    }
    manifest_canonical = json.dumps(
        manifest_envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert manifest_canonical == (
        '{"command_id":"cmd-1","confirm_ordinal":null,"created_at":"2026-07-14T12:00:00.000000Z",'
        '"domain":"workbench.command.manifest.v1","drafts_checksum":'
        '"196c225b4ee2d3cc000abaded415649c6fb831cf5cd09fd0bca2e70f967d62c0",'
        '"event_count":2,"expected_frame_version":0,"first_event_id":"event-1","first_sequence":1,'
        '"last_event_id":"event-2","last_sequence":2,"starting_frame_version":0,'
        '"target_branch_id":"main","task_id":"task-Ω"}'
    )
    assert hashlib.sha256(manifest_canonical.encode("utf-8")).hexdigest() == (
        "0c9b063cf6b4d4cc6661c0633252a5360e91b107bdccbd59889fbd801e4e2306"
    )
    manifest_envelope["confirm_ordinal"] = 2
    mutated_canonical = json.dumps(
        manifest_envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert hashlib.sha256(mutated_canonical.encode("utf-8")).hexdigest() == (
        "eb16368cc351de06ff440dafae11f7a2f43cbae35d0c4d16838f5bb835f7f1e3"
    )


@pytest.mark.asyncio
async def test_command_manifest_storage_classes_reject_fractional_numbers_and_blob_checksums(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    cases: tuple[tuple[str, dict[str, object]], ...] = (
        ("fractional-count", {"event_count": 1.5, "last_sequence": 1.5}),
        ("fractional-frames", {"starting_frame_version": 0.5, "expected_frame_version": 0.5}),
        ("fractional-confirm", {"event_count": 2, "last_sequence": 2, "confirm_ordinal": 1.5}),
        ("blob-drafts", {"drafts_checksum": sqlite3.Binary(b"a" * 64)}),
        ("blob-manifest", {"manifest_checksum": sqlite3.Binary(b"b" * 64)}),
    )
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for label, _ in cases:
            insert_manifest_task(db, f"type-{label}")
        db.commit()
        accepted: list[str] = []
        for label, overrides in cases:
            try:
                insert_command_manifest(
                    db,
                    task_id=f"type-{label}",
                    command_id=f"command-{label}",
                    first_event_id=f"first-{label}",
                    last_event_id=f"last-{label}",
                    **overrides,
                )
            except sqlite3.IntegrityError:
                continue
            accepted.append(label)
        db.rollback()
        assert accepted == []


@pytest.mark.asyncio
async def test_event_manifest_guard_rejects_fractional_event_storage_classes(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_manifest_task(db, "fractional-ordinal-task")
        insert_manifest_task(db, "fractional-frame-task")
        db.commit()
        insert_command_manifest(
            db,
            task_id="fractional-ordinal-task",
            command_id="fractional-ordinal-command",
            event_count=2,
            first_sequence=1,
            last_sequence=2,
            first_event_id="ordinal-first",
            last_event_id="ordinal-last",
        )
        insert_command_manifest(
            db,
            task_id="fractional-frame-task",
            command_id="fractional-frame-command",
            event_count=1,
            first_sequence=1,
            last_sequence=1,
            first_event_id="frame-event",
            last_event_id="frame-event",
        )
        attempts = (
            (
                "fractional-ordinal", "fractional-ordinal-task", 1.5, 1,
                "fractional-ordinal-command", 1.5, 0,
            ),
            (
                "frame-event", "fractional-frame-task", 1, 1.5,
                "fractional-frame-command", 1, 0.5,
            ),
        )
        accepted: list[str] = []
        for event_id, task_id, sequence, schema_version, command_id, ordinal, frame_version in attempts:
            try:
                db.execute(
                    """
                    INSERT INTO workbench_events(
                        event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                        command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
                        checksum,created_at
                    ) VALUES (?,?,?,'fixture.type',?,'service','writer','main',?,?,?,'{}',?,?,?,'created-1')
                    """,
                    (
                        event_id, task_id, sequence, schema_version, command_id, ordinal, frame_version,
                        f"idem-{event_id}", f"prior-{event_id}", f"sum-{event_id}",
                    ),
                )
            except sqlite3.IntegrityError:
                continue
            accepted.append(event_id)
        db.rollback()
        assert accepted == []


@pytest.mark.asyncio
async def test_command_manifests_are_complete_immutable_and_protect_tail_endpoints(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    expected_columns = {
        "task_id", "command_id", "target_branch_id", "event_count", "first_sequence", "last_sequence",
        "first_event_id", "last_event_id", "starting_frame_version", "expected_frame_version",
        "confirm_ordinal", "drafts_checksum", "manifest_checksum", "created_at",
    }
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        column_info = {str(row[1]): row for row in db.execute("PRAGMA table_info(workbench_command_manifests)")}
        assert set(column_info) == expected_columns
        assert {name for name, row in column_info.items() if int(row[3]) == 0} == {"confirm_ordinal"}
        table_sql = str(db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='workbench_command_manifests'"
        ).fetchone()[0]).upper()
        assert table_sql.count("DEFERRABLE INITIALLY DEFERRED") == 2
        insert_manifest_task(db)
        insert_manifest_task(db, "")
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('manifest-task','','active','now')"
        )
        db.commit()

        invalid_shapes = (
            {"command_id": "bad-count", "event_count": 0},
            {"command_id": "bad-first", "first_sequence": 0},
            {"command_id": "bad-range", "last_sequence": 3},
            {"command_id": "bad-first-id", "first_event_id": ""},
            {"command_id": "bad-last-id", "last_event_id": " "},
            {"command_id": "bad-start-frame", "starting_frame_version": -1},
            {"command_id": "bad-expected-frame", "expected_frame_version": -1},
            {"command_id": "bad-confirm-low", "confirm_ordinal": 0},
            {"command_id": "bad-confirm-high", "confirm_ordinal": 3},
            {"command_id": "bad-drafts", "drafts_checksum": "A" * 64},
            {"command_id": "bad-manifest", "manifest_checksum": "short"},
            {"task_id": "", "command_id": "blank-task"},
            {"command_id": ""},
            {"command_id": "blank-branch", "target_branch_id": ""},
        )
        for invalid in invalid_shapes:
            with pytest.raises(sqlite3.IntegrityError):
                insert_command_manifest(db, **invalid)
        with pytest.raises(sqlite3.IntegrityError):
            insert_command_manifest(db, task_id="missing-task", command_id="cross-task")
        with pytest.raises(sqlite3.IntegrityError):
            insert_command_manifest(db, command_id="cross-branch", target_branch_id="missing")

        insert_command_manifest(db)
        insert_manifest_event(db, "event-1", 1, "command-1", 1)
        insert_manifest_event(db, "event-2", 2, "command-1", 2, created_at="created-2")
        db.commit()
        assert db.execute(
            "SELECT event_count,first_sequence,last_sequence,first_event_id,last_event_id "
            "FROM workbench_command_manifests WHERE command_id='command-1'"
        ).fetchone() == (2, 1, 2, "event-1", "event-2")

        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE workbench_command_manifests SET event_count=1 WHERE command_id='command-1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM workbench_command_manifests WHERE command_id='command-1'")
        with pytest.raises(sqlite3.IntegrityError):
            insert_command_manifest(
                db, command_id="gap", event_count=1, first_sequence=4, last_sequence=4,
                first_event_id="event-4", last_event_id="event-4",
            )
        with pytest.raises(sqlite3.IntegrityError):
            insert_command_manifest(
                db, command_id="overlap", event_count=1, first_sequence=2, last_sequence=2,
                first_event_id="event-overlap", last_event_id="event-overlap",
            )

        insert_command_manifest(
            db, command_id="command-2", event_count=1, first_sequence=3, last_sequence=3,
            first_event_id="event-3", last_event_id="event-3", confirm_ordinal=1, created_at="created-3",
        )
        for event_args in (
            ("orphan", 3, "missing-command", 1, "main", "created-3"),
            ("wrong-id", 3, "command-2", 1, "main", "created-3"),
            ("event-3", 4, "command-2", 1, "main", "created-3"),
            ("event-3", 3, "command-2", 2, "main", "created-3"),
            ("event-3", 3, "command-2", 1, "missing", "created-3"),
            ("event-3", 3, "command-2", 1, "main", "wrong-created-at"),
        ):
            event_id, sequence, command_id, ordinal, branch_id, created_at = event_args
            with pytest.raises(sqlite3.IntegrityError):
                insert_manifest_event(
                    db, event_id, sequence, command_id, ordinal, branch_id=branch_id, created_at=created_at
                )
        insert_manifest_event(db, "event-3", 3, "command-2", 1, created_at="created-3")
        db.commit()

        db.execute("DROP TRIGGER workbench_events_no_delete")
        db.commit()
        db.execute("DELETE FROM workbench_events WHERE event_id='event-2'")
        with pytest.raises(sqlite3.IntegrityError):
            db.commit()
        db.rollback()
        assert db.execute("SELECT COUNT(*) FROM workbench_events WHERE event_id='event-2'").fetchone() == (1,)


@pytest.mark.asyncio
async def test_v2_without_events_upgrades_to_v3_without_fabricating_manifests(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    v2_catalog = write_catalog(
        tmp_path / "v2-only",
        {"0002_workbench_core.sql": (
            ROOT / "orchestrator" / "state" / "migrations" / "0002_workbench_core.sql"
        ).read_text(encoding="utf-8")},
    )
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, v2_catalog).apply(db) == [2]
    assert query_all(settings.state_path, "SELECT version FROM schema_migrations ORDER BY version") == [(1,), (2,)]
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings).apply(db) == [3]
    assert query_all(settings.state_path, "SELECT version FROM schema_migrations ORDER BY version") == [
        (1,), (2,), (3,),
    ]


@pytest.mark.asyncio
async def test_manifest_transaction_order_supports_root_append_and_parent_targeted_fork(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_manifest_task(db, "fork-task")
        insert_command_manifest(
            db, task_id="fork-task", command_id="fork-command", target_branch_id="main",
            event_count=1, first_sequence=1, last_sequence=1,
            first_event_id="fork-event", last_event_id="fork-event", created_at="fork-created",
        )
        insert_manifest_event(
            db, "fork-event", 1, "fork-command", 1, task_id="fork-task", created_at="fork-created"
        )
        db.execute(
            """
            INSERT INTO workbench_branches(
                task_id,branch_id,parent_branch_id,forked_from_sequence,forked_from_frame_version,
                created_by_event_id,status,created_at
            ) VALUES ('fork-task','child','main',1,0,'fork-event','active','fork-created')
            """
        )
        insert_command_manifest(
            db, task_id="fork-task", command_id="child-command", target_branch_id="child",
            event_count=1, first_sequence=2, last_sequence=2,
            first_event_id="child-event", last_event_id="child-event", created_at="child-created",
        )
        insert_manifest_event(
            db, "child-event", 2, "child-command", 1, task_id="fork-task",
            branch_id="child", created_at="child-created",
        )
        db.commit()
        assert db.execute(
            "SELECT command_id,target_branch_id,first_sequence,last_sequence "
            "FROM workbench_command_manifests WHERE task_id='fork-task' ORDER BY first_sequence"
        ).fetchall() == [
            ("fork-command", "main", 1, 1),
            ("child-command", "child", 2, 2),
        ]


@pytest.mark.asyncio
async def test_v3_fails_closed_and_records_repair_for_preexisting_v2_events(tmp_path: Path) -> None:
    assert hasattr(migration_module, "MigrationPreconditionError")
    precondition_type = migration_module.MigrationPreconditionError
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    v2_catalog = write_catalog(
        tmp_path / "v2-with-event",
        {"0002_workbench_core.sql": (
            ROOT / "orchestrator" / "state" / "migrations" / "0002_workbench_core.sql"
        ).read_text(encoding="utf-8")},
    )
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, v2_catalog).apply(db) == [2]
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_manifest_task(db, "legacy-v2-task")
        insert_manifest_event(
            db, "legacy-v2-event", 1, "legacy-v2-command", 1, task_id="legacy-v2-task"
        )
        db.commit()

    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(precondition_type) as raised:
            await MigrationRunner(settings).apply(db)
        assert not db.in_transaction
    assert raised.value.version == 3
    assert raised.value.code == "preexisting_workbench_events_without_manifests"

    assert query_all(settings.state_path, "SELECT version FROM schema_migrations ORDER BY version") == [(1,), (2,)]
    assert query_all(
        settings.state_path,
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='workbench_command_manifests'",
    ) == [(0,)]
    repair = json.loads(str(query_all(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration' ORDER BY created_at DESC LIMIT 1",
    )[0][0]))
    assert repair["version"] == 3
    assert repair["code"] == "preexisting_workbench_events_without_manifests"
    assert repair["backup_published"] is True
    assert repair["backup_retained"] is True
    assert Path(str(repair["backup_path"])).is_file()

    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(precondition_type) as retry:
            await MigrationRunner(settings).apply(db)
    assert retry.value.version == 3
    assert retry.value.code == "preexisting_workbench_events_without_manifests"
    assert query_all(settings.state_path, "SELECT version FROM schema_migrations ORDER BY version") == [(1,), (2,)]
    assert query_all(
        settings.state_path,
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='workbench_command_manifests'",
    ) == [(0,)]


def test_migration_two_contains_no_transcript_content_storage() -> None:
    sql = (ROOT / "orchestrator" / "state" / "migrations" / "0002_workbench_core.sql").read_text(
        encoding="utf-8"
    ).lower()
    forbidden = (
        "transcript_body", "transcript_summary", "transcript_snippet", "transcript_embedding",
        "hidden_reasoning", "raw_transcript",
    )
    assert not any(name in sql for name in forbidden)


@pytest.mark.asyncio
async def test_cancellation_rolls_back_records_accurate_repair_and_propagates(tmp_path: Path) -> None:
    class CancelDuringBackup(MigrationRunner):
        def _create_validated_backup(self, next_version: int) -> Path:
            raise asyncio.CancelledError

    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(asyncio.CancelledError):
            await CancelDuringBackup(settings).apply(db)
        assert not db.in_transaction

    details = query_all(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'",
    )
    assert len(details) == 1
    payload = json.loads(str(details[0][0]))
    assert payload["backup_published"] is False
    assert payload["backup_retained"] is False
    assert payload["backup_path"] is None


@pytest.mark.asyncio
async def test_cancellation_during_repair_finishes_cleanup_and_still_propagates(tmp_path: Path) -> None:
    class PauseDuringRepair(MigrationRunner):
        def __init__(self, settings: Settings, migrations_dir: Path) -> None:
            super().__init__(settings, migrations_dir)
            self.repair_started = asyncio.Event()
            self.release_repair = asyncio.Event()

        async def _record_repair(
            self,
            db: aiosqlite.Connection,
            version: int | None,
            exc: BaseException,
            backup_path: Path | None,
        ) -> None:
            self.repair_started.set()
            await self.release_repair.wait()
            await super()._record_repair(db, version, exc, backup_path)

    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    catalog = write_catalog(
        tmp_path / "cancel-repair",
        {"0002_break.sql": "CREATE TABLE before_cancel(id TEXT); INSERT INTO missing_table VALUES (1);"},
    )
    runner = PauseDuringRepair(settings, catalog)
    async with aiosqlite.connect(settings.state_path) as db:
        task = asyncio.create_task(runner.apply(db))
        await asyncio.wait_for(runner.repair_started.wait(), timeout=5)
        task.cancel()
        runner.release_repair.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not db.in_transaction

    assert query_all(
        settings.state_path,
        "SELECT COUNT(*) FROM repair_queue WHERE failure_source='state_migration'",
    ) == [(1,)]


def write_catalog(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    for name, sql in files.items():
        (path / name).write_text(sql, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_partial_statement_failure_rolls_back_and_writes_one_durable_repair_item(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    catalog = write_catalog(
        tmp_path / "bad-migrations",
        {"0002_break.sql": "CREATE TABLE should_rollback(id TEXT);\nINSERT INTO missing_table VALUES (1);"},
    )
    runner = MigrationRunner(settings, catalog)

    for _ in range(2):
        async with aiosqlite.connect(settings.state_path) as db:
            with pytest.raises(MigrationApplyError):
                await runner.apply(db)

    tables = {row[0] for row in query_all(settings.state_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "should_rollback" not in tables
    assert query_all(settings.state_path, "SELECT version FROM schema_migrations WHERE version=2") == []
    repairs = query_all(settings.state_path, "SELECT failure_source, resolved FROM repair_queue WHERE failure_source='state_migration'")
    assert repairs == [("state_migration", 0)]
    detail = json.loads(
        str(query_all(settings.state_path, "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'")[0][0])
    )
    assert detail["backup_published"] is True
    assert detail["backup_retained"] is True
    assert Path(detail["backup_path"]).is_file()


@pytest.mark.asyncio
async def test_pre_migration_backup_is_readable_valid_and_windows_safe(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)

    await StateStore(settings).initialize()

    backups = backup_paths(settings)
    assert len(backups) == 1
    backup = backups[0]
    assert ":" not in backup.name
    assert not list(backup.parent.glob("*.tmp-*"))
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT raw_text FROM intents WHERE id='intent-owner-sentinel'").fetchone() == (
            "Preserve this exact owner value: café / Ω / line one\\nline two",
        )
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='workbench_events'").fetchone() == (0,)


@pytest.mark.asyncio
async def test_backup_publication_failure_aborts_before_schema_writes(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    (settings.home / "backups").write_text("blocks backup directory", encoding="utf-8")

    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(MigrationBackupError):
            await MigrationRunner(settings).apply(db)

    assert query_all(settings.state_path, "SELECT COUNT(*) FROM sqlite_master WHERE name='workbench_events'") == [(0,)]
    assert query_all(settings.state_path, "SELECT version FROM schema_migrations WHERE version=2") == []
    detail = json.loads(
        str(query_all(settings.state_path, "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'")[0][0])
    )
    assert detail["backup_published"] is False
    assert detail["backup_retained"] is False
    assert detail["backup_path"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"0002_ok.sql": "SELECT 1;", "two_bad.sql": "SELECT 2;"}, "malformed"),
        ({"0002_a.sql": "SELECT 1;", "0002_b.sql": "SELECT 2;"}, "duplicate"),
        ({"0002_a.sql": "SELECT 1;", "0004_gap.sql": "SELECT 2;"}, "gap"),
        ({"0002_tx.sql": "BEGIN; CREATE TABLE x(id); COMMIT;"}, "transaction"),
    ],
)
async def test_invalid_catalogs_are_rejected_before_backup(
    tmp_path: Path, files: dict[str, str], message: str
) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    catalog = write_catalog(tmp_path / "catalog", files)

    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(MigrationCatalogError, match=message):
            await MigrationRunner(settings, catalog).apply(db)

    assert backup_paths(settings) == []


@pytest.mark.asyncio
async def test_changed_applied_checksum_and_missing_applied_file_are_rejected(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    catalog = write_catalog(tmp_path / "catalog", {"0002_one.sql": "CREATE TABLE one(id TEXT);"})
    runner = MigrationRunner(settings, catalog)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await runner.apply(db) == [2]
    (catalog / "0002_one.sql").write_text("CREATE TABLE one(id TEXT, changed TEXT);", encoding="utf-8")
    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(MigrationChecksumError):
            await runner.apply(db)
    (catalog / "0002_one.sql").unlink()
    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(MigrationCatalogError, match="applied migration.*missing"):
            await runner.apply(db)


@pytest.mark.asyncio
async def test_multiple_complete_statements_on_one_line_execute_individually(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    catalog = write_catalog(
        tmp_path / "catalog",
        {"0002_two.sql": "CREATE TABLE first_table(id TEXT); CREATE TABLE second_table(id TEXT);"},
    )

    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, catalog).apply(db) == [2]

    names = {row[0] for row in query_all(settings.state_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"first_table", "second_table"} <= names


@pytest.mark.asyncio
async def test_unknown_applied_version_is_rejected(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    with sqlite3.connect(settings.state_path) as db:
        db.execute("ALTER TABLE schema_migrations ADD COLUMN name TEXT")
        db.execute("ALTER TABLE schema_migrations ADD COLUMN status TEXT")
        db.execute("ALTER TABLE schema_migrations ADD COLUMN checksum TEXT")
        db.execute("UPDATE schema_migrations SET name='legacy_baseline', status='applied'")
        db.execute(
            "INSERT INTO schema_migrations(version,applied_at,name,status,checksum) VALUES (9,'now','unknown','applied',?)",
            ("a" * 64,),
        )

    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(MigrationCatalogError, match="unknown applied version"):
            await MigrationRunner(settings).apply(db)


@pytest.mark.asyncio
async def test_numbered_migration_rows_are_fully_immutable(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    updates = {
        "name": "changed",
        "status": "changed",
        "applied_at": "changed",
        "checksum": "0" * 64,
    }
    with sqlite3.connect(settings.state_path) as db:
        for field, value in updates.items():
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(f"UPDATE schema_migrations SET {field}=? WHERE version=2", (value,))


def test_sql_splitter_handles_literals_comments_and_trailing_comment_only_remainder() -> None:
    sql = """
    CREATE TABLE split_test(value TEXT DEFAULT ';'); -- a line-comment semicolon ;
    /* a block-comment semicolon ; */ INSERT INTO split_test(value) VALUES ('a;b');
    -- trailing comment only ;
    """

    statements = split_sql_statements(sql)

    assert len(statements) == 2
    with sqlite3.connect(":memory:") as db:
        for statement in statements:
            db.execute(statement)
        assert db.execute("SELECT value FROM split_test").fetchall() == [("a;b",)]


@pytest.mark.asyncio
async def test_two_concurrent_initializers_apply_each_version_exactly_once(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    await asyncio.wait_for(
        asyncio.gather(StateStore(settings).initialize(), StateStore(settings).initialize()),
        timeout=15,
    )

    assert query_all(settings.state_path, "SELECT version, COUNT(*) FROM schema_migrations GROUP BY version") == [
        (1, 1), (2, 1), (3, 1),
    ]
    assert len(backup_paths(settings)) == 1


def test_wheel_contains_and_loads_baseline_and_numbered_sql_resources(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    built = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(ROOT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert built.returncode == 0
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "orchestrator/state/schema.sql" in names
    assert "orchestrator/state/migrations/0002_workbench_core.sql" in names
    assert "orchestrator/state/migrations/0003_workbench_command_manifests.sql" in names

    site = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(site), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    script = (
        "from importlib.resources import files; "
        "root=files('orchestrator.state'); "
        "assert 'CREATE TABLE' in root.joinpath('schema.sql').read_text(); "
        "assert 'workbench_events' in root.joinpath('migrations','0002_workbench_core.sql').read_text(); "
        "assert 'workbench_command_manifests' in "
        "root.joinpath('migrations','0003_workbench_command_manifests.sql').read_text()"
    )
    env = {**os.environ, "PYTHONPATH": str(site)}
    loaded = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert loaded.returncode == 0, loaded.stderr
