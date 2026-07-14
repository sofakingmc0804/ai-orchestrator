from __future__ import annotations

import asyncio
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
from orchestrator.state.migration_runner import (
    MigrationApplyError,
    MigrationBackupError,
    MigrationCatalogError,
    MigrationChecksumError,
    MigrationRunner,
    split_sql_statements,
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
                          'acct-1','profile-1','claude-opus','cap-v7','transport-v2','session-2','thread-1',
                          'turn-1','request-1','governed','now')
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
        db.execute(
            """
            INSERT INTO workbench_events(
                event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,branch_id,
                command_id,command_sequence,frame_version,payload_json,idempotency_key,prior_checksum,checksum,created_at
            ) VALUES ('event-a','a',1,'evidence.observed',1,'service','run-a','main','cmd-a',1,3,'{}','idem-a','','sum-a','now')
            """
        )
        db.execute(
            "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,kind,text,status,provenance_json,"
            "created_at,updated_at) VALUES ('success-a','a','main',3,'success','success','Complete','confirmed','{}','now','now')"
        )
        db.execute(
            "INSERT INTO service_runs(run_id,task_id,service,role,frame_version,branch_id,state,updated_at) "
            "VALUES ('run-a','a','codex','verifier',3,'main','verifying','now')"
        )
        db.execute(
            """
            INSERT INTO evidence_refs(
                id,task_id,branch_id,success_node_id,frame_version,observed_head_sequence,predicate_id,
                predicate_text,predicate_json,expected_outcome,authority_kind,authority_locator,verifier_kind,
                verifier_identity,verification_status,observed_at,valid_until,observed_value_checksum,
                content_checksum,source_event_id,producing_run_id,metadata_json
            ) VALUES (
                'evidence-a','a','main','success-a',3,7,'predicate-1','Output exists',
                '{"op":"exists"}','present','filesystem','C:/proof/result.json','script','verify-result-v1',
                'pass','2026-07-13T20:00:00Z','2026-07-14T20:00:00Z','observed-sum','content-sum',
                'event-a','run-a','{"receipt":"r1"}'
            )
            """
        )
        row = db.execute(
            """
            SELECT branch_id,success_node_id,frame_version,observed_head_sequence,predicate_id,predicate_text,
                   predicate_json,expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                   verification_status,observed_at,valid_until,observed_value_checksum,content_checksum,
                   source_event_id,producing_run_id,metadata_json
            FROM evidence_refs WHERE id='evidence-a'
            """
        ).fetchone()
        assert row[0:6] == ("main", "success-a", 3, 7, "predicate-1", "Output exists")
        assert row[12] == "pass"
        assert row[-2:] == ("run-a", '{"receipt":"r1"}')
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,frame_version,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,metadata_json
                ) VALUES ('cross','b','main','success-a',3,7,'p','ok','filesystem','x','script','v','pass','now','sum','{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,frame_version,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,metadata_json
                ) VALUES ('wrong-frame','a','main','success-a',2,7,'p','ok','filesystem','x','script','v',
                          'pass','now','sum','{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,frame_version,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,invalidated_at,metadata_json
                ) VALUES ('partial-invalidation','a','main','success-a',3,7,'p','ok','filesystem','x','script','v',
                          'pass','now','sum','now','{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO evidence_refs(
                    id,task_id,branch_id,success_node_id,frame_version,observed_head_sequence,predicate_id,
                    expected_outcome,authority_kind,authority_locator,verifier_kind,verifier_identity,
                    verification_status,observed_at,observed_value_checksum,metadata_json
                ) VALUES ('bad-status','a','main','success-a',3,7,'p','ok','filesystem','x','script','v',
                          'unverified','now','sum','{}')
                """
            )


@pytest.mark.asyncio
async def test_quarantine_stages_before_activation_and_requires_replacement_proof(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute(
            """
            INSERT INTO quarantined_components(
                id,component_type,component_id,behavior,state,manifest_json,manifest_checksum,staged_at,
                preserved_read,metadata_json
            ) VALUES ('q1','scheduled_task','legacy-pinger','periodically pings a UI','candidate',
                      '{"action":"disable_after_proof"}','manifest-sum','now',1,'{}')
            """
        )
        assert db.execute("SELECT state FROM quarantined_components WHERE id='q1'").fetchone() == ("candidate",)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,component_type,component_id,behavior,state,manifest_json,manifest_checksum,staged_at,
                    preserved_read,metadata_json
                ) VALUES ('q2','scheduled_task','legacy-pinger','duplicate','shadow_readonly','{}','sum','now',1,'{}')
                """
            )
        db.execute(
            "UPDATE quarantined_components SET state='released',released_at='now',release_reason='superseded' WHERE id='q1'"
        )
        db.execute(
            """
            INSERT INTO quarantined_components(
                id,component_type,component_id,behavior,state,manifest_json,manifest_checksum,staged_at,
                preserved_read,metadata_json
            ) VALUES ('q2','scheduled_task','legacy-pinger','shadow replacement','shadow_readonly','{}','sum','now',1,'{}')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE quarantined_components SET state='active' WHERE id='q2'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,component_type,component_id,behavior,state,manifest_json,manifest_checksum,staged_at,
                    replacement_bootstrap_receipt_id,activated_at,activated_event_id,preserved_read,metadata_json
                ) VALUES ('direct-active','scheduled_task','other-pinger','skipped staging','active','{}','sum','now',
                          'receipt-1','now','event-proof',1,'{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO quarantined_components(
                    id,component_type,component_id,behavior,state,manifest_json,manifest_checksum,staged_at,
                    legacy_data_store,preserved_read,metadata_json
                ) VALUES ('legacy-missing-proof','sqlite_store','legacy-db','legacy rows','candidate','{}','sum','now',
                          1,1,'{}')
                """
            )
        db.execute(
            """
            UPDATE quarantined_components
            SET replacement_bootstrap_receipt_id='receipt-2',activated_at='now',activated_event_id='event-proof',
                state='active'
            WHERE id='q2'
            """
        )
        assert db.execute("SELECT state FROM quarantined_components WHERE id='q2'").fetchone() == ("active",)


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
