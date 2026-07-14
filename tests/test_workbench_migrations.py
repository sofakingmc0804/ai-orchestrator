from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import aiosqlite
import pytest

from orchestrator.config import Settings
from orchestrator.state.migration_runner import (
    MigrationApplyError,
    MigrationBackupError,
    MigrationCatalogError,
    MigrationChecksumError,
    MigrationRunner,
)
from orchestrator.state.store import StateStore


ROOT = Path(__file__).resolve().parents[1]
V1_FIXTURE = ROOT / "tests" / "fixtures" / "state_v1.sql"
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
    "quarantined_components",
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
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
                "command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at) "
                "VALUES ('e','t',1,'x',1,'service','a','main','cmd',1,0,'not-json','k','','c','now')"
            )


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
        db.execute(
            "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
            "command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at) "
            "VALUES ('a1','a',1,'x',1,'service','actor','main','cmd-a1',1,0,'{}','a1','','c1','now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
                "cause,caused_by,command_id,command_sequence,frame_version,payload_json,idempotency_key,checksum,created_at) "
                "VALUES ('b1','b',1,'x',1,'service','actor','main','bad','a1','cmd-b1',1,0,'{}','b1','c2','now')"
            )
        db.execute(
            "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,"
            "cause,caused_by,command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,"
            "checksum,created_at) VALUES ('a2','a',2,'x',1,'service','actor','main','valid','a1','cmd-a2',1,0,'{}','a2','c1','c2','now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE workbench_events SET actor_id='changed' WHERE event_id='a1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM workbench_events WHERE event_id='a1'")


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
async def test_two_concurrent_initializers_apply_each_version_exactly_once(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    await asyncio.wait_for(
        asyncio.gather(StateStore(settings).initialize(), StateStore(settings).initialize()),
        timeout=15,
    )

    assert query_all(settings.state_path, "SELECT version, COUNT(*) FROM schema_migrations GROUP BY version") == [(1, 1), (2, 1)]
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
        "assert 'workbench_events' in root.joinpath('migrations','0002_workbench_core.sql').read_text()"
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
