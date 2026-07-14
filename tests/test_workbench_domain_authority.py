from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import aiosqlite
import pytest

from orchestrator.config import Settings
from orchestrator.state.migration_runner import MigrationRunner
from orchestrator.state.store import StateStore


ROOT = Path(__file__).resolve().parents[1]
V1_FIXTURE = ROOT / "tests" / "fixtures" / "state_v1.sql"
MIGRATIONS = ROOT / "orchestrator" / "state" / "migrations"
SHA_A = "a" * 64
SHA_B = "b" * 64

DECISION_COLUMNS = (
    "decision_id",
    "task_id",
    "branch_id",
    "revision",
    "last_event_id",
    "state",
    "tier",
    "kind",
    "queue_order",
    "semantic_identity",
    "question",
    "options_json",
    "free_form_allowed",
    "recommendation_option_id",
    "recommendation_reason",
    "changed_outcome",
    "positions_json",
    "materiality_json",
    "consequence_if_unresolved",
    "affected_node_keys_json",
    "provenance_json",
    "source_occurrences_json",
    "pending_interpretation_json",
    "resolution_json",
    "created_by_event_id",
    "created_at",
    "decided_at",
)

PREEXISTING_TABLES = (
    "decision_requests",
    "evidence_refs",
    "frame_edges",
    "frame_nodes",
    "frame_proposals",
    "service_run_inputs",
    "service_runs",
)


def insert_manifest(
    db: sqlite3.Connection,
    *,
    task_id: str,
    branch_id: str,
    command_id: str,
    sequence: int,
    event_id: str,
    frame_version: int = 0,
    created_at: str = "2026-07-14T00:00:00.000000Z",
) -> None:
    db.execute(
        """
        INSERT INTO workbench_command_manifests(
            task_id,command_id,target_branch_id,event_count,first_sequence,last_sequence,
            first_event_id,last_event_id,starting_frame_version,expected_frame_version,
            confirm_ordinal,drafts_checksum,manifest_checksum,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            task_id, command_id, branch_id, 1, sequence, sequence, event_id, event_id,
            frame_version, frame_version, None, SHA_A, SHA_B, created_at,
        ),
    )


def insert_event(
    db: sqlite3.Connection,
    *,
    task_id: str,
    branch_id: str,
    command_id: str,
    sequence: int,
    event_id: str,
    event_type: str,
    payload: dict[str, object],
    frame_version: int = 0,
    created_at: str = "2026-07-14T00:00:00.000000Z",
) -> None:
    insert_manifest(
        db,
        task_id=task_id,
        branch_id=branch_id,
        command_id=command_id,
        sequence=sequence,
        event_id=event_id,
        frame_version=frame_version,
        created_at=created_at,
    )
    db.execute(
        """
        INSERT INTO workbench_events(
            event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
            command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,
            checksum,created_at
        ) VALUES (?,?,?,?,1,'system','test',?,?,1,?,?,?,?,?,?)
        """,
        (
            event_id, task_id, sequence, event_type, branch_id, command_id, frame_version,
            json.dumps(payload, sort_keys=True, separators=(",", ":")), f"idem-{event_id}",
            "" if sequence == 1 else f"prior-{sequence}", f"sum-{sequence}", created_at,
        ),
    )


def insert_task_and_root(db: sqlite3.Connection, task_id: str) -> None:
    db.execute(
        "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
        "VALUES (?,?, 'intake','main',0,'2026-07-14T00:00:00.000000Z','2026-07-14T00:00:00.000000Z')",
        (task_id, task_id),
    )
    db.execute(
        "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
        "VALUES (?,'main','active','2026-07-14T00:00:00.000000Z')",
        (task_id,),
    )


def decision_values(
    *,
    task_id: str,
    decision_id: str,
    event_id: str,
    state: str = "active",
    queue_order: int = 0,
    revision: int = 1,
    created_at: str = "2026-07-14T00:01:00.000000Z",
) -> dict[str, object]:
    source = [{
        "classification": "accepted",
        "native_question_identity_checksum": hashlib.sha256(
            f"native-question:{decision_id}".encode()
        ).hexdigest(),
        "occurrence_id": f"occurrence-{decision_id}",
        "occurrence_kind": "question",
    }]
    return {
        "decision_id": decision_id,
        "task_id": task_id,
        "branch_id": "main",
        "revision": revision,
        "last_event_id": event_id,
        "state": state,
        "tier": "blocking",
        "kind": "intent_clarification",
        "queue_order": queue_order,
        "semantic_identity": SHA_B,
        "question": "Which outcome should be authoritative?",
        "options_json": "[]",
        "free_form_allowed": 1,
        "recommendation_option_id": None,
        "recommendation_reason": None,
        "changed_outcome": "Owner-selected outcome",
        "positions_json": "[]",
        "materiality_json": "{}",
        "consequence_if_unresolved": "Execution cannot begin",
        "affected_node_keys_json": '["goal"]',
        "provenance_json": "{}",
        "source_occurrences_json": json.dumps(source, sort_keys=True, separators=(",", ":")),
        "pending_interpretation_json": None,
        "resolution_json": None,
        "created_by_event_id": event_id,
        "created_at": created_at,
        "decided_at": None,
    }


def insert_decision_row(db: sqlite3.Connection, values: dict[str, object]) -> None:
    columns = tuple(values)
    db.execute(
        f"INSERT INTO decision_requests({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def decision_snapshot(values: dict[str, object]) -> dict[str, object]:
    return {
        "decision_id": values["decision_id"],
        "task_id": values["task_id"],
        "branch_id": values["branch_id"],
        "revision": values["revision"],
        "state": values["state"],
        "tier": values["tier"],
        "kind": values["kind"],
        "queue_order": values["queue_order"],
        "semantic_identity": values["semantic_identity"],
        "question": values["question"],
        "options": json.loads(str(values["options_json"])),
        "free_form_allowed": bool(values["free_form_allowed"]),
        "recommendation_option_id": values["recommendation_option_id"],
        "recommendation_reason": values["recommendation_reason"],
        "changed_outcome": values["changed_outcome"],
        "positions": json.loads(str(values["positions_json"])),
        "materiality": json.loads(str(values["materiality_json"])),
        "consequence_if_unresolved": values["consequence_if_unresolved"],
        "affected_node_keys": json.loads(str(values["affected_node_keys_json"])),
        "provenance": json.loads(str(values["provenance_json"])),
        "source_occurrences": json.loads(str(values["source_occurrences_json"])),
        "pending_interpretation": (
            None if values["pending_interpretation_json"] is None
            else json.loads(str(values["pending_interpretation_json"]))
        ),
        "resolution": (
            None if values["resolution_json"] is None
            else json.loads(str(values["resolution_json"]))
        ),
    }


def insert_decision_event_and_row(
    db: sqlite3.Connection,
    *,
    task_id: str,
    decision_id: str,
    event_id: str,
    sequence: int,
    queue_checksum: str = SHA_A,
) -> dict[str, object]:
    values = decision_values(task_id=task_id, decision_id=decision_id, event_id=event_id)
    payload = {
        "candidate": {
            "source": json.loads(str(values["source_occurrences_json"]))[0],
        },
        "assessment": {"disposition": "ask", "semantic_identity": values["semantic_identity"]},
        "decision": decision_snapshot(values),
        "resulting_queue": {
            "active": decision_snapshot(values),
            "branch_id": "main",
            "checksum": queue_checksum,
            "queued": [],
            "revision": 1,
            "task_id": task_id,
        },
    }
    insert_event(
        db,
        task_id=task_id,
        branch_id="main",
        command_id=f"command-{event_id}",
        sequence=sequence,
        event_id=event_id,
        event_type="decision.enqueued",
        payload=payload,
        created_at=str(values["created_at"]),
    )
    insert_decision_row(db, values)
    return values


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


def write_v3_catalog(path: Path) -> Path:
    path.mkdir(parents=True)
    for name in ("0002_workbench_core.sql", "0003_workbench_command_manifests.sql"):
        (path / name).write_text((MIGRATIONS / name).read_text(encoding="utf-8"), encoding="utf-8")
    return path


def rows(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as db:
        return db.execute(sql, params).fetchall()


def table_sql(path: Path, table: str) -> str:
    result = rows(path, "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
    assert len(result) == 1
    return str(result[0][0])


def trigger_sql(path: Path, trigger: str) -> str:
    result = rows(path, "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,))
    assert len(result) == 1
    return str(result[0][0])


def insert_legacy_task3_rows(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('legacy-task','Legacy','intake','main',0,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('legacy-task','main','active','now')"
        )
        db.execute(
            "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,kind,text,status,"
            "provenance_json,created_at,updated_at) "
            "VALUES ('legacy-node','legacy-task','main',0,'goal','goal','Legacy','confirmed','{}','now','now')"
        )
        db.execute(
            """
            INSERT INTO decision_requests(
                decision_id,task_id,branch_id,state,tier,kind,queue_order,semantic_identity,question,
                affected_node_ids_json,options_json,free_form_allowed,changed_outcome,outcome_deltas_json,
                materiality_json,consequence_if_unresolved,consequence_json,provenance_json,created_at
            ) VALUES (
                'legacy-decision','legacy-task','main','active','blocking','intent_clarification',0,
                'legacy-semantic','Legacy question?','[]','[]',1,'legacy outcome','{}','{}',
                'legacy consequence','{}','{}','now'
            )
            """
        )
        db.commit()


@pytest.mark.asyncio
async def test_migration_four_fails_closed_with_exact_public_failure_and_retained_backup(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, write_v3_catalog(tmp_path / "v3-catalog")).apply(db) == [2, 3]
    insert_legacy_task3_rows(settings.state_path)

    migration_module = importlib.import_module("orchestrator.state.migration_runner")
    failure_module = importlib.import_module("orchestrator.workbench.failures")
    with pytest.raises(migration_module.MigrationPreconditionError) as raised:
        async with aiosqlite.connect(settings.state_path) as db:
            await MigrationRunner(settings).apply(db)

    failure = raised.value.failure
    assert isinstance(failure, failure_module.Task3Failure)
    assert raised.value.version == 4
    assert raised.value.code == "preexisting_task3_authority_without_events"
    assert failure.model_dump(mode="json") == {
        "schema_version": 1,
        "code": "preexisting_task3_authority_without_events",
        "task_id": None,
        "branch_id": None,
        "subject": {"kind": "migration", "id": "0004"},
        "details": {
            "code": "preexisting_task3_authority_without_events",
            "migration_version": 4,
            "row_counts": [
                {"table_name": "decision_requests", "row_count": 1},
                {"table_name": "frame_nodes", "row_count": 1},
            ],
        },
        "message": "migration 4 refuses to authenticate preexisting Task-3 authority without events",
    }
    assert rows(settings.state_path, "SELECT version FROM schema_migrations ORDER BY version") == [
        (1,), (2,), (3,)
    ]
    assert rows(
        settings.state_path,
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='decision_queue_heads'",
    ) == [(0,)]
    repair = json.loads(str(rows(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration' "
        "ORDER BY created_at DESC LIMIT 1",
    )[0][0]))
    assert repair["failure"] == failure.model_dump(mode="json")
    assert repair["backup_published"] is True
    assert repair["backup_retained"] is True
    backup = Path(str(repair["backup_path"]))
    assert backup.is_file()
    assert rows(backup, "SELECT decision_id FROM decision_requests") == [("legacy-decision",)]
    assert rows(backup, "SELECT node_id FROM frame_nodes") == [("legacy-node",)]


@pytest.mark.asyncio
async def test_migration_three_compatibility_failure_has_no_task3_failure_object(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    v2 = tmp_path / "v2-catalog"
    v2.mkdir()
    (v2 / "0002_workbench_core.sql").write_text(
        (MIGRATIONS / "0002_workbench_core.sql").read_text(encoding="utf-8"), encoding="utf-8"
    )
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, v2).apply(db) == [2]
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('t','T','intake','main',0,'now','now')"
        )
        db.execute("INSERT INTO workbench_branches(task_id,branch_id,status,created_at) VALUES ('t','main','active','now')")
        db.execute(
            "INSERT INTO workbench_events(event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,"
            "branch_id,command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at) "
            "VALUES ('e','t',1,'legacy',1,'system','test','main','cmd',1,0,'{}','idem','','sum','now')"
        )
        db.commit()
    migration_module = importlib.import_module("orchestrator.state.migration_runner")
    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(migration_module.MigrationPreconditionError) as raised:
            await MigrationRunner(settings).apply(db)
    assert raised.value.version == 3
    assert raised.value.code == "preexisting_workbench_events_without_manifests"
    assert raised.value.failure is None
    repair = json.loads(str(rows(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'",
    )[0][0]))
    assert "failure" not in repair


def test_migration_failure_models_are_strict_frozen_and_canonical() -> None:
    failure_module = importlib.import_module("orchestrator.workbench.failures")
    row = failure_module.Task3AuthorityRowCount(table_name="frame_nodes", row_count=1)
    with pytest.raises(Exception):
        row.row_count = 2
    with pytest.raises(Exception):
        failure_module.Task3AuthorityRowCount(table_name="frame_nodes", row_count="1")
    with pytest.raises(Exception):
        failure_module.Task3AuthorityRowCount(table_name="frame_nodes", row_count=True)
    with pytest.raises(Exception):
        failure_module.Task3AuthorityRowCount(table_name="unknown", row_count=1)
    with pytest.raises(Exception):
        failure_module.Task3AuthorityRowCount(table_name="frame_nodes", row_count=1, extra="forbidden")
    with pytest.raises(Exception):
        failure_module.FailureSubject(kind="migration", id="4")
    with pytest.raises(Exception):
        failure_module.FailureSubject(kind="event", id="0004")
    with pytest.raises(Exception):
        failure_module.PreexistingTask3AuthorityDetails(
            code="preexisting_task3_authority_without_events",
            migration_version=4,
            row_counts=(
                failure_module.Task3AuthorityRowCount(table_name="frame_nodes", row_count=1),
                failure_module.Task3AuthorityRowCount(table_name="decision_requests", row_count=1),
            ),
        )
    with pytest.raises(Exception):
        failure_module.PreexistingTask3AuthorityDetails(
            code="preexisting_task3_authority_without_events",
            migration_version=4,
            row_counts=(row, row),
        )
    failure = failure_module.preexisting_task3_authority_failure((row,))
    assert failure_module.Task3Failure.model_validate_json(failure.model_dump_json()) == failure
    with pytest.raises(Exception):
        failure_module.Task3Failure.model_validate({**failure.model_dump(mode="python"), "task_id": "claimed"})
    with pytest.raises(Exception):
        failure_module.Task3Failure.model_validate({**failure.model_dump(mode="python"), "branch_id": "claimed"})
    migration_module = importlib.import_module("orchestrator.state.migration_runner")
    with pytest.raises(ValueError, match="requires its exact typed failure"):
        migration_module.MigrationPreconditionError(4, "preexisting_task3_authority_without_events")


@pytest.mark.asyncio
async def test_migration_four_precondition_retry_is_stable_and_each_backup_is_valid(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, write_v3_catalog(tmp_path / "v3")).apply(db) == [2, 3]
    insert_legacy_task3_rows(settings.state_path)
    failures: list[dict[str, object]] = []
    for _ in range(2):
        async with aiosqlite.connect(settings.state_path) as db:
            with pytest.raises(Exception) as raised:
                await MigrationRunner(settings).apply(db)
            failures.append(raised.value.failure.model_dump(mode="json"))
            assert not db.in_transaction
    assert failures[0] == failures[1]
    repair = json.loads(str(rows(
        settings.state_path,
        "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'",
    )[0][0]))
    assert repair["failure"] == failures[0]
    backups = sorted((settings.home / "backups").glob("state-pre-migration-v0004-*.sqlite"))
    assert len(backups) == 2
    for backup in backups:
        with sqlite3.connect(backup) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("SELECT COUNT(*) FROM frame_nodes").fetchone() == (1,)
            assert db.execute("SELECT COUNT(*) FROM decision_requests").fetchone() == (1,)


@pytest.mark.asyncio
async def test_migration_four_backfills_only_exact_task_and_branch_creation_events(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, write_v3_catalog(tmp_path / "v3-backfill")).apply(db) == [2, 3]
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        insert_task_and_root(db, "created-task")
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,forked_from_sequence,"
            "forked_from_frame_version,created_by_event_id,status,created_at) "
            "VALUES ('created-task','child','main',1,0,'branch-event','active','2026-07-14T00:01:00.000000Z')"
        )
        insert_event(
            db, task_id="created-task", branch_id="main", command_id="task-command",
            sequence=1, event_id="task-event", event_type="task.created",
            payload={
                "title": "created-task",
                "initial_branch_id": "main",
            },
        )
        insert_event(
            db, task_id="created-task", branch_id="main", command_id="branch-command",
            sequence=2, event_id="branch-event", event_type="branch.forked",
            payload={
                "new_branch_id": "child",
                "parent_branch_id": "main",
                "forked_from_sequence": 1,
                "forked_from_frame_version": 0,
            }, frame_version=0,
            created_at="2026-07-14T00:01:00.000000Z",
        )
        db.commit()
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings).apply(db) == [4]
    assert rows(
        settings.state_path,
        "SELECT id,last_transition_event_id FROM workbench_tasks WHERE id='created-task'",
    ) == [("created-task", "task-event")]
    assert rows(
        settings.state_path,
        "SELECT branch_id,last_transition_event_id FROM workbench_branches "
        "WHERE task_id='created-task' ORDER BY branch_id",
    ) == [("child", "branch-event"), ("main", "task-event")]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("title", "initial_branch", "timestamp", "extra_key", "frame"))
async def test_migration_four_task_and_root_backfill_requires_exact_creation_event(
    tmp_path: Path, defect: str
) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, write_v3_catalog(tmp_path / f"v3-task-{defect}")).apply(db) == [2, 3]
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        insert_task_and_root(db, "created-task")
        payload = {
            "title": "forged-title" if defect == "title" else "created-task",
            "initial_branch_id": "forged-branch" if defect == "initial_branch" else "main",
        }
        if defect == "extra_key":
            payload["forged"] = True
        insert_event(
            db, task_id="created-task", branch_id="main", command_id="task-command",
            sequence=1, event_id="task-event", event_type="task.created", payload=payload,
            created_at=(
                "2026-07-14T00:01:00.000000Z"
                if defect == "timestamp" else "2026-07-14T00:00:00.000000Z"
            ),
            frame_version=1 if defect == "frame" else 0,
        )
        db.commit()
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings).apply(db) == [4]
    assert rows(
        settings.state_path,
        "SELECT last_transition_event_id FROM workbench_tasks WHERE id='created-task'",
    ) == [(None,)]
    assert rows(
        settings.state_path,
        "SELECT last_transition_event_id FROM workbench_branches "
        "WHERE task_id='created-task' AND branch_id='main'",
    ) == [(None,)]


@pytest.mark.asyncio
async def test_migration_four_does_not_authenticate_child_from_mismatched_fork_payload(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, write_v3_catalog(tmp_path / "v3-mismatch")).apply(db) == [2, 3]
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        insert_task_and_root(db, "created-task")
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,forked_from_sequence,"
            "forked_from_frame_version,created_by_event_id,status,created_at) "
            "VALUES ('created-task','child','main',1,0,'branch-event','active','2026-07-14T00:01:00.000000Z')"
        )
        insert_event(
            db, task_id="created-task", branch_id="main", command_id="task-command",
            sequence=1, event_id="task-event", event_type="task.created",
            payload={
                "title": "created-task",
                "initial_branch_id": "main",
            },
        )
        insert_event(
            db, task_id="created-task", branch_id="main", command_id="branch-command",
            sequence=2, event_id="branch-event", event_type="branch.forked",
            payload={
                "new_branch_id": "forged-child",
                "parent_branch_id": "main",
                "forked_from_sequence": 1,
                "forked_from_frame_version": 0,
            }, frame_version=0,
            created_at="2026-07-14T00:01:00.000000Z",
        )
        db.commit()
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings).apply(db) == [4]
    assert rows(
        settings.state_path,
        "SELECT branch_id,last_transition_event_id FROM workbench_branches "
        "WHERE task_id='created-task' ORDER BY branch_id",
    ) == [("child", None), ("main", "task-event")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ("equal_sequence", "earlier_sequence", "frame", "timestamp", "extra_key")
)
async def test_migration_four_child_backfill_requires_exact_parent_creation_boundary(
    tmp_path: Path, defect: str
) -> None:
    settings = settings_for(tmp_path)
    install_v1_fixture(settings)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, write_v3_catalog(tmp_path / f"v3-child-{defect}")).apply(db) == [2, 3]
    fork_sequence = 3 if defect == "earlier_sequence" else (2 if defect == "equal_sequence" else 1)
    event_sequence = 2
    event_frame = 1 if defect == "frame" else 0
    branch_time = "2026-07-14T00:01:00.000000Z"
    event_time = "2026-07-14T00:02:00.000000Z" if defect == "timestamp" else branch_time
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        insert_task_and_root(db, "created-task")
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,forked_from_sequence,"
            "forked_from_frame_version,created_by_event_id,status,created_at) "
            "VALUES ('created-task','child','main',?,0,'branch-event','active',?)",
            (fork_sequence, branch_time),
        )
        insert_event(
            db, task_id="created-task", branch_id="main", command_id="task-command",
            sequence=1, event_id="task-event", event_type="task.created",
            payload={
                "title": "created-task",
                "initial_branch_id": "main",
            },
        )
        fork_payload = {
            "new_branch_id": "child",
            "parent_branch_id": "main",
            "forked_from_sequence": fork_sequence,
            "forked_from_frame_version": 0,
        }
        if defect == "extra_key":
            fork_payload["forged"] = True
        insert_event(
            db, task_id="created-task", branch_id="main", command_id="branch-command",
            sequence=event_sequence, event_id="branch-event", event_type="branch.forked",
            payload=fork_payload, frame_version=event_frame, created_at=event_time,
        )
        db.commit()
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings).apply(db) == [4]
    assert rows(
        settings.state_path,
        "SELECT last_transition_event_id FROM workbench_branches "
        "WHERE task_id='created-task' AND branch_id='child'",
    ) == [(None,)]


@pytest.mark.asyncio
async def test_migration_four_rebuilds_exact_strict_decision_authority_schema(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()

    assert rows(settings.state_path, "SELECT version FROM schema_migrations ORDER BY version") == [
        (1,), (2,), (3,), (4,)
    ]
    assert hashlib.sha256((MIGRATIONS / "0002_workbench_core.sql").read_bytes()).hexdigest() == (
        "6b6156d7c3d6e8e488b51c85a8ab90f3b36564c7cecb507a38d565f4ee91586b"
    )
    assert hashlib.sha256((MIGRATIONS / "0003_workbench_command_manifests.sql").read_bytes()).hexdigest() == (
        "f97a4f387513a99f3d3b8f2c8e8bdc5d5986bf3af56140d31c4ba2f12735caca"
    )
    info = rows(settings.state_path, "PRAGMA table_info(decision_requests)")
    assert tuple(str(row[1]) for row in info) == DECISION_COLUMNS
    assert "STRICT" in table_sql(settings.state_path, "decision_requests").upper()
    assert {str(row[1]) for row in rows(settings.state_path, "PRAGMA index_list(decision_requests)")} == {
        "idx_decision_requests_one_active",
        "idx_decision_requests_active_identity",
        "sqlite_autoindex_decision_requests_2",
        "sqlite_autoindex_decision_requests_1",
    }
    foreign_keys = rows(settings.state_path, "PRAGMA foreign_key_list(decision_requests)")
    assert {(str(row[2]), str(row[3]), str(row[4])) for row in foreign_keys} >= {
        ("workbench_branches", "task_id", "task_id"),
        ("workbench_events", "task_id", "task_id"),
        ("workbench_events", "last_event_id", "event_id"),
    }


@pytest.mark.asyncio
async def test_migration_four_adds_exact_columns_queue_and_overlay_tables(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()

    proposal_columns = {str(row[1]) for row in rows(settings.state_path, "PRAGMA table_info(frame_proposals)")}
    assert {"base_state_checksum", "impact_preview_checksum", "decided_by_event_id"} <= proposal_columns
    service_columns = {str(row[1]) for row in rows(settings.state_path, "PRAGMA table_info(service_runs)")}
    assert {"revision", "last_event_id", "output_validity"} <= service_columns
    assert "last_transition_event_id" in {
        str(row[1]) for row in rows(settings.state_path, "PRAGMA table_info(workbench_tasks)")
    }
    assert "last_transition_event_id" in {
        str(row[1]) for row in rows(settings.state_path, "PRAGMA table_info(workbench_branches)")
    }
    queue_columns = tuple(str(row[1]) for row in rows(settings.state_path, "PRAGMA table_info(decision_queue_heads)"))
    assert queue_columns == (
        "task_id", "branch_id", "revision", "active_decision_id", "ordered_open_ids_json",
        "queue_checksum", "last_event_id", "updated_at",
    )
    overlay_columns = tuple(
        str(row[1]) for row in rows(settings.state_path, "PRAGMA table_info(evidence_invalidation_overlays)")
    )
    assert overlay_columns == (
        "task_id", "evidence_id", "invalidation_branch_id", "invalidated_at",
        "invalidated_by_event_id", "invalidated_by_event_sequence", "invalidation_reason",
    )
    assert "STRICT" in table_sql(settings.state_path, "decision_queue_heads").upper()
    assert "STRICT" in table_sql(settings.state_path, "evidence_invalidation_overlays").upper()
    evidence_indexes = rows(settings.state_path, "PRAGMA index_list(evidence_refs)")
    assert any(int(row[2]) == 1 for row in evidence_indexes)


@pytest.mark.asyncio
async def test_decision_schema_rejects_shadow_json_storage_and_cross_field_corruption(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "decision-task")
        valid = insert_decision_event_and_row(
            db, task_id="decision-task", decision_id="decision-valid", event_id="decision-event", sequence=1
        )
        db.execute(
            "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
            "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
            "VALUES ('decision-task','main',1,'decision-valid','[\"decision-valid\"]',?,'decision-event',?)",
            (SHA_A, valid["created_at"]),
        )
        db.commit()
        assert db.execute("SELECT decision_id FROM decision_requests").fetchall() == [("decision-valid",)]

        corruptions: tuple[tuple[str, object], ...] = (
            ("revision", 0),
            ("revision", 1.5),
            ("semantic_identity", SHA_B.upper()),
            ("options_json", "[ ]"),
            ("options_json", "{}"),
            ("positions_json", "{}"),
            ("materiality_json", "[]"),
            ("affected_node_keys_json", '["goal","goal"]'),
            ("affected_node_keys_json", '["z","a"]'),
            ("provenance_json", "[]"),
            ("source_occurrences_json", "{}"),
            ("recommendation_reason", "reason without recommendation"),
            ("pending_interpretation_json", "[]"),
            ("resolution_json", "{}"),
            ("decided_at", "2026-07-14T00:02:00.000000Z"),
        )
        for ordinal, (field, value) in enumerate(corruptions, start=2):
            candidate = decision_values(
                task_id="decision-task",
                decision_id=f"decision-bad-{ordinal}",
                event_id=f"bad-event-{ordinal}",
            )
            candidate[field] = value
            insert_event(
                db,
                task_id="decision-task",
                branch_id="main",
                command_id=f"bad-command-{ordinal}",
                sequence=2,
                event_id=f"bad-event-{ordinal}",
                event_type="decision.enqueued",
                payload={
                    "decision": {
                        "decision_id": candidate["decision_id"], "revision": 1, "state": "queued",
                        "queue_order": ordinal, "semantic_identity": SHA_B,
                    },
                    "resulting_queue": {
                        "active": {"decision_id": "decision-valid"}, "branch_id": "main", "checksum": SHA_A,
                        "queued": [{"decision_id": candidate["decision_id"]}], "revision": ordinal,
                        "task_id": "decision-task",
                    },
                },
            )
            with pytest.raises(sqlite3.IntegrityError):
                insert_decision_row(db, candidate)
            db.rollback()


@pytest.mark.asyncio
async def test_decision_options_positions_and_recommendation_references_are_exact(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "reference-task")
        base = decision_values(task_id="reference-task", decision_id="reference-decision", event_id="reference-event")
        invalid_pairs = (
            (
                '[{"description":"","label":"One","option_id":"o1","position_id":"missing"}]',
                '[{"position_id":"p1"}]', None, None,
            ),
            (
                '[{"description":"","label":"One","option_id":"o1","position_id":"p1"},'
                '{"description":"","label":"Duplicate","option_id":"o1","position_id":"p1"}]',
                '[{"position_id":"p1"}]', None, None,
            ),
            (
                '[{"description":"","label":"One","option_id":"o1","position_id":"p1"}]',
                '[{"position_id":"p1"},{"position_id":"p1"}]', None, None,
            ),
            (
                '[{"description":"","label":"One","option_id":"o1","position_id":"p1"}]',
                '[{"position_id":"p1"}]', "missing-option", "reason",
            ),
        )
        for ordinal, (options, positions, recommendation, reason) in enumerate(invalid_pairs):
            candidate = dict(base)
            candidate["decision_id"] = f"reference-bad-{ordinal}"
            candidate["created_by_event_id"] = f"reference-event-{ordinal}"
            candidate["last_event_id"] = f"reference-event-{ordinal}"
            candidate["created_at"] = f"2026-07-14T00:0{ordinal + 1}:00.000000Z"
            candidate["options_json"] = options
            candidate["positions_json"] = positions
            candidate["recommendation_option_id"] = recommendation
            candidate["recommendation_reason"] = reason
            insert_event(
                db, task_id="reference-task", branch_id="main", command_id=f"reference-command-{ordinal}",
                sequence=ordinal + 1, event_id=f"reference-event-{ordinal}", event_type="decision.enqueued",
                payload={"decision": {
                    "decision_id": candidate["decision_id"], "revision": 1, "state": "active",
                    "queue_order": 0, "semantic_identity": SHA_B,
                }},
                created_at=str(candidate["created_at"]),
            )
            with pytest.raises(sqlite3.IntegrityError, match="option and position"):
                insert_decision_row(db, candidate)


@pytest.mark.asyncio
async def test_queue_final_state_enforces_active_first_contiguous_physical_reorder(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "reorder-task")

        first = insert_decision_event_and_row(
            db, task_id="reorder-task", decision_id="d1", event_id="e1", sequence=1,
        )
        db.execute(
            "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
            "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
            "VALUES ('reorder-task','main',1,'d1','[\"d1\"]',?,'e1',?)",
            (SHA_A, first["created_at"]),
        )
        queue_rows = {"d1": first}
        for sequence, decision_id, checksum in ((2, "d2", "c" * 64), (3, "d3", "d" * 64)):
            queued_ids = [f"d{number}" for number in range(2, sequence + 1)]
            values = decision_values(
                task_id="reorder-task", decision_id=decision_id, event_id=f"e{sequence}",
                state="queued", queue_order=sequence - 1,
                created_at=f"2026-07-14T00:0{sequence}:00.000000Z",
            )
            values["semantic_identity"] = hashlib.sha256(decision_id.encode()).hexdigest()
            queue_rows[decision_id] = values
            payload = {
                "candidate": {"source": json.loads(str(values["source_occurrences_json"]))[0]},
                "assessment": {"disposition": "ask", "semantic_identity": values["semantic_identity"]},
                "decision": decision_snapshot(values),
                "resulting_queue": {
                    "active": decision_snapshot(queue_rows["d1"]),
                    "branch_id": "main", "checksum": checksum,
                    "queued": [decision_snapshot(queue_rows[item]) for item in queued_ids],
                    "revision": sequence,
                    "task_id": "reorder-task",
                },
            }
            insert_event(
                db, task_id="reorder-task", branch_id="main", command_id=f"command-e{sequence}",
                sequence=sequence, event_id=f"e{sequence}", event_type="decision.enqueued", payload=payload,
                created_at=f"2026-07-14T00:0{sequence}:00.000000Z",
            )
            insert_decision_row(db, values)
            ordered = json.dumps(["d1", *queued_ids], separators=(",", ":"))
            db.execute(
                "UPDATE decision_queue_heads SET revision=?,ordered_open_ids_json=?,queue_checksum=?,"
                "last_event_id=?,updated_at=? WHERE task_id='reorder-task' AND branch_id='main'",
                (sequence, ordered, checksum, f"e{sequence}", f"2026-07-14T00:0{sequence}:00.000000Z"),
            )

        reordered_d2 = dict(queue_rows["d2"], revision=2, queue_order=2)
        reordered_d3 = dict(queue_rows["d3"], revision=2, queue_order=1)
        insert_event(
            db, task_id="reorder-task", branch_id="main", command_id="reorder-command", sequence=4,
            event_id="reorder-event", event_type="decision.queue_reordered",
            payload={
                "expected_queue_revision": 3, "expected_queue_checksum": "d" * 64,
                "queued_decision_ids": ["d3", "d2"],
                "resulting_queue": {
                    "active": decision_snapshot(queue_rows["d1"]),
                    "branch_id": "main", "checksum": SHA_B,
                    "queued": [decision_snapshot(reordered_d3), decision_snapshot(reordered_d2)],
                    "revision": 4,
                    "task_id": "reorder-task",
                },
            },
            created_at="2026-07-14T00:04:00.000000Z",
        )
        db.execute(
            "UPDATE decision_requests SET revision=2,queue_order=2,last_event_id='reorder-event' WHERE decision_id='d2'"
        )
        db.execute(
            "UPDATE decision_requests SET revision=2,queue_order=1,last_event_id='reorder-event' WHERE decision_id='d3'"
        )
        db.execute(
            "UPDATE decision_queue_heads SET revision=4,ordered_open_ids_json='[\"d1\",\"d3\",\"d2\"]',"
            "queue_checksum=?,last_event_id='reorder-event',updated_at='2026-07-14T00:04:00.000000Z' "
            "WHERE task_id='reorder-task' AND branch_id='main'",
            (SHA_B,),
        )
        assert db.execute(
            "SELECT decision_id,queue_order FROM decision_requests WHERE state='queued' ORDER BY queue_order"
        ).fetchall() == [("d3", 1), ("d2", 2)]

        gap_d2 = dict(queue_rows["d2"], revision=3, queue_order=3)
        gap_d3 = dict(queue_rows["d3"], revision=3, queue_order=1)
        insert_event(
            db, task_id="reorder-task", branch_id="main", command_id="gap-command", sequence=5,
            event_id="gap-event", event_type="decision.queue_reordered",
            payload={
                "expected_queue_revision": 4, "expected_queue_checksum": SHA_B,
                "queued_decision_ids": ["d3", "d2"],
                "resulting_queue": {
                    "active": decision_snapshot(queue_rows["d1"]),
                    "branch_id": "main", "checksum": SHA_A,
                    "queued": [decision_snapshot(gap_d3), decision_snapshot(gap_d2)],
                    "revision": 5,
                    "task_id": "reorder-task",
                },
            },
            created_at="2026-07-14T00:05:00.000000Z",
        )
        db.execute(
            "UPDATE decision_requests SET revision=3,queue_order=3,last_event_id='gap-event' WHERE decision_id='d2'"
        )
        db.execute(
            "UPDATE decision_requests SET revision=3,queue_order=1,last_event_id='gap-event' WHERE decision_id='d3'"
        )
        with pytest.raises(sqlite3.IntegrityError, match="resulting queue|final state"):
            db.execute(
                "UPDATE decision_queue_heads SET revision=5,queue_checksum=?,last_event_id='gap-event',"
                "updated_at='2026-07-14T00:05:00.000000Z' "
                "WHERE task_id='reorder-task' AND branch_id='main'",
                (SHA_A,),
            )


@pytest.mark.asyncio
async def test_queue_head_binds_event_order_active_checksum_time_revision_and_delete(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "queue-task")
        values = insert_decision_event_and_row(
            db, task_id="queue-task", decision_id="queue-decision", event_id="queue-event", sequence=1
        )
        valid = (
            "queue-task", "main", 1, "queue-decision", '["queue-decision"]', SHA_A,
            "queue-event", values["created_at"],
        )
        sql = (
            "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
            "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) VALUES (?,?,?,?,?,?,?,?)"
        )
        for index, replacement in (
            (2, 0),
            (3, None),
            (4, '["other"]'),
            (5, SHA_B),
            (6, "missing-event"),
            (7, "2026-07-14T00:09:00.000000Z"),
        ):
            candidate = list(valid)
            candidate[index] = replacement
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, tuple(candidate))
        db.execute(sql, valid)
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE decision_queue_heads SET revision=3 WHERE task_id='queue-task' AND branch_id='main'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM decision_queue_heads WHERE task_id='queue-task' AND branch_id='main'")


@pytest.mark.asyncio
async def test_decision_update_requires_exact_later_event_and_legal_terminal_shape(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "transition-task")
        base = insert_decision_event_and_row(
            db, task_id="transition-task", decision_id="transition-decision",
            event_id="enqueue-event", sequence=1,
        )
        reply = {
            "occurrence_kind": "reply", "occurrence_id": "resolution-reply",
            "reply_kind": "free_form", "reply": {"text": "Proceed"},
            "native_reply_checksum": SHA_A, "received_at": "2026-07-14T00:02:00.000000Z",
            "provenance": {}, "classification": "accepted",
            "original_occurrence_event_id": None,
        }
        resolved = dict(base)
        resolved.update(
            revision=2, last_event_id="resolve-event", state="resolved",
            source_occurrences_json=json.dumps(
                [*json.loads(str(base["source_occurrences_json"])), reply],
                sort_keys=True, separators=(",", ":"),
            ),
            resolution_json="{}", decided_at="2026-07-14T00:02:00.000000Z",
        )
        insert_event(
            db,
            task_id="transition-task",
            branch_id="main",
            command_id="resolve-command",
            sequence=2,
            event_id="resolve-event",
            event_type="decision.resolved",
            payload={
                "decision_id": "transition-decision",
                "occurrence": reply, "resolution": {}, "expected_decision_revision": 1,
                "resulting_decision": decision_snapshot(resolved),
            },
            created_at="2026-07-14T00:02:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE decision_requests SET state='resolved',revision=3,last_event_id='resolve-event',"
                "resolution_json='{}',decided_at='2026-07-14T00:02:00.000000Z' "
                "WHERE decision_id='transition-decision'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE decision_requests SET state='resolved',revision=2,last_event_id='resolve-event',"
                "resolution_json=NULL,decided_at='2026-07-14T00:02:00.000000Z' "
                "WHERE decision_id='transition-decision'"
            )
        db.execute(
            "UPDATE decision_requests SET state='resolved',revision=2,last_event_id='resolve-event',"
            "resolution_json='{}',decided_at='2026-07-14T00:02:00.000000Z',"
            "source_occurrences_json=? WHERE decision_id='transition-decision'",
            (resolved["source_occurrences_json"],),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE decision_requests SET state='active',revision=3,last_event_id='resolve-event',"
                "resolution_json=NULL,decided_at=NULL WHERE decision_id='transition-decision'"
            )


@pytest.mark.asyncio
async def test_proposal_and_service_run_transitions_are_event_bound_and_immutable(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "transition-authority")
        insert_event(
            db,
            task_id="transition-authority", branch_id="main", command_id="proposal-command",
            sequence=1, event_id="proposal-event", event_type="frame.proposal_created",
            payload={"proposal": {
                "proposal_id": "proposal-1",
                "base": {
                    "task_id": "transition-authority", "branch_id": "main", "frame_version": 0,
                    "head_sequence": 0, "head_event_checksum": SHA_A,
                    "frame_state_checksum": SHA_A,
                },
                "proposed_version": 1,
                "changes": {"edge_operations": [], "node_operations": []},
                "preview": {}, "preview_state_checksum": SHA_B,
                "impact": {}, "impact_preview_checksum": SHA_B,
                "status": "pending", "provenance": {},
            }},
        )
        db.execute(
            """
            INSERT INTO frame_proposals(
                id,task_id,branch_id,base_version,proposed_version,expected_head_sequence,
                base_head_checksum,base_state_checksum,preview_state_checksum,impact_preview_checksum,
                status,changes_json,impact_preview_json,provenance_json,created_by,
                created_by_event_id,decided_by_event_id,created_at,decided_at
            ) VALUES (
                'proposal-1','transition-authority','main',0,1,0,?,?,?,?,
                'pending','{"edge_operations":[],"node_operations":[]}','{}','{}','system:test',
                'proposal-event',NULL,'2026-07-14T00:00:00.000000Z',NULL
            )
            """,
            (SHA_A, SHA_A, SHA_B, SHA_B),
        )
        insert_event(
            db,
            task_id="transition-authority", branch_id="main", command_id="confirm-command",
            sequence=2, event_id="confirm-event", event_type="frame.change_confirmed",
            payload={
                "proposal_id": "proposal-1", "expected_preview_state_checksum": SHA_B,
                "expected_impact_preview_checksum": SHA_B, "proposal_supersessions": [],
            }, frame_version=1,
            created_at="2026-07-14T00:02:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE frame_proposals SET id='rewritten',status='accepted',decided_by_event_id='confirm-event',"
                "decided_at='2026-07-14T00:02:00.000000Z' WHERE id='proposal-1'"
            )
        db.execute(
            "UPDATE frame_proposals SET status='accepted',decided_by_event_id='confirm-event',"
            "decided_at='2026-07-14T00:02:00.000000Z' WHERE id='proposal-1'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE frame_proposals SET status='pending' WHERE id='proposal-1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM frame_proposals WHERE id='proposal-1'")

        insert_event(
            db,
            task_id="transition-authority", branch_id="main", command_id="run-register-command",
            sequence=3, event_id="run-register-event", event_type="service_run.registered",
            payload={
                "run_id": "run-1", "service": "codex", "role": "implementer",
                "task_id": "transition-authority", "branch_id": "main", "frame_version": 1,
                "initial_state": "queued", "output_validity": "current",
                "input_manifest": {}, "input_node_ids": [], "native_identity_envelope": None,
                "expected_task_cache": None, "resulting_task_state": "intake",
            }, frame_version=1,
            created_at="2026-07-14T00:03:00.000000Z",
        )
        db.execute(
            """
            INSERT INTO service_runs(
                run_id,task_id,service,role,frame_version,branch_id,state,receipt_event_id,
                input_manifest_json,started_at,updated_at,completed_at,revision,last_event_id,output_validity
            ) VALUES (
                'run-1','transition-authority','codex','implementer',1,'main','queued',NULL,
                '{}',NULL,'2026-07-14T00:03:00.000000Z',NULL,1,'run-register-event','current'
            )
            """
        )
        insert_event(
            db,
            task_id="transition-authority", branch_id="main", command_id="run-start-command",
            sequence=4, event_id="run-start-event", event_type="service_run.state_changed",
            payload={
                "run_id": "run-1", "expected_revision": 1, "expected_state": "queued",
                "expected_output_validity": "current", "new_state": "starting",
                "reason_code": "dispatch_started", "resulting_output_validity": "current",
                "native_identity_attachment": None, "expected_task_cache": None,
                "resulting_task_state": "intake",
            }, frame_version=1,
            created_at="2026-07-14T00:04:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE service_runs SET state='starting',revision=3,last_event_id='run-start-event',"
                "started_at='2026-07-14T00:04:00.000000Z',updated_at='2026-07-14T00:04:00.000000Z' "
                "WHERE run_id='run-1'"
            )
        db.execute(
            "UPDATE service_runs SET state='starting',revision=2,last_event_id='run-start-event',"
            "started_at='2026-07-14T00:04:00.000000Z',updated_at='2026-07-14T00:04:00.000000Z' "
            "WHERE run_id='run-1'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE service_runs SET input_manifest_json='{\"changed\":true}' WHERE run_id='run-1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM service_runs WHERE run_id='run-1'")
        db.commit()
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "INSERT INTO service_run_inputs(task_id,run_id,node_id,input_frame_version,ordinal) "
            "VALUES ('transition-authority','run-1','input-node',1,0)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE service_run_inputs SET ordinal=1 WHERE run_id='run-1'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM service_run_inputs WHERE run_id='run-1'")


@pytest.mark.asyncio
async def test_task_and_branch_cache_updates_require_exact_transition_events(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "cache-task")
        insert_event(
            db,
            task_id="cache-task", branch_id="main", command_id="activate-command", sequence=1,
            event_id="activate-event", event_type="branch.activated",
            payload={
                "source_branch_id": "main", "target_branch_id": "main",
                "expected_task_cache": {
                    "expected_selected_branch_id": "main", "expected_frame_version": 0,
                    "expected_state": "intake", "expected_updated_at": "2026-07-14T00:00:00.000000Z",
                    "expected_last_transition_event_id": None,
                },
                "target_frame_version": 0, "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:01:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE workbench_tasks SET state='running' WHERE id='cache-task'")
        db.execute(
            "UPDATE workbench_tasks SET updated_at='2026-07-14T00:01:00.000000Z',"
            "last_transition_event_id='activate-event' WHERE id='cache-task'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE workbench_tasks SET active_branch_id='missing',updated_at='2026-07-14T00:01:00.000000Z',"
                "last_transition_event_id='activate-event' WHERE id='cache-task'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE workbench_branches SET status='abandoned' WHERE task_id='cache-task'")


@pytest.mark.asyncio
async def test_frame_node_mapping_overlay_visibility_and_immutable_edges_inputs(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_and_root(db, "frame-task")
        changes = {
            "edge_operations": [],
            "node_operations": [{
                "depends_on_node_keys": [],
                "expected_current_node_id": None,
                "kind": "success",
                "new_node_id": "success-node",
                "node_key": "success",
                "operation_id": "upsert-success",
                "operation_kind": "upsert",
                "provenance": {"source_event_ids": []},
                "text": "Verified outcome",
                "value": {"predicate": "exists"},
            }],
        }
        insert_event(
            db,
            task_id="frame-task", branch_id="main", command_id="frame-proposal-command",
            sequence=1, event_id="frame-proposal-event", event_type="frame.proposal_created",
            payload={"proposal": {
                "proposal_id": "frame-proposal",
                "base": {
                    "task_id": "frame-task", "branch_id": "main", "frame_version": 0,
                    "head_sequence": 0, "head_event_checksum": SHA_A,
                    "frame_state_checksum": SHA_A,
                },
                "proposed_version": 1, "changes": changes, "preview": {},
                "preview_state_checksum": SHA_B, "impact": {},
                "impact_preview_checksum": SHA_B, "status": "pending", "provenance": {},
            }},
        )
        db.execute(
            """
            INSERT INTO frame_proposals(
                id,task_id,branch_id,base_version,proposed_version,expected_head_sequence,
                base_head_checksum,base_state_checksum,preview_state_checksum,impact_preview_checksum,
                status,changes_json,impact_preview_json,provenance_json,created_by,
                created_by_event_id,decided_by_event_id,created_at,decided_at
            ) VALUES (
                'frame-proposal','frame-task','main',0,1,0,?,?,?,?,'pending',?,'{}','{}','system:test',
                'frame-proposal-event',NULL,'2026-07-14T00:00:00.000000Z',NULL
            )
            """,
            (SHA_A, SHA_A, SHA_B, SHA_B, json.dumps(changes, sort_keys=True, separators=(",", ":"))),
        )
        confirmed_node = {
            "branch_id": "main", "depends_on": [], "frame_version": 1, "kind": "success",
            "node_id": "success-node", "node_key": "success", "provenance_event_ids": [],
            "status": "confirmed", "supersedes_node_id": None, "task_id": "frame-task",
            "text": "Verified outcome", "value": {"predicate": "exists"},
        }
        insert_event(
            db,
            task_id="frame-task", branch_id="main", command_id="frame-confirm-command",
            sequence=2, event_id="frame-confirm-event", event_type="frame.change_confirmed",
            payload={
                "proposal_id": "frame-proposal",
                "expected_preview_state_checksum": SHA_B,
                "expected_impact_preview_checksum": SHA_B,
                "proposal_supersessions": [],
                "confirmed": {
                    "branch_id": "main", "edges": [{
                        "edge_id": "edge", "task_id": "frame-task", "branch_id": "main",
                        "frame_version": 1, "from_node_id": "success-node",
                        "to_node_id": "other-node", "relation": "supports",
                    }], "frame_version": 1,
                    "nodes": [confirmed_node], "state_checksum": SHA_A, "task_id": "frame-task",
                },
            }, frame_version=1,
            created_at="2026-07-14T00:02:00.000000Z",
        )
        db.execute(
            "UPDATE frame_proposals SET status='accepted',decided_by_event_id='frame-confirm-event',"
            "decided_at='2026-07-14T00:02:00.000000Z' WHERE id='frame-proposal'"
        )
        node_values = (
            "success-node", "frame-task", "main", 1, "success", None, "success", "Verified outcome",
            '{"predicate":"exists"}', "confirmed", "[]", "[]", '{"source_event_ids":[]}',
            "frame-confirm-event", None, "2026-07-14T00:02:00.000000Z", "2026-07-14T00:02:00.000000Z",
        )
        node_sql = (
            "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,supersedes_node_id,kind,text,"
            "value_json,status,depends_on_json,provenance_event_ids_json,provenance_json,created_by_event_id,"
            "invalidated_by_event_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        bad_node = list(node_values)
        bad_node[7] = "Rewritten text"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(node_sql, tuple(bad_node))
        db.execute(node_sql, node_values)
        db.execute(
            "INSERT INTO evidence_refs(id,task_id,branch_id,success_node_id,success_node_frame_version,frame_version,"
            "observed_head_event_id,observed_head_sequence,predicate_id,predicate_text,expected_outcome,authority_kind,"
            "authority_locator,verifier_kind,verifier_identity,verification_status,observed_at,observed_value_checksum,"
            "metadata_json) VALUES ('evidence-1','frame-task','main','success-node',1,1,'frame-confirm-event',2,"
            "'predicate','exists','present','filesystem','C:/proof','script','verifier','pass',"
            "'2026-07-14T00:02:00.000000Z',?,'{}')",
            (SHA_A,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE evidence_refs SET invalidated_at='2026-07-14T00:03:00.000000Z',"
                "invalidated_event_id='future',invalidated_event_sequence=3,invalidation_reason='changed' "
                "WHERE id='evidence-1'"
            )
        insert_event(
            db,
            task_id="frame-task", branch_id="main", command_id="invalidate-command", sequence=3,
            event_id="invalidate-event", event_type="frame.change_confirmed",
            payload={"proposal_id": "later", "impact": {"evidence_impacts": [{
                "evidence_id": "evidence-1", "invalidation_reason": "evidence_conflict",
            }]}}, frame_version=2,
            created_at="2026-07-14T00:03:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO evidence_invalidation_overlays(task_id,evidence_id,invalidation_branch_id,invalidated_at,"
                "invalidated_by_event_id,invalidated_by_event_sequence,invalidation_reason) "
                "VALUES ('frame-task','evidence-1','main','wrong','invalidate-event',3,'evidence_conflict')"
            )
        db.execute(
            "INSERT INTO evidence_invalidation_overlays(task_id,evidence_id,invalidation_branch_id,invalidated_at,"
            "invalidated_by_event_id,invalidated_by_event_sequence,invalidation_reason) "
            "VALUES ('frame-task','evidence-1','main','2026-07-14T00:03:00.000000Z',"
            "'invalidate-event',3,'evidence_conflict')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE evidence_invalidation_overlays SET invalidation_reason='changed'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM evidence_invalidation_overlays")

        db.commit()
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "INSERT INTO frame_edges(id,task_id,branch_id,frame_version,from_node_id,to_node_id,edge_type,"
            "created_by_event_id,created_at) VALUES ('edge','frame-task','main',1,'success-node','other-node',"
            "'supports','frame-confirm-event','2026-07-14T00:02:00.000000Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE frame_edges SET edge_type='contradicts' WHERE id='edge'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM frame_edges WHERE id='edge'")


@pytest.mark.asyncio
async def test_migration_four_storage_and_immutability_guards_are_installed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    expected_triggers = {
        "frame_nodes_task3_validate_insert",
        "frame_edges_task3_validate_insert",
        "frame_edges_no_update",
        "frame_edges_no_delete",
        "frame_proposals_task3_transition",
        "frame_proposals_task3_no_delete",
        "decision_requests_task3_transition",
        "decision_requests_task3_no_delete",
        "decision_queue_heads_validate_insert",
        "decision_queue_heads_validate_update",
        "decision_queue_heads_no_delete",
        "service_runs_task3_transition",
        "service_runs_task3_no_delete",
        "service_run_inputs_no_update",
        "service_run_inputs_no_delete",
        "evidence_refs_task3_no_update",
        "evidence_invalidation_overlays_validate_insert",
        "evidence_invalidation_overlays_no_update",
        "evidence_invalidation_overlays_no_delete",
        "workbench_tasks_task3_transition",
        "workbench_branches_task3_validate_insert",
        "workbench_branches_task3_transition",
    }
    installed = {str(row[0]) for row in rows(settings.state_path, "SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert expected_triggers <= installed
    assert "NEW.revision <> 1" in trigger_sql(settings.state_path, "decision_queue_heads_validate_insert")
    assert "NEW.revision <> OLD.revision + 1" in trigger_sql(
        settings.state_path, "decision_queue_heads_validate_update"
    )
    assert "frame.change_confirmed" in trigger_sql(
        settings.state_path, "evidence_invalidation_overlays_validate_insert"
    )
    assert "proposed" in trigger_sql(settings.state_path, "frame_nodes_task3_validate_insert")
    assert "rejected" in trigger_sql(settings.state_path, "frame_nodes_task3_validate_insert")


@pytest.mark.asyncio
async def test_decision_queue_head_rejects_revision_zero_fractional_json_and_delete(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        for revision, open_ids in ((0, '["d"]'), (1.5, '["d"]'), (1, '{"d":1}')):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
                    "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    ("t", "main", revision, "d", open_ids, SHA_A, "e", "2026-07-14T00:00:00.000000Z"),
                )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
                "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
                "VALUES ('t','main',1,'d','[\"d\"]',?,'e','2026-07-14T00:00:00.000000Z')",
                (sqlite3.Binary(b"a" * 64),),
            )


@pytest.mark.asyncio
async def test_frame_nodes_reject_nonproduction_status_and_legacy_evidence_invalidation_is_frozen(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        for status in ("proposed", "rejected"):
            with pytest.raises(sqlite3.IntegrityError, match="confirmed or invalidated"):
                db.execute(
                    "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,kind,text,status,"
                    "provenance_json,created_at,updated_at) VALUES (?,?,?,?,?,'goal','text',?,'{}','now','now')",
                    (f"node-{status}", "t", "main", 1, "goal", status),
                )


@pytest.mark.asyncio
async def test_two_concurrent_initializers_apply_migration_four_once(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await asyncio.wait_for(
        asyncio.gather(StateStore(settings).initialize(), StateStore(settings).initialize()),
        timeout=20,
    )
    assert rows(settings.state_path, "SELECT version,COUNT(*) FROM schema_migrations GROUP BY version") == [
        (1, 1), (2, 1), (3, 1), (4, 1)
    ]
    backups = sorted((settings.home / "backups").glob("state-pre-migration-*.sqlite"))
    assert len(backups) == 1


def test_wheel_contains_and_loads_migration_four_and_failure_surface(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(ROOT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "orchestrator/state/migrations/0004_intent_authority_guards.sql" in names
    assert "orchestrator/workbench/failures.py" in names
    site = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(site), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    env = {**os.environ, "PYTHONPATH": str(site)}
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib.resources import files; "
            "from orchestrator.workbench.failures import Task3Failure; "
            "sql=files('orchestrator.state').joinpath('migrations','0004_intent_authority_guards.sql').read_text(); "
            "assert 'decision_queue_heads' in sql; assert Task3Failure.__name__ == 'Task3Failure'",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert loaded.returncode == 0, loaded.stderr
