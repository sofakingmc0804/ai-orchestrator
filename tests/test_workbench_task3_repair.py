from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from orchestrator.config import Settings
from orchestrator.state.migration_runner import MigrationRunner, _ParsedPreconditionMarker
from orchestrator.state.store import StateStore
from orchestrator.workbench.models import EventActor
from orchestrator.workbench.store import WorkbenchStore


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "orchestrator" / "state" / "migrations"
V1_FIXTURE = ROOT / "tests" / "fixtures" / "state_v1.sql"
SHA_A = "a" * 64
SHA_B = "b" * 64
NOW = "2026-07-14T00:00:00.000000Z"


def settings_for(tmp_path: Path) -> Settings:
    home = tmp_path / "runtime"
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=ROOT,
    )


async def install_v3_with_preexisting_authority(settings: Settings, tmp_path: Path) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.state_path) as db:
        db.executescript(V1_FIXTURE.read_text(encoding="utf-8"))
    catalog = tmp_path / "v3-catalog"
    catalog.mkdir()
    for name in ("0002_workbench_core.sql", "0003_workbench_command_manifests.sql"):
        shutil.copyfile(MIGRATIONS / name, catalog / name)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await MigrationRunner(settings, catalog).apply(db) == [2, 3]
    with sqlite3.connect(settings.state_path) as db:
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
            "VALUES ('legacy','Legacy','intake','main',0,'now','now')"
        )
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('legacy','main','active','now')"
        )
        db.execute(
            "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,kind,text,status,"
            "provenance_json,created_at,updated_at) VALUES "
            "('node','legacy','main',0,'goal','goal','Legacy','confirmed','{}','now','now')"
        )
        db.commit()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def insert_task_root(db: sqlite3.Connection, task_id: str) -> None:
    db.execute(
        "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,created_at,updated_at) "
        "VALUES (?,?,'intake','main',0,?,?)",
        (task_id, task_id, NOW, NOW),
    )
    db.execute(
        "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) VALUES (?,'main','active',?)",
        (task_id, NOW),
    )


def insert_event(
    db: sqlite3.Connection,
    *,
    task_id: str,
    event_id: str,
    sequence: int,
    event_type: str,
    payload: dict[str, object],
    branch_id: str = "main",
    frame_version: int = 0,
    created_at: str = NOW,
) -> None:
    command_id = f"command-{event_id}"
    db.execute(
        """
        INSERT INTO workbench_command_manifests(
            task_id,command_id,target_branch_id,event_count,first_sequence,last_sequence,
            first_event_id,last_event_id,starting_frame_version,expected_frame_version,
            confirm_ordinal,drafts_checksum,manifest_checksum,created_at
        ) VALUES (?,?,?,1,?,?,?,?,?,?,NULL,?,?,?)
        """,
        (
            task_id,
            command_id,
            branch_id,
            sequence,
            sequence,
            event_id,
            event_id,
            frame_version,
            frame_version,
            SHA_A,
            SHA_B,
            created_at,
        ),
    )
    db.execute(
        """
        INSERT INTO workbench_events(
            event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,
            branch_id,command_id,command_sequence,frame_version,payload_json,idempotency_key,
            prior_checksum,checksum,created_at
        ) VALUES (?,?,?,?,1,'system','test',?,?,1,?,?,?,?,?,?)
        """,
        (
            event_id,
            task_id,
            sequence,
            event_type,
            branch_id,
            command_id,
            frame_version,
            canonical(payload),
            f"idem-{event_id}",
            "" if sequence == 1 else f"prior-{sequence}",
            f"sum-{sequence}",
            created_at,
        ),
    )


def decision_row(
    *,
    task_id: str,
    decision_id: str,
    event_id: str,
    state: str = "active",
    queue_order: int = 0,
    revision: int = 1,
    source_occurrences: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    occurrences = source_occurrences or [
        {
            "classification": "accepted",
            "native_question_identity_checksum": SHA_A,
            "occurrence_id": f"question-{decision_id}",
            "occurrence_kind": "question",
        }
    ]
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
        "source_occurrences_json": canonical(occurrences),
        "pending_interpretation_json": None,
        "resolution_json": None,
        "created_by_event_id": event_id,
        "created_at": NOW,
        "decided_at": None,
    }


def decision_snapshot(row: dict[str, object]) -> dict[str, object]:
    return {
        "decision_id": row["decision_id"],
        "task_id": row["task_id"],
        "branch_id": row["branch_id"],
        "revision": row["revision"],
        "state": row["state"],
        "tier": row["tier"],
        "kind": row["kind"],
        "queue_order": row["queue_order"],
        "semantic_identity": row["semantic_identity"],
        "question": row["question"],
        "options": json.loads(str(row["options_json"])),
        "free_form_allowed": bool(row["free_form_allowed"]),
        "recommendation_option_id": row["recommendation_option_id"],
        "recommendation_reason": row["recommendation_reason"],
        "changed_outcome": row["changed_outcome"],
        "positions": json.loads(str(row["positions_json"])),
        "materiality": json.loads(str(row["materiality_json"])),
        "consequence_if_unresolved": row["consequence_if_unresolved"],
        "affected_node_keys": json.loads(str(row["affected_node_keys_json"])),
        "provenance": json.loads(str(row["provenance_json"])),
        "source_occurrences": json.loads(str(row["source_occurrences_json"])),
        "pending_interpretation": (
            None
            if row["pending_interpretation_json"] is None
            else json.loads(str(row["pending_interpretation_json"]))
        ),
        "resolution": (
            None if row["resolution_json"] is None else json.loads(str(row["resolution_json"]))
        ),
    }


def insert_decision(db: sqlite3.Connection, row: dict[str, object]) -> None:
    columns = tuple(row)
    db.execute(
        f"INSERT INTO decision_requests({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def queue_snapshot(
    *,
    task_id: str,
    revision: int,
    checksum: str,
    active: dict[str, object] | None,
    queued: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "branch_id": "main",
        "revision": revision,
        "checksum": checksum,
        "active": active,
        "queued": queued,
    }


def insert_enqueued_decision(
    db: sqlite3.Connection,
    row: dict[str, object],
    *,
    queue_checksum: str = SHA_A,
    resulting_queue: dict[str, object] | None = None,
) -> None:
    snapshot = decision_snapshot(row)
    queue = resulting_queue or queue_snapshot(
        task_id=str(row["task_id"]),
        revision=1,
        checksum=queue_checksum,
        active=snapshot if row["state"] == "active" else None,
        queued=[] if row["state"] == "active" else [snapshot],
    )
    insert_event(
        db,
        task_id=str(row["task_id"]),
        event_id=str(row["created_by_event_id"]),
        sequence=1,
        event_type="decision.enqueued",
        payload={
            "candidate": {"source": json.loads(str(row["source_occurrences_json"]))[0]},
            "assessment": {"disposition": "ask", "semantic_identity": row["semantic_identity"]},
            "decision": snapshot,
            "expected_queue_revision": 0,
            "expected_queue_checksum": "0" * 64,
            "expected_task_cache": None,
            "resulting_queue": queue,
            "resulting_task_state": "intake",
        },
    )
    insert_decision(db, row)


def service_run_row(
    *,
    task_id: str = "task-1",
    run_id: str = "run-1",
    event_id: str = "run-register-1",
    native: bool = False,
) -> dict[str, object]:
    envelope = {
        "adapter_provider": "provider" if native else None,
        "adapter_contract_revision": "v1" if native else None,
        "account_id": "account" if native else None,
        "profile_id": "profile" if native else None,
        "model_id": "model" if native else None,
        "capability_inventory_revision": "cap-v1" if native else None,
        "transport_generation": "transport-v1" if native else None,
        "native_session_id": "session" if native else None,
        "native_thread_id": "thread" if native else None,
        "native_turn_id": None,
        "native_request_id": None,
        "native_tool_use_id": None,
        "native_question_group_id": None,
        "launch_origin": "governed" if native else None,
        "native_handle_json": None,
    }
    return {
        "run_id": run_id,
        "task_id": task_id,
        "service": "claude",
        "role": "researcher",
        "frame_version": 0,
        **envelope,
        "branch_id": "main",
        "state": "queued",
        "receipt_event_id": None,
        "input_manifest_json": canonical({"checksum": SHA_A, "node_ids": []}),
        "started_at": None,
        "updated_at": NOW,
        "completed_at": None,
        "revision": 1,
        "last_event_id": event_id,
        "output_validity": "current",
    }


def native_envelope(row: dict[str, object]) -> dict[str, object] | None:
    if row["adapter_provider"] is None:
        return None
    return {
        "adapter_provider": row["adapter_provider"],
        "adapter_contract_revision": row["adapter_contract_revision"],
        "account_id": row["account_id"],
        "profile_id": row["profile_id"],
        "model_id": row["model_id"],
        "capability_inventory_revision": row["capability_inventory_revision"],
        "transport_generation": row["transport_generation"],
        "native_session_id": row["native_session_id"],
        "native_thread_id": row["native_thread_id"],
        "native_turn_id": row["native_turn_id"],
        "native_request_id": row["native_request_id"],
        "native_tool_use_id": row["native_tool_use_id"],
        "native_question_group_id": row["native_question_group_id"],
        "launch_origin": row["launch_origin"],
        "native_handle": None,
    }


def registration_payload(row: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": row["run_id"],
        "service": row["service"],
        "role": row["role"],
        "task_id": row["task_id"],
        "branch_id": row["branch_id"],
        "frame_version": row["frame_version"],
        "input_manifest": json.loads(str(row["input_manifest_json"])),
        "input_node_ids": [],
        "initial_state": "queued",
        "output_validity": "current",
        "native_identity_envelope": native_envelope(row),
        "expected_task_cache": None,
        "resulting_task_state": "intake",
    }


def insert_registered_run(
    db: sqlite3.Connection,
    row: dict[str, object],
    *,
    payload: dict[str, object] | None = None,
) -> None:
    insert_event(
        db,
        task_id=str(row["task_id"]),
        event_id=str(row["last_event_id"]),
        sequence=1,
        event_type="service_run.registered",
        payload=payload or registration_payload(row),
    )
    columns = tuple(row)
    db.execute(
        f"INSERT INTO service_runs({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def proposal_row(
    *, task_id: str, proposal_id: str, event_id: str, created_at: str = NOW
) -> dict[str, object]:
    return {
        "id": proposal_id,
        "task_id": task_id,
        "branch_id": "main",
        "base_version": 0,
        "proposed_version": 1,
        "expected_head_sequence": 0,
        "base_head_checksum": SHA_A,
        "base_state_checksum": SHA_A,
        "preview_state_checksum": SHA_B,
        "impact_preview_checksum": "c" * 64,
        "status": "pending",
        "changes_json": canonical({"edge_operations": [], "node_operations": []}),
        "impact_preview_json": canonical({"evidence_impacts": [], "run_impacts": []}),
        "provenance_json": canonical({"source_event_ids": []}),
        "created_by": "system:test",
        "created_by_event_id": event_id,
        "decided_by_event_id": None,
        "created_at": created_at,
        "decided_at": None,
    }


def proposal_snapshot(row: dict[str, object]) -> dict[str, object]:
    return {
        "proposal_id": row["id"],
        "base": {
            "task_id": row["task_id"],
            "branch_id": row["branch_id"],
            "frame_version": row["base_version"],
            "head_sequence": row["expected_head_sequence"],
            "head_event_checksum": row["base_head_checksum"],
            "frame_state_checksum": row["base_state_checksum"],
        },
        "proposed_version": row["proposed_version"],
        "changes": json.loads(str(row["changes_json"])),
        "preview": {"state_checksum": row["preview_state_checksum"]},
        "preview_state_checksum": row["preview_state_checksum"],
        "impact": json.loads(str(row["impact_preview_json"])),
        "impact_preview_checksum": row["impact_preview_checksum"],
        "status": row["status"],
        "provenance": json.loads(str(row["provenance_json"])),
    }


def insert_proposal(db: sqlite3.Connection, row: dict[str, object]) -> None:
    columns = tuple(row)
    db.execute(
        f"INSERT INTO frame_proposals({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


@pytest.mark.asyncio
async def test_cancellation_during_precondition_materialization_cleans_up_and_reraises_original(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await install_v3_with_preexisting_authority(settings, tmp_path)
    cancellation = asyncio.CancelledError("cancelled inside failure materialization")

    class CancelMaterialization(MigrationRunner):
        async def _materialize_precondition_failure(
            self,
            db: aiosqlite.Connection,
            marker: _ParsedPreconditionMarker,
        ):  # type: ignore[no-untyped-def]
            assert db.in_transaction
            raise cancellation

    async with aiosqlite.connect(settings.state_path) as db:
        with pytest.raises(asyncio.CancelledError) as raised:
            await CancelMaterialization(settings).apply(db)
        assert raised.value is cancellation
        assert not db.in_transaction
        await db.execute("BEGIN IMMEDIATE")
        await db.rollback()

    backups = sorted((settings.home / "backups").glob("state-pre-migration-v0004-*.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("SELECT COUNT(*) FROM frame_nodes").fetchone() == (1,)
    with sqlite3.connect(settings.state_path) as db:
        detail_row = db.execute(
            "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'"
        ).fetchone()
    assert detail_row is not None
    detail = json.loads(str(detail_row[0]))
    assert detail["backup_retained"] is True
    assert detail["backup_published"] is True
    assert detail["error_type"] == "CancelledError"


@pytest.mark.asyncio
async def test_task_cancellation_while_counting_precondition_rows_releases_transaction_and_lock(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await install_v3_with_preexisting_authority(settings, tmp_path)
    counting = asyncio.Event()
    never = asyncio.Event()

    class GateMaterialization(MigrationRunner):
        async def _materialize_precondition_failure(
            self,
            db: aiosqlite.Connection,
            marker: _ParsedPreconditionMarker,
        ):  # type: ignore[no-untyped-def]
            assert db.in_transaction
            counting.set()
            await never.wait()

    async with aiosqlite.connect(settings.state_path) as db:
        task = asyncio.create_task(GateMaterialization(settings).apply(db))
        await asyncio.wait_for(counting.wait(), timeout=10)
        task.cancel("owner canceled during precondition count")
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("owner canceled during precondition count",)
        assert not db.in_transaction
        await db.execute("BEGIN IMMEDIATE")
        await db.rollback()

    with sqlite3.connect(settings.state_path) as contender:
        contender.execute("BEGIN IMMEDIATE")
        contender.rollback()
    with sqlite3.connect(settings.state_path) as db:
        repair = db.execute(
            "SELECT failure_detail FROM repair_queue WHERE failure_source='state_migration'"
        ).fetchone()
    assert repair is not None
    assert json.loads(str(repair[0]))["backup_retained"] is True


@pytest.mark.asyncio
async def test_fresh_store_records_exact_task_and_branch_creation_transition_ids(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    event_ids = iter(("event-task-created", "event-branch-forked"))
    store = WorkbenchStore(settings, id_factory=lambda: next(event_ids))
    actor = EventActor(kind="owner", actor_id="matt")

    created = await store.create_task("task-1", "Build it", "command-create", actor)
    forked = await store.fork_branch(
        "task-1", "command-fork", "main", "child", created.sequence, actor
    )

    with sqlite3.connect(settings.state_path) as db:
        task_transition = db.execute(
            "SELECT last_transition_event_id FROM workbench_tasks WHERE id='task-1'"
        ).fetchone()
        branch_transitions = db.execute(
            "SELECT branch_id,last_transition_event_id FROM workbench_branches "
            "WHERE task_id='task-1' ORDER BY branch_id"
        ).fetchall()
    assert created.event_id == "event-task-created"
    assert forked.event_id == "event-branch-forked"
    assert task_transition == (created.event_id,)
    assert branch_transitions == [
        ("child", forked.event_id),
        ("main", created.event_id),
    ]


@pytest.mark.asyncio
async def test_task_title_is_immutable_after_creation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    store = WorkbenchStore(settings, id_factory=lambda: "event-task-created")
    await store.create_task(
        "task-1", "Build it", "command-create", EventActor(kind="owner", actor_id="matt")
    )

    with sqlite3.connect(settings.state_path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="title.*immutable"):
            db.execute("UPDATE workbench_tasks SET title='Changed' WHERE id='task-1'")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ("insert", "transition"))
@pytest.mark.parametrize(
    "defect", (
        "wrong_branch", "equal_sequence", "earlier_sequence", "frame", "timestamp", "extra_key"
    )
)
async def test_post_v4_child_creation_requires_exact_parent_event_boundary(
    tmp_path: Path, route: str, defect: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    fork_sequence = 3 if defect == "earlier_sequence" else (2 if defect == "equal_sequence" else 1)
    event_sequence = 2
    event_frame = 1 if defect == "frame" else 0
    branch_time = "2026-07-14T00:01:00.000000Z"
    event_time = "2026-07-14T00:02:00.000000Z" if defect == "timestamp" else branch_time
    event_branch = "other" if defect == "wrong_branch" else "main"
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        insert_event(
            db, task_id="task-1", branch_id="main", event_id="task-event",
            sequence=1, event_type="task.created",
            payload={"title": "task-1", "initial_branch_id": "main"},
        )
        if defect == "wrong_branch":
            db.execute(
                "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
                "VALUES ('task-1','other','active',?)",
                (NOW,),
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
            db, task_id="task-1", branch_id=event_branch, event_id="fork-event",
            sequence=event_sequence, event_type="branch.forked",
            payload=fork_payload, frame_version=event_frame, created_at=event_time,
        )
        last_transition = "fork-event" if route == "insert" else None
        if route == "insert":
            with pytest.raises(sqlite3.IntegrityError, match="creation transition"):
                db.execute(
                    "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,"
                    "forked_from_sequence,forked_from_frame_version,created_by_event_id,status,"
                    "created_at,last_transition_event_id) "
                    "VALUES ('task-1','child','main',?,0,'fork-event','active',?,?)",
                    (fork_sequence, branch_time, last_transition),
                )
        else:
            db.execute(
                "INSERT INTO workbench_branches(task_id,branch_id,parent_branch_id,"
                "forked_from_sequence,forked_from_frame_version,created_by_event_id,status,created_at) "
                "VALUES ('task-1','child','main',?,0,'fork-event','active',?)",
                (fork_sequence, branch_time),
            )
            with pytest.raises(sqlite3.IntegrityError, match="one-time and exact"):
                db.execute(
                    "UPDATE workbench_branches SET last_transition_event_id='fork-event' "
                    "WHERE task_id='task-1' AND branch_id='child'"
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("authority_path", ("task_transition", "root_insert", "root_transition"))
@pytest.mark.parametrize("defect", ("extra_key", "non_genesis_sequence", "frame"))
async def test_post_v4_task_and_root_creation_require_exact_genesis_payload(
    tmp_path: Path, authority_path: str, defect: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "INSERT INTO workbench_tasks(id,title,state,active_branch_id,current_frame_version,"
            "created_at,updated_at) VALUES ('task-1','Build it','intake','main',0,?,?)",
            (NOW, NOW),
        )
        if authority_path == "task_transition":
            db.execute(
                "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
                "VALUES ('task-1','main','active',?)",
                (NOW,),
            )
        sequence = 2 if defect == "non_genesis_sequence" else 1
        if sequence == 2:
            insert_event(
                db, task_id="task-1", branch_id="main", event_id="seed-event",
                sequence=1, event_type="question.ignored", payload={},
            )
        payload: dict[str, object] = {"title": "Build it", "initial_branch_id": "main"}
        if defect == "extra_key":
            payload["forged"] = True
        insert_event(
            db, task_id="task-1", branch_id="main", event_id="task-event",
            sequence=sequence, event_type="task.created", payload=payload,
            frame_version=1 if defect == "frame" else 0,
        )
        if authority_path == "task_transition":
            with pytest.raises(sqlite3.IntegrityError, match="task cache update"):
                db.execute(
                    "UPDATE workbench_tasks SET last_transition_event_id='task-event' WHERE id='task-1'"
                )
        elif authority_path == "root_insert":
            with pytest.raises(sqlite3.IntegrityError, match="creation transition"):
                db.execute(
                    "INSERT INTO workbench_branches(task_id,branch_id,status,created_at,"
                    "last_transition_event_id) VALUES ('task-1','main','active',?,'task-event')",
                    (NOW,),
                )
        else:
            db.execute(
                "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
                "VALUES ('task-1','main','active',?)",
                (NOW,),
            )
            with pytest.raises(sqlite3.IntegrityError, match="one-time and exact"):
                db.execute(
                    "UPDATE workbench_branches SET last_transition_event_id='task-event' "
                    "WHERE task_id='task-1' AND branch_id='main'"
                )


@pytest.mark.asyncio
async def test_post_v4_task_creation_transition_requires_event_on_active_branch(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        db.execute(
            "INSERT INTO workbench_branches(task_id,branch_id,status,created_at) "
            "VALUES ('task-1','other','active',?)",
            (NOW,),
        )
        insert_event(
            db, task_id="task-1", branch_id="other", event_id="task-event",
            sequence=1, event_type="task.created",
            payload={"initial_branch_id": "main", "title": "task-1"},
        )
        with pytest.raises(sqlite3.IntegrityError, match="task cache update"):
            db.execute(
                "UPDATE workbench_tasks SET last_transition_event_id='task-event' WHERE id='task-1'"
            )


@pytest.mark.asyncio
async def test_queue_head_insert_rejects_queued_row_named_as_active(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        queued = decision_row(
            task_id="task-1",
            decision_id="decision-1",
            event_id="enqueue-1",
            state="queued",
            queue_order=1,
        )
        forged_queue = queue_snapshot(
            task_id="task-1",
            revision=1,
            checksum=SHA_A,
            active=decision_snapshot(queued),
            queued=[],
        )
        insert_enqueued_decision(db, queued, resulting_queue=forged_queue)
        with pytest.raises(sqlite3.IntegrityError, match="active-first"):
            db.execute(
                "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
                "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
                "VALUES ('task-1','main',1,'decision-1','[\"decision-1\"]',?,'enqueue-1',?)",
                (SHA_A, NOW),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["decision.source_merged", "decision.queue_reordered"])
async def test_nonresolving_decision_events_cannot_resolve_or_forge_authority(
    tmp_path: Path, event_type: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    forged_occurrence = {
        "classification": "accepted",
        "native_reply_checksum": SHA_B,
        "occurrence_id": "forged-reply",
        "occurrence_kind": "reply",
        "reply_kind": "free_form",
    }
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = decision_row(task_id="task-1", decision_id="decision-1", event_id="enqueue-1")
        insert_enqueued_decision(db, row)
        resulting = dict(row)
        resulting.update(
            revision=2,
            last_event_id="attack-2",
            state="resolved",
            source_occurrences_json=canonical(
                json.loads(str(row["source_occurrences_json"])) + [forged_occurrence]
            ),
            resolution_json=canonical({"option_id": "forged"}),
            decided_at="2026-07-14T00:01:00.000000Z",
        )
        payload: dict[str, object] = {
            "decision_id": "decision-1",
            "expected_decision_revision": 1,
            "resulting_decision": decision_snapshot(resulting),
            "resulting_queue": queue_snapshot(
                task_id="task-1", revision=2, checksum=SHA_B, active=None, queued=[]
            ),
        }
        if event_type == "decision.queue_reordered":
            payload = {
                "expected_queue_revision": 1,
                "expected_queue_checksum": SHA_A,
                "queued_decision_ids": [],
                "resulting_decision": decision_snapshot(resulting),
                "resulting_queue": payload["resulting_queue"],
            }
        insert_event(
            db,
            task_id="task-1",
            event_id="attack-2",
            sequence=2,
            event_type=event_type,
            payload=payload,
            created_at="2026-07-14T00:01:00.000000Z",
        )
        assignments = ",".join(
            f"{column}=?"
            for column in (
                "revision",
                "last_event_id",
                "state",
                "source_occurrences_json",
                "resolution_json",
                "decided_at",
            )
        )
        with pytest.raises(sqlite3.IntegrityError, match="decision .*event"):
            db.execute(
                f"UPDATE decision_requests SET {assignments} WHERE decision_id='decision-1'",
                tuple(
                    resulting[column]
                    for column in (
                        "revision",
                        "last_event_id",
                        "state",
                        "source_occurrences_json",
                        "resolution_json",
                        "decided_at",
                    )
                ),
            )


@pytest.mark.asyncio
async def test_valid_terminal_late_reply_appends_without_reopening_or_touching_queue(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    original = {
        "classification": "accepted",
        "native_question_identity_checksum": SHA_A,
        "occurrence_id": "question-1",
        "occurrence_kind": "question",
        "received_at": "2026-07-14T00:00:00.000000Z",
    }
    late = {
        "classification": "late_new",
        "native_reply_checksum": SHA_B,
        "occurrence_id": "late-reply",
        "occurrence_kind": "reply",
        "original_occurrence_event_id": None,
        "received_at": "2026-07-14T00:02:00.000000Z",
        "reply": {"text": "A late explanation"},
        "reply_kind": "free_form",
    }
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        insert_event(
            db,
            task_id="task-1",
            event_id="enqueue-1",
            sequence=1,
            event_type="decision.enqueued",
            payload={"decision": {"decision_id": "decision-1"}},
        )
        terminal = decision_row(
            task_id="task-1",
            decision_id="decision-1",
            event_id="resolved-2",
            state="resolved",
            revision=2,
            source_occurrences=[original],
        )
        terminal.update(
            created_by_event_id="enqueue-1",
            resolution_json=canonical({"option_id": "option-1"}),
            decided_at="2026-07-14T00:01:00.000000Z",
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="resolved-2",
            sequence=2,
            event_type="decision.resolved",
            payload={"decision_id": "decision-1", "resulting_decision": decision_snapshot(terminal)},
            created_at="2026-07-14T00:01:00.000000Z",
        )
        db.execute("DROP TRIGGER decision_requests_task3_validate_insert")
        insert_decision(db, terminal)
        resulting = dict(terminal)
        resulting.update(
            revision=3,
            last_event_id="late-3",
            source_occurrences_json=canonical([original, late]),
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="late-3",
            sequence=3,
            event_type="decision.late_reply_recorded",
            payload={
                "decision_id": "decision-1",
                "occurrence": late,
                "terminal_authority_event_id": "resolved-2",
                "expected_decision_revision": 2,
                "resulting_decision": decision_snapshot(resulting),
            },
            created_at="2026-07-14T00:02:00.000000Z",
        )
        db.execute(
            "UPDATE decision_requests SET revision=3,last_event_id='late-3',source_occurrences_json=? "
            "WHERE decision_id='decision-1'",
            (resulting["source_occurrences_json"],),
        )
        assert db.execute(
            "SELECT state,resolution_json,decided_at,revision,last_event_id FROM decision_requests"
        ).fetchone() == (
            "resolved",
            terminal["resolution_json"],
            terminal["decided_at"],
            3,
            "late-3",
        )


@pytest.mark.asyncio
async def test_accepted_native_question_identity_is_globally_unique(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for task_id in ("task-1", "task-2"):
            insert_task_root(db, task_id)
            row = decision_row(
                task_id=task_id,
                decision_id=f"decision-{task_id}",
                event_id=f"enqueue-{task_id}",
            )
            if task_id == "task-1":
                insert_enqueued_decision(db, row)
            else:
                with pytest.raises(sqlite3.IntegrityError, match="native question identity"):
                    insert_enqueued_decision(db, row)


@pytest.mark.asyncio
async def test_service_run_registration_rejects_partial_payload_substitute(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = service_run_row()
        with pytest.raises(sqlite3.IntegrityError, match="registration.*exact event"):
            insert_registered_run(
                db,
                row,
                payload={"run_id": "run-1", "initial_state": "queued", "output_validity": "current"},
            )


@pytest.mark.asyncio
async def test_service_run_rejects_illegal_reason_and_null_terminal_receipt_and_times(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = service_run_row()
        insert_registered_run(db, row)
        insert_event(
            db,
            task_id="task-1",
            event_id="start-2",
            sequence=2,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": "queued",
                "expected_output_validity": "current",
                "new_state": "starting",
                "resulting_output_validity": "current",
                "reason_code": "dispatch_started",
                "native_identity_attachment": None,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:01:00.000000Z",
        )
        db.execute(
            "UPDATE service_runs SET revision=2,last_event_id='start-2',"
            "updated_at='2026-07-14T00:01:00.000000Z',started_at='2026-07-14T00:01:00.000000Z',"
            "state='starting' WHERE run_id='run-1'"
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="running-3",
            sequence=3,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 2,
                "expected_state": "starting",
                "expected_output_validity": "current",
                "new_state": "running",
                "resulting_output_validity": "current",
                "reason_code": "provider_attached",
                "native_identity_attachment": None,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:02:00.000000Z",
        )
        db.execute(
            "UPDATE service_runs SET revision=3,last_event_id='running-3',"
            "updated_at='2026-07-14T00:02:00.000000Z',state='running' WHERE run_id='run-1'"
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="illegal-complete-4",
            sequence=4,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 3,
                "expected_state": "running",
                "expected_output_validity": "current",
                "new_state": "complete",
                "resulting_output_validity": "current",
                "reason_code": "made_up_reason",
                "native_identity_attachment": None,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:03:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="reason-bound|receipt|terminal time"):
            db.execute(
                "UPDATE service_runs SET revision=4,last_event_id='illegal-complete-4',"
                "updated_at='2026-07-14T00:03:00.000000Z',state='complete' WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
async def test_service_run_native_envelope_cannot_be_rewritten_after_registration(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = service_run_row(native=True)
        insert_registered_run(db, row)
        insert_event(
            db,
            task_id="task-1",
            event_id="start-2",
            sequence=2,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": "queued",
                "expected_output_validity": "current",
                "new_state": "starting",
                "resulting_output_validity": "current",
                "reason_code": "dispatch_started",
                "native_identity_attachment": None,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:01:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="native.*immutable"):
            db.execute(
                "UPDATE service_runs SET revision=2,last_event_id='start-2',"
                "updated_at='2026-07-14T00:01:00.000000Z',started_at='2026-07-14T00:01:00.000000Z',"
                "state='starting',adapter_provider='rewritten-provider' WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
async def test_service_run_valid_receipt_backed_completion_path(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = service_run_row()
        insert_registered_run(db, row)
        transitions = (
            (2, "start-2", "queued", "starting", "dispatch_started"),
            (3, "running-3", "starting", "running", "provider_attached"),
        )
        for revision, event_id, old_state, new_state, reason in transitions:
            event_time = f"2026-07-14T00:0{revision - 1}:00.000000Z"
            insert_event(
                db,
                task_id="task-1",
                event_id=event_id,
                sequence=revision,
                event_type="service_run.state_changed",
                payload={
                    "run_id": "run-1",
                    "expected_revision": revision - 1,
                    "expected_state": old_state,
                    "expected_output_validity": "current",
                    "new_state": new_state,
                    "resulting_output_validity": "current",
                    "reason_code": reason,
                    "native_identity_attachment": None,
                    "expected_task_cache": None,
                    "resulting_task_state": "intake",
                },
                created_at=event_time,
            )
            start_assignment = ",started_at=?" if revision == 2 else ""
            params: tuple[object, ...] = (
                revision,
                event_id,
                event_time,
                new_state,
                *((event_time,) if revision == 2 else ()),
            )
            db.execute(
                "UPDATE service_runs SET revision=?,last_event_id=?,updated_at=?,state=?"
                + start_assignment
                + " WHERE run_id='run-1'",
                params,
            )
        receipt_time = "2026-07-14T00:03:00.000000Z"
        insert_event(
            db,
            task_id="task-1",
            event_id="receipt-4",
            sequence=4,
            event_type="service_run.receipt_recorded",
            payload={
                "run_id": "run-1",
                "expected_revision": 3,
                "expected_state": "running",
                "receipt": {
                    "run_id": "run-1",
                    "run_revision": 3,
                    "frame_version": 0,
                    "input_manifest_checksum": SHA_A,
                },
                "resulting_revision": 4,
            },
            created_at=receipt_time,
        )
        db.execute(
            "UPDATE service_runs SET revision=4,last_event_id='receipt-4',receipt_event_id='receipt-4',"
            "updated_at=? WHERE run_id='run-1'",
            (receipt_time,),
        )
        completion_time = "2026-07-14T00:04:00.000000Z"
        insert_event(
            db,
            task_id="task-1",
            event_id="complete-5",
            sequence=5,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 4,
                "expected_state": "running",
                "expected_output_validity": "current",
                "new_state": "complete",
                "resulting_output_validity": "current",
                "reason_code": "service_completed",
                "native_identity_attachment": None,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at=completion_time,
        )
        db.execute(
            "UPDATE service_runs SET revision=5,last_event_id='complete-5',updated_at=?,"
            "state='complete',completed_at=? WHERE run_id='run-1'",
            (completion_time, completion_time),
        )
        assert db.execute(
            "SELECT state,revision,receipt_event_id,started_at,completed_at FROM service_runs"
        ).fetchone() == (
            "complete",
            5,
            "receipt-4",
            "2026-07-14T00:01:00.000000Z",
            completion_time,
        )


@pytest.mark.asyncio
async def test_service_run_receipt_must_bind_exact_current_run_revision_frame_and_manifest(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = service_run_row()
        insert_event(
            db,
            task_id="task-1",
            event_id="run-register-1",
            sequence=1,
            event_type="service_run.registered",
            payload=registration_payload(row),
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="running-2",
            sequence=2,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": "starting",
                "expected_output_validity": "current",
                "new_state": "running",
                "resulting_output_validity": "current",
                "reason_code": "provider_attached",
                "native_identity_attachment": None,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:01:00.000000Z",
        )
        row.update(
            state="running",
            revision=2,
            last_event_id="running-2",
            started_at="2026-07-14T00:01:00.000000Z",
            updated_at="2026-07-14T00:01:00.000000Z",
        )
        db.execute("DROP TRIGGER service_runs_task3_validate_insert")
        columns = tuple(row)
        db.execute(
            f"INSERT INTO service_runs({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="wrong-receipt-3",
            sequence=3,
            event_type="frame.change_confirmed",
            payload={
                "proposal_id": "proposal-1",
                "impact": {"run_impacts": []},
            },
            created_at="2026-07-14T00:02:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="transition.*exact|receipt.*exact"):
            db.execute(
                "UPDATE service_runs SET revision=3,last_event_id='wrong-receipt-3',"
                "receipt_event_id='wrong-receipt-3',updated_at='2026-07-14T00:02:00.000000Z',"
                "state='complete',completed_at='2026-07-14T00:02:00.000000Z' "
                "WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
async def test_proposal_creation_requires_full_frozen_event_payload(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = proposal_row(task_id="task-1", proposal_id="proposal-1", event_id="proposal-event-1")
        insert_event(
            db,
            task_id="task-1",
            event_id="proposal-event-1",
            sequence=1,
            event_type="frame.proposal_created",
            payload={"proposal": {"proposal_id": "proposal-1", "status": "pending"}},
        )
        with pytest.raises(sqlite3.IntegrityError, match="proposal.*exact creation event"):
            insert_proposal(db, row)


@pytest.mark.asyncio
async def test_confirmation_accepts_primary_and_exact_peer_supersession_only(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    decided_at = "2026-07-14T00:02:00.000000Z"
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        primary = proposal_row(task_id="task-1", proposal_id="primary", event_id="proposal-1")
        peer = proposal_row(
            task_id="task-1",
            proposal_id="peer",
            event_id="proposal-2",
            created_at="2026-07-14T00:01:00.000000Z",
        )
        for sequence, row in enumerate((primary, peer), start=1):
            insert_event(
                db,
                task_id="task-1",
                event_id=str(row["created_by_event_id"]),
                sequence=sequence,
                event_type="frame.proposal_created",
                payload={"proposal": proposal_snapshot(row)},
                created_at=str(row["created_at"]),
            )
            insert_proposal(db, row)
        insert_event(
            db,
            task_id="task-1",
            event_id="confirm-3",
            sequence=3,
            event_type="frame.change_confirmed",
            payload={
                "proposal_id": "primary",
                "expected_preview_state_checksum": SHA_B,
                "expected_impact_preview_checksum": "c" * 64,
                "proposal_supersessions": [
                    {
                        "proposal_id": "peer",
                        "expected_created_by_event_id": "proposal-2",
                        "expected_preview_state_checksum": SHA_B,
                    }
                ],
                "confirmed": {"frame_version": 1},
                "impact": {"evidence_impacts": [], "run_impacts": []},
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at=decided_at,
        )
        db.execute(
            "UPDATE frame_proposals SET status='accepted',decided_by_event_id='confirm-3',decided_at=? "
            "WHERE id='primary'",
            (decided_at,),
        )
        db.execute(
            "UPDATE frame_proposals SET status='superseded',decided_by_event_id='confirm-3',decided_at=? "
            "WHERE id='peer'",
            (decided_at,),
        )
        assert db.execute(
            "SELECT id,status FROM frame_proposals ORDER BY id"
        ).fetchall() == [("peer", "superseded"), ("primary", "accepted")]


@pytest.mark.asyncio
async def test_confirmation_cannot_misclassify_primary_as_superseded(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = proposal_row(task_id="task-1", proposal_id="primary", event_id="proposal-1")
        insert_event(
            db,
            task_id="task-1",
            event_id="proposal-1",
            sequence=1,
            event_type="frame.proposal_created",
            payload={"proposal": proposal_snapshot(row)},
        )
        insert_proposal(db, row)
        insert_event(
            db,
            task_id="task-1",
            event_id="confirm-2",
            sequence=2,
            event_type="frame.change_confirmed",
            payload={"proposal_id": "primary", "proposal_supersessions": []},
            created_at="2026-07-14T00:01:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="transition.*exact|primary.*accepted"):
            db.execute(
                "UPDATE frame_proposals SET status='superseded',decided_by_event_id='confirm-2',"
                "decided_at='2026-07-14T00:01:00.000000Z' WHERE id='primary'"
            )


def install_confirmed_node_event(
    db: sqlite3.Connection,
    *,
    operation_kind: str = "upsert",
    expected_current_node_id: str | None = None,
    provenance_event_ids: list[str] | None = None,
    value: object = None,
    node_ids: tuple[str, ...] = ("node-1",),
) -> tuple[str, list[dict[str, object]]]:
    provenance_ids = provenance_event_ids or []
    operations: list[dict[str, object]] = []
    confirmed_nodes: list[dict[str, object]] = []
    for index, node_id in enumerate(node_ids, start=1):
        node_key = f"goal-{index}"
        operation = {
            "operation_id": f"operation-{index}",
            "operation_kind": operation_kind,
            "new_node_id": node_id,
            "node_key": node_key,
            "expected_current_node_id": expected_current_node_id,
            "kind": "goal",
            "text": f"Goal {index}",
            "value": value,
            "depends_on_node_keys": [],
            "provenance": {"source_event_ids": []},
        }
        operations.append(operation)
        confirmed_nodes.append(
            {
                "node_id": node_id,
                "node_key": node_key,
                "task_id": "task-1",
                "branch_id": "main",
                "frame_version": 1,
                "kind": "goal",
                "text": f"Goal {index}",
                "value": value,
                "status": "confirmed",
                "supersedes_node_id": None,
                "depends_on": [],
                "provenance_event_ids": provenance_ids,
            }
        )
    proposal = proposal_row(task_id="task-1", proposal_id="proposal-1", event_id="proposal-1")
    proposal["changes_json"] = canonical(
        {"edge_operations": [], "node_operations": operations}
    )
    insert_event(
        db,
        task_id="task-1",
        event_id="proposal-1",
        sequence=1,
        event_type="frame.proposal_created",
        payload={"proposal": proposal_snapshot(proposal)},
    )
    insert_proposal(db, proposal)
    confirm_time = "2026-07-14T00:01:00.000000Z"
    insert_event(
        db,
        task_id="task-1",
        event_id="confirm-2",
        sequence=2,
        event_type="frame.change_confirmed",
        frame_version=1,
        payload={
            "proposal_id": "proposal-1",
            "expected_preview_state_checksum": SHA_B,
            "expected_impact_preview_checksum": "c" * 64,
            "confirmed": {
                "task_id": "task-1",
                "branch_id": "main",
                "frame_version": 1,
                "nodes": confirmed_nodes,
                "edges": [],
            },
            "impact": {"evidence_impacts": [], "run_impacts": []},
            "proposal_supersessions": [],
            "expected_task_cache": None,
            "resulting_task_state": "intake",
        },
        created_at=confirm_time,
    )
    db.execute(
        "UPDATE frame_proposals SET status='accepted',decided_by_event_id='confirm-2',decided_at=? "
        "WHERE id='proposal-1'",
        (confirm_time,),
    )
    return confirm_time, confirmed_nodes


def insert_confirmed_node_row(
    db: sqlite3.Connection, node: dict[str, object], *, value_json: str, provenance_ids: list[str]
) -> None:
    db.execute(
        """
        INSERT INTO frame_nodes(
            node_id,task_id,branch_id,frame_version,node_key,supersedes_node_id,kind,text,
            value_json,status,depends_on_json,provenance_event_ids_json,provenance_json,
            created_by_event_id,invalidated_by_event_id,created_at,updated_at
        ) VALUES (?,?,?,?,?,NULL,?,?,?,'confirmed','[]',?,'{"source_event_ids":[]}',
                  'confirm-2',NULL,'2026-07-14T00:01:00.000000Z','2026-07-14T00:01:00.000000Z')
        """,
        (
            node["node_id"],
            node["task_id"],
            node["branch_id"],
            node["frame_version"],
            node["node_key"],
            node["kind"],
            node["text"],
            value_json,
            canonical(provenance_ids),
        ),
    )


@pytest.mark.asyncio
async def test_frame_node_accepts_valid_json_string_scalar_mapping(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        _, nodes = install_confirmed_node_event(db, value="plain string")
        insert_confirmed_node_row(db, nodes[0], value_json='"plain string"', provenance_ids=[])
        assert db.execute("SELECT value_json FROM frame_nodes").fetchone() == ('"plain string"',)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_kind", "expected_current", "provenance_ids"),
    (
        ("invalidate", None, []),
        ("upsert", "foreign-predecessor", []),
        ("upsert", None, ["forged-provenance-event"]),
    ),
)
async def test_frame_node_rejects_operation_supersession_or_provenance_mismatch(
    tmp_path: Path,
    operation_kind: str,
    expected_current: str | None,
    provenance_ids: list[str],
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        _, nodes = install_confirmed_node_event(
            db,
            operation_kind=operation_kind,
            expected_current_node_id=expected_current,
            provenance_event_ids=provenance_ids,
            value={},
        )
        with pytest.raises(sqlite3.IntegrityError, match="operation|supersession|provenance"):
            insert_confirmed_node_row(
                db, nodes[0], value_json="{}", provenance_ids=provenance_ids
            )


@pytest.mark.asyncio
async def test_frame_edge_insert_must_be_in_exact_confirmed_full_snapshot(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        _, nodes = install_confirmed_node_event(db, value={}, node_ids=("node-1", "node-2"))
        for node in nodes:
            insert_confirmed_node_row(db, node, value_json="{}", provenance_ids=[])
        with pytest.raises(sqlite3.IntegrityError, match="edge.*confirmed"):
            db.execute(
                "INSERT INTO frame_edges(id,task_id,branch_id,frame_version,from_node_id,to_node_id,"
                "edge_type,created_by_event_id,created_at) VALUES "
                "('edge-forged','task-1','main',1,'node-1','node-2','supports','confirm-2',"
                "'2026-07-14T00:01:00.000000Z')"
            )


@pytest.mark.asyncio
async def test_inactive_or_unrelated_event_cannot_rewrite_selected_task_cache(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    event_ids = iter(("task-created-1", "branch-forked-2"))
    store = WorkbenchStore(settings, id_factory=lambda: next(event_ids))
    actor = EventActor(kind="owner", actor_id="matt")
    created = await store.create_task("task-1", "Build it", "command-create", actor)
    await store.fork_branch("task-1", "command-fork", "main", "child", created.sequence, actor)
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_event(
            db,
            task_id="task-1",
            branch_id="child",
            event_id="inactive-decision-3",
            sequence=3,
            event_type="decision.enqueued",
            payload={"resulting_task_state": "waiting_owner"},
            created_at="2026-07-14T00:02:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="selected|task cache"):
            db.execute(
                "UPDATE workbench_tasks SET state='ready',current_frame_version=99,"
                "updated_at='2026-07-14T00:02:00.000000Z',"
                "last_transition_event_id='inactive-decision-3' WHERE id='task-1'"
            )


LEGAL_RUN_TRANSITIONS = (
    ("dispatch_started", "queued", "starting", "current", "current"),
    ("provider_attached", "starting", "running", "current", "current"),
    ("owner_input_required", "running", "waiting_owner", "current", "current"),
    ("owner_input_required", "repairing", "waiting_owner", "current", "current"),
    ("owner_input_received", "waiting_owner", "running", "current", "current"),
    ("dependency_changed", "running", "paused_dependency", "current", "current"),
    ("dependency_changed", "waiting_owner", "paused_dependency", "current", "current"),
    ("dependency_changed", "repairing", "paused_dependency", "current", "current"),
    ("dependency_restored", "paused_dependency", "starting", "current", "current"),
    ("repair_started", "starting", "repairing", "current", "current"),
    ("repair_started", "running", "repairing", "current", "current"),
    ("repair_started", "waiting_owner", "repairing", "current", "current"),
    ("repair_started", "paused_dependency", "repairing", "current", "current"),
    ("repair_started", "interrupted", "repairing", "current", "current"),
    ("retry_started", "repairing", "starting", "current", "current"),
    ("retry_started", "interrupted", "starting", "current", "current"),
    ("recovery_reconciled", "repairing", "running", "current", "current"),
    ("recovery_reconciled", "interrupted", "running", "current", "current"),
    ("verification_started", "running", "verifying", "current", "current"),
    ("verification_started", "repairing", "verifying", "current", "current"),
    ("service_interrupted", "starting", "interrupted", "current", "current"),
    ("service_interrupted", "running", "interrupted", "current", "current"),
    ("service_interrupted", "waiting_owner", "interrupted", "current", "current"),
    ("service_interrupted", "repairing", "interrupted", "current", "current"),
    ("service_completed", "running", "complete", "current", "current"),
    ("service_completed", "verifying", "complete", "current", "current"),
    ("service_failed", "starting", "failed", "current", "current"),
    ("service_failed", "running", "failed", "current", "current"),
    ("service_failed", "repairing", "failed", "current", "current"),
    ("service_failed", "verifying", "failed", "current", "current"),
    ("service_failed", "interrupted", "failed", "current", "current"),
    *(("owner_canceled", state, "canceled", "current", "current") for state in (
        "queued", "starting", "running", "waiting_owner", "paused_dependency", "repairing",
        "verifying", "interrupted",
    )),
    ("output_staled", "complete", "complete", "current", "stale"),
    ("reverification_required", "complete", "complete", "current", "reverification_required"),
    ("reverification_required", "complete", "complete", "stale", "reverification_required"),
)

ILLEGAL_RUN_TRANSITIONS = (
    ("dispatch_started", "running", "starting"),
    ("provider_attached", "queued", "running"),
    ("owner_input_required", "starting", "waiting_owner"),
    ("owner_input_received", "running", "running"),
    ("dependency_changed", "starting", "paused_dependency"),
    ("dependency_restored", "running", "starting"),
    ("repair_started", "queued", "repairing"),
    ("retry_started", "running", "starting"),
    ("recovery_reconciled", "starting", "running"),
    ("verification_started", "starting", "verifying"),
    ("service_interrupted", "paused_dependency", "interrupted"),
    ("service_completed", "starting", "complete"),
    ("service_failed", "queued", "failed"),
    ("owner_canceled", "complete", "canceled"),
    ("output_staled", "running", "running"),
    ("reverification_required", "running", "running"),
    ("unknown_reason", "running", "failed"),
)


def insert_direct_run_base(
    db: sqlite3.Connection,
    *,
    old_state: str,
    output_validity: str = "current",
    receipt_bound: bool = False,
    manifest_complete: bool = True,
) -> dict[str, object]:
    row = service_run_row()
    row["state"] = old_state
    row["revision"] = 2 if receipt_bound else 1
    row["started_at"] = None if old_state == "queued" else "2026-07-13T23:59:00.000000Z"
    row["completed_at"] = (
        "2026-07-13T23:59:30.000000Z" if old_state in ("complete", "failed", "canceled") else None
    )
    row["output_validity"] = output_validity
    if not manifest_complete:
        row["input_manifest_json"] = canonical({"checksum": SHA_A})
    prior_event = "receipt-prior" if receipt_bound else "run-register-1"
    row["last_event_id"] = prior_event
    row["receipt_event_id"] = prior_event if receipt_bound else None
    if receipt_bound:
        insert_event(
            db,
            task_id="task-1",
            event_id=prior_event,
            sequence=1,
            event_type="service_run.receipt_recorded",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": old_state,
                "receipt": {
                    "run_id": "run-1",
                    "run_revision": 1,
                    "frame_version": 0,
                    "input_manifest_checksum": SHA_A,
                },
                "resulting_revision": 2,
            },
        )
    else:
        insert_event(
            db,
            task_id="task-1",
            event_id=prior_event,
            sequence=1,
            event_type="service_run.registered",
            payload=registration_payload(row),
        )
    db.execute("DROP TRIGGER service_runs_task3_validate_insert")
    columns = tuple(row)
    db.execute(
        f"INSERT INTO service_runs({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )
    return row


def apply_run_state_event(
    db: sqlite3.Connection,
    row: dict[str, object],
    *,
    reason: str,
    new_state: str,
    new_output_validity: str,
    native_attachment: dict[str, object] | None = None,
) -> None:
    event_time = "2026-07-14T00:01:00.000000Z"
    new_revision = int(row["revision"]) + 1
    insert_event(
        db,
        task_id="task-1",
        event_id="transition-2",
        sequence=2,
        event_type="service_run.state_changed",
        payload={
            "run_id": "run-1",
            "expected_revision": row["revision"],
            "expected_state": row["state"],
            "expected_output_validity": row["output_validity"],
            "new_state": new_state,
            "resulting_output_validity": new_output_validity,
            "reason_code": reason,
            "native_identity_attachment": native_attachment,
            "expected_task_cache": None,
            "resulting_task_state": "intake",
        },
        created_at=event_time,
    )
    started_at = row["started_at"]
    if started_at is None and new_state in ("starting", "running"):
        started_at = event_time
    completed_at = row["completed_at"]
    if completed_at is None and new_state in ("complete", "failed", "canceled"):
        completed_at = event_time
    db.execute(
        "UPDATE service_runs SET revision=?,last_event_id='transition-2',updated_at=?,state=?,"
        "output_validity=?,started_at=?,completed_at=? WHERE run_id='run-1'",
        (new_revision, event_time, new_state, new_output_validity, started_at, completed_at),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "old_state", "new_state", "old_output", "new_output"), LEGAL_RUN_TRANSITIONS
)
async def test_every_ordinary_run_reason_edge_is_accepted(
    tmp_path: Path,
    reason: str,
    old_state: str,
    new_state: str,
    old_output: str,
    new_output: str,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(
            db,
            old_state=old_state,
            output_validity=old_output,
            receipt_bound=reason == "service_completed" or old_state == "complete",
        )
        apply_run_state_event(
            db, row, reason=reason, new_state=new_state, new_output_validity=new_output
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("reason", "old_state", "new_state"), ILLEGAL_RUN_TRANSITIONS)
async def test_each_run_reason_rejects_a_forbidden_edge(
    tmp_path: Path, reason: str, old_state: str, new_state: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(
            db, old_state=old_state, receipt_bound=old_state == "complete"
        )
        with pytest.raises(sqlite3.IntegrityError, match="reason-bound"):
            apply_run_state_event(
                db,
                row,
                reason=reason,
                new_state=new_state,
                new_output_validity="current",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(("old_state", "new_state"), (("queued", "starting"), ("starting", "running")))
async def test_native_identity_attaches_all_null_to_complete_once_then_freezes(
    tmp_path: Path, old_state: str, new_state: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    complete = service_run_row(native=True)
    attachment = native_envelope(complete)
    assert attachment is not None
    reason = "dispatch_started" if old_state == "queued" else "provider_attached"
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(db, old_state=old_state)
        event_time = "2026-07-14T00:01:00.000000Z"
        insert_event(
            db,
            task_id="task-1",
            event_id="transition-2",
            sequence=2,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": old_state,
                "expected_output_validity": "current",
                "new_state": new_state,
                "resulting_output_validity": "current",
                "reason_code": reason,
                "native_identity_attachment": attachment,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at=event_time,
        )
        native_columns = tuple(key for key in complete if key in {
            "adapter_provider", "adapter_contract_revision", "account_id", "profile_id", "model_id",
            "capability_inventory_revision", "transport_generation", "native_session_id",
            "native_thread_id", "native_turn_id", "native_request_id", "native_tool_use_id",
            "native_question_group_id", "launch_origin", "native_handle_json",
        })
        started_at = event_time if row["started_at"] is None else row["started_at"]
        db.execute(
            "UPDATE service_runs SET revision=2,last_event_id='transition-2',updated_at=?,state=?,"
            "started_at=?," + ",".join(f"{column}=?" for column in native_columns)
            + " WHERE run_id='run-1'",
            (event_time, new_state, started_at, *(complete[column] for column in native_columns)),
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="rewrite-3",
            sequence=3,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1", "expected_revision": 2, "expected_state": new_state,
                "expected_output_validity": "current", "new_state": "repairing",
                "resulting_output_validity": "current", "reason_code": "repair_started",
                "native_identity_attachment": {**attachment, "adapter_provider": "rewritten"},
                "expected_task_cache": None, "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:02:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="native identity"):
            db.execute(
                "UPDATE service_runs SET revision=3,last_event_id='rewrite-3',"
                "updated_at='2026-07-14T00:02:00.000000Z',state='repairing',"
                "adapter_provider='rewritten' WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "reason", "old_state", "new_state", "old_output", "new_output", "receipt_bound"),
    (
        ("pause_requested", "dependency_changed", "running", "paused_dependency", "current", "current", False),
        ("pause_requested", "dependency_changed", "running", "paused_dependency", "current", "stale", True),
        ("repairing", "missing_input_manifest", "waiting_owner", "repairing", "current", "current", False),
        ("require_reverification", "reverification_required", "complete", "complete", "current", "reverification_required", True),
        ("require_reverification", "reverification_required", "complete", "complete", "stale", "reverification_required", True),
    ),
)
async def test_frame_confirmation_run_correction_matrix(
    tmp_path: Path,
    action: str,
    reason: str,
    old_state: str,
    new_state: str,
    old_output: str,
    new_output: str,
    receipt_bound: bool,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(
            db,
            old_state=old_state,
            output_validity=old_output,
            receipt_bound=receipt_bound,
            manifest_complete=action != "repairing",
        )
        event_time = "2026-07-14T00:01:00.000000Z"
        new_revision = int(row["revision"]) + 1
        impact = {
            "run_id": "run-1",
            "expected_revision": row["revision"],
            "expected_state": old_state,
            "expected_output_validity": old_output,
            "expected_last_event_id": row["last_event_id"],
            "expected_receipt_event_id": row["receipt_event_id"],
            "resulting_state": new_state,
            "resulting_output_validity": new_output,
            "resulting_revision": new_revision,
            "reason_code": reason,
            "action": action,
        }
        insert_event(
            db,
            task_id="task-1",
            event_id="confirm-2",
            sequence=2,
            event_type="frame.change_confirmed",
            payload={"proposal_id": "proposal-1", "impact": {"run_impacts": [impact]}},
            created_at=event_time,
        )
        db.execute(
            "UPDATE service_runs SET revision=?,last_event_id='confirm-2',updated_at=?,state=?,"
            "output_validity=? WHERE run_id='run-1'",
            (new_revision, event_time, new_state, new_output),
        )
        assert db.execute(
            "SELECT receipt_event_id,output_validity FROM service_runs WHERE run_id='run-1'"
        ).fetchone() == (row["receipt_event_id"], new_output)


@pytest.mark.asyncio
async def test_frame_confirmation_rejects_mismatched_run_correction_action(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(db, old_state="running")
        impact = {
            "run_id": "run-1", "expected_revision": 1, "expected_state": "running",
            "expected_output_validity": "current", "expected_last_event_id": row["last_event_id"],
            "expected_receipt_event_id": None, "resulting_state": "repairing",
            "resulting_output_validity": "current", "resulting_revision": 2,
            "reason_code": "missing_input_manifest", "action": "pause_requested",
        }
        insert_event(
            db, task_id="task-1", event_id="confirm-2", sequence=2,
            event_type="frame.change_confirmed",
            payload={"proposal_id": "proposal-1", "impact": {"run_impacts": [impact]}},
            created_at="2026-07-14T00:01:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="correction"):
            db.execute(
                "UPDATE service_runs SET revision=2,last_event_id='confirm-2',"
                "updated_at='2026-07-14T00:01:00.000000Z',state='repairing' WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("receipt_revision", "frame_version", "manifest_checksum", "resulting_revision"),
    ((99, 0, SHA_A, 2), (1, 7, SHA_A, 2), (1, 0, SHA_B, 2), (1, 0, SHA_A, 99)),
)
async def test_receipt_rejects_stale_revision_frame_manifest_or_resulting_revision(
    tmp_path: Path,
    receipt_revision: int,
    frame_version: int,
    manifest_checksum: str,
    resulting_revision: int,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(db, old_state="running")
        insert_event(
            db,
            task_id="task-1",
            event_id="receipt-2",
            sequence=2,
            event_type="service_run.receipt_recorded",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": "running",
                "receipt": {
                    "run_id": "run-1",
                    "run_revision": receipt_revision,
                    "frame_version": frame_version,
                    "input_manifest_checksum": manifest_checksum,
                },
                "resulting_revision": resulting_revision,
            },
            created_at="2026-07-14T00:01:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="transition.*exact"):
            db.execute(
                "UPDATE service_runs SET revision=2,last_event_id='receipt-2',receipt_event_id='receipt-2',"
                "updated_at='2026-07-14T00:01:00.000000Z' WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
async def test_completion_rejects_receipt_that_is_not_immediate_run_predecessor(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(db, old_state="running", receipt_bound=True)
        insert_event(
            db, task_id="task-1", event_id="later-run-event", sequence=2,
            event_type="frame.change_confirmed", payload={"impact": {"run_impacts": []}},
            created_at="2026-07-14T00:01:00.000000Z",
        )
        db.execute("DROP TRIGGER service_runs_task3_transition")
        db.execute(
            "UPDATE service_runs SET revision=3,last_event_id='later-run-event',"
            "updated_at='2026-07-14T00:01:00.000000Z' WHERE run_id='run-1'"
        )
        migration_sql = (MIGRATIONS / "0004_intent_authority_guards.sql").read_text(encoding="utf-8")
        trigger_start = migration_sql.index("CREATE TRIGGER service_runs_task3_transition")
        trigger_end = migration_sql.index("CREATE TRIGGER service_runs_task3_no_delete")
        db.executescript(migration_sql[trigger_start:trigger_end])
        insert_event(
            db,
            task_id="task-1",
            event_id="complete-3",
            sequence=3,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1", "expected_revision": 3, "expected_state": "running",
                "expected_output_validity": "current", "new_state": "complete",
                "resulting_output_validity": "current", "reason_code": "service_completed",
                "native_identity_attachment": None, "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:02:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="immediate receipt"):
            db.execute(
                "UPDATE service_runs SET revision=4,last_event_id='complete-3',state='complete',"
                "updated_at='2026-07-14T00:02:00.000000Z',"
                "completed_at='2026-07-14T00:02:00.000000Z' WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
async def test_native_identity_attachment_is_rejected_outside_two_allowed_edges(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    complete = service_run_row(native=True)
    attachment = native_envelope(complete)
    assert attachment is not None
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(db, old_state="running")
        insert_event(
            db, task_id="task-1", event_id="transition-2", sequence=2,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1", "expected_revision": 1, "expected_state": "running",
                "expected_output_validity": "current", "new_state": "waiting_owner",
                "resulting_output_validity": "current", "reason_code": "owner_input_required",
                "native_identity_attachment": attachment, "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at="2026-07-14T00:01:00.000000Z",
        )
        native_columns = tuple(key for key in complete if key in {
            "adapter_provider", "adapter_contract_revision", "account_id", "profile_id", "model_id",
            "capability_inventory_revision", "transport_generation", "native_session_id",
            "native_thread_id", "native_turn_id", "native_request_id", "native_tool_use_id",
            "native_question_group_id", "launch_origin", "native_handle_json",
        })
        with pytest.raises(sqlite3.IntegrityError, match="native identity"):
            db.execute(
                "UPDATE service_runs SET revision=2,last_event_id='transition-2',"
                "updated_at='2026-07-14T00:01:00.000000Z',state='waiting_owner',"
                + ",".join(f"{column}=?" for column in native_columns)
                + " WHERE run_id='run-1'",
                tuple(complete[column] for column in native_columns),
            )


@pytest.mark.asyncio
async def test_pause_correction_requires_complete_input_manifest(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = insert_direct_run_base(db, old_state="running", manifest_complete=False)
        impact = {
            "run_id": "run-1", "expected_revision": 1, "expected_state": "running",
            "expected_output_validity": "current", "expected_last_event_id": row["last_event_id"],
            "expected_receipt_event_id": None, "resulting_state": "paused_dependency",
            "resulting_output_validity": "current", "resulting_revision": 2,
            "reason_code": "dependency_changed", "action": "pause_requested",
        }
        insert_event(
            db, task_id="task-1", event_id="confirm-2", sequence=2,
            event_type="frame.change_confirmed",
            payload={"impact": {"run_impacts": [impact]}},
            created_at="2026-07-14T00:01:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="complete input manifest"):
            db.execute(
                "UPDATE service_runs SET revision=2,last_event_id='confirm-2',"
                "updated_at='2026-07-14T00:01:00.000000Z',state='paused_dependency' "
                "WHERE run_id='run-1'"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("forgery", ("active", "queued_cardinality"))
async def test_queue_update_binds_complete_resulting_queue_snapshot(
    tmp_path: Path, forgery: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        active = decision_row(
            task_id="task-1", decision_id="decision-1", event_id="enqueue-1"
        )
        insert_enqueued_decision(db, active)
        db.execute(
            "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
            "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
            "VALUES ('task-1','main',1,'decision-1','[\"decision-1\"]',?,'enqueue-1',?)",
            (SHA_A, NOW),
        )

        queued_occurrence = {
            "classification": "accepted",
            "native_question_identity_checksum": "c" * 64,
            "occurrence_id": "question-decision-2",
            "occurrence_kind": "question",
        }
        queued = decision_row(
            task_id="task-1",
            decision_id="decision-2",
            event_id="enqueue-2",
            state="queued",
            queue_order=1,
            source_occurrences=[queued_occurrence],
        )
        queued["semantic_identity"] = "d" * 64
        active_snapshot = decision_snapshot(active)
        queued_snapshot = decision_snapshot(queued)
        resulting_queue = queue_snapshot(
            task_id="task-1",
            revision=2,
            checksum=SHA_B,
            active=queued_snapshot if forgery == "active" else active_snapshot,
            queued=[] if forgery == "queued_cardinality" else [queued_snapshot],
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="enqueue-2",
            sequence=2,
            event_type="decision.enqueued",
            payload={
                "candidate": {"source": queued_occurrence},
                "assessment": {
                    "disposition": "ask",
                    "semantic_identity": queued["semantic_identity"],
                },
                "decision": queued_snapshot,
                "expected_queue_revision": 1,
                "expected_queue_checksum": SHA_A,
                "expected_task_cache": None,
                "resulting_queue": resulting_queue,
                "resulting_task_state": "intake",
            },
        )
        insert_decision(db, queued)
        with pytest.raises(sqlite3.IntegrityError, match="resulting queue|active-first"):
            db.execute(
                "UPDATE decision_queue_heads SET revision=2,"
                "ordered_open_ids_json='[\"decision-1\",\"decision-2\"]',"
                "queue_checksum=?,last_event_id='enqueue-2',updated_at=? "
                "WHERE task_id='task-1' AND branch_id='main'",
                (SHA_B, NOW),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forgery", "expected_error"),
    (
        ("active_question", "resulting queue|snapshot"),
        ("queued_materiality", "resulting queue|snapshot"),
        ("queue_task_id", "resulting queue|snapshot"),
        ("queue_branch_id", "resulting queue|snapshot"),
    ),
)
async def test_queue_update_rejects_correct_ids_with_forged_snapshot_fields(
    tmp_path: Path, forgery: str, expected_error: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        active = decision_row(
            task_id="task-1", decision_id="decision-1", event_id="enqueue-1"
        )
        insert_enqueued_decision(db, active)
        db.execute(
            "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
            "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
            "VALUES ('task-1','main',1,'decision-1','[\"decision-1\"]',?,'enqueue-1',?)",
            (SHA_A, NOW),
        )
        occurrence = {
            "classification": "accepted",
            "native_question_identity_checksum": "c" * 64,
            "occurrence_id": "question-decision-2",
            "occurrence_kind": "question",
        }
        queued = decision_row(
            task_id="task-1", decision_id="decision-2", event_id="enqueue-2",
            state="queued", queue_order=1, source_occurrences=[occurrence],
        )
        queued["semantic_identity"] = "d" * 64
        active_snapshot = decision_snapshot(active)
        queued_snapshot = decision_snapshot(queued)
        resulting_queue = queue_snapshot(
            task_id="task-1", revision=2, checksum=SHA_B,
            active=active_snapshot, queued=[queued_snapshot],
        )
        if forgery == "active_question":
            active_snapshot["question"] = "Forged active question"
        elif forgery == "queued_materiality":
            queued_snapshot["materiality"] = {"forged": True}
        elif forgery == "queue_task_id":
            resulting_queue["task_id"] = "forged-task"
        else:
            resulting_queue["branch_id"] = "forged-branch"
        insert_event(
            db, task_id="task-1", event_id="enqueue-2", sequence=2,
            event_type="decision.enqueued",
            payload={
                "candidate": {"source": occurrence},
                "assessment": {"disposition": "ask", "semantic_identity": queued["semantic_identity"]},
                "decision": decision_snapshot(queued),
                "expected_queue_revision": 1,
                "expected_queue_checksum": SHA_A,
                "expected_task_cache": None,
                "resulting_queue": resulting_queue,
                "resulting_task_state": "intake",
            },
        )
        insert_decision(db, queued)
        with pytest.raises(sqlite3.IntegrityError, match=expected_error):
            db.execute(
                "UPDATE decision_queue_heads SET revision=2,"
                "ordered_open_ids_json='[\"decision-1\",\"decision-2\"]',"
                "queue_checksum=?,last_event_id='enqueue-2',updated_at=? "
                "WHERE task_id='task-1' AND branch_id='main'",
                (SHA_B, NOW),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forgery", ("active_question", "queue_task_id", "queue_branch_id")
)
async def test_queue_insert_rejects_forged_full_snapshot(
    tmp_path: Path, forgery: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        active = decision_row(
            task_id="task-1", decision_id="decision-1", event_id="enqueue-1"
        )
        active_snapshot = decision_snapshot(active)
        resulting_queue = queue_snapshot(
            task_id="task-1", revision=1, checksum=SHA_A,
            active=active_snapshot, queued=[],
        )
        if forgery == "active_question":
            active_snapshot["question"] = "Forged active question"
        elif forgery == "queue_task_id":
            resulting_queue["task_id"] = "forged-task"
        else:
            resulting_queue["branch_id"] = "forged-branch"
        insert_enqueued_decision(db, active, resulting_queue=resulting_queue)
        with pytest.raises(sqlite3.IntegrityError, match="resulting queue|snapshot|enqueue event"):
            db.execute(
                "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
                "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
                "VALUES ('task-1','main',1,'decision-1','[\"decision-1\"]',?,'enqueue-1',?)",
                (SHA_A, NOW),
            )


@pytest.mark.asyncio
async def test_queue_insert_and_update_accept_exact_full_snapshots(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        active = decision_row(
            task_id="task-1", decision_id="decision-1", event_id="enqueue-1"
        )
        insert_enqueued_decision(db, active)
        db.execute(
            "INSERT INTO decision_queue_heads(task_id,branch_id,revision,active_decision_id,"
            "ordered_open_ids_json,queue_checksum,last_event_id,updated_at) "
            "VALUES ('task-1','main',1,'decision-1','[\"decision-1\"]',?,'enqueue-1',?)",
            (SHA_A, NOW),
        )
        occurrence = {
            "classification": "accepted",
            "native_question_identity_checksum": "c" * 64,
            "occurrence_id": "question-decision-2",
            "occurrence_kind": "question",
        }
        queued = decision_row(
            task_id="task-1", decision_id="decision-2", event_id="enqueue-2",
            state="queued", queue_order=1, source_occurrences=[occurrence],
        )
        queued["semantic_identity"] = "d" * 64
        resulting_queue = queue_snapshot(
            task_id="task-1", revision=2, checksum=SHA_B,
            active=decision_snapshot(active), queued=[decision_snapshot(queued)],
        )
        insert_event(
            db, task_id="task-1", event_id="enqueue-2", sequence=2,
            event_type="decision.enqueued",
            payload={
                "candidate": {"source": occurrence},
                "assessment": {"disposition": "ask", "semantic_identity": queued["semantic_identity"]},
                "decision": decision_snapshot(queued),
                "expected_queue_revision": 1,
                "expected_queue_checksum": SHA_A,
                "expected_task_cache": None,
                "resulting_queue": resulting_queue,
                "resulting_task_state": "intake",
            },
        )
        insert_decision(db, queued)
        db.execute(
            "UPDATE decision_queue_heads SET revision=2,"
            "ordered_open_ids_json='[\"decision-1\",\"decision-2\"]',"
            "queue_checksum=?,last_event_id='enqueue-2',updated_at=? "
            "WHERE task_id='task-1' AND branch_id='main'",
            (SHA_B, NOW),
        )
        assert db.execute(
            "SELECT revision,active_decision_id,ordered_open_ids_json FROM decision_queue_heads"
        ).fetchone() == (2, "decision-1", '["decision-1","decision-2"]')


def install_direct_decision_with_prior_occurrences(
    db: sqlite3.Connection, occurrences: list[dict[str, object]]
) -> dict[str, object]:
    insert_event(
        db,
        task_id="task-1",
        event_id="enqueue-1",
        sequence=1,
        event_type="decision.enqueued",
        payload={"decision": {"decision_id": "decision-1"}},
    )
    insert_event(
        db,
        task_id="task-1",
        event_id="baseline-2",
        sequence=2,
        event_type="decision.free_form_received",
        payload={"decision_id": "decision-1"},
        created_at="2026-07-14T00:02:00.000000Z",
    )
    row = decision_row(
        task_id="task-1",
        decision_id="decision-1",
        event_id="baseline-2",
        revision=2,
        source_occurrences=occurrences,
    )
    row["created_by_event_id"] = "enqueue-1"
    db.execute("DROP TRIGGER decision_requests_task3_validate_insert")
    insert_decision(db, row)
    return row


@pytest.mark.asyncio
async def test_occurrence_update_rejects_replacement_or_duplication_of_prior_bytes(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    old_question = {
        "classification": "accepted",
        "native_question_identity_checksum": SHA_A,
        "occurrence_id": "question-1",
        "occurrence_kind": "question",
        "received_at": "2026-07-14T00:01:00.000000Z",
    }
    old_reply = {
        "classification": "duplicate",
        "native_reply_checksum": SHA_B,
        "occurrence_id": "reply-2",
        "occurrence_kind": "reply",
        "received_at": "2026-07-14T00:02:00.000000Z",
        "reply_kind": "free_form",
    }
    event_occurrence = {
        "classification": "accepted",
        "native_reply_checksum": "c" * 64,
        "occurrence_id": "reply-0",
        "occurrence_kind": "reply",
        "received_at": "2026-07-14T00:00:00.000000Z",
        "reply_kind": "free_form",
    }
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = install_direct_decision_with_prior_occurrences(db, [old_question, old_reply])
        forged_occurrences = [event_occurrence, old_question, old_question]
        resulting = dict(row)
        resulting.update(
            revision=3,
            last_event_id="reply-3",
            source_occurrences_json=canonical(forged_occurrences),
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="reply-3",
            sequence=3,
            event_type="decision.free_form_received",
            payload={
                "decision_id": "decision-1",
                "expected_decision_revision": 2,
                "occurrence": event_occurrence,
                "resulting_decision": decision_snapshot(resulting),
            },
            created_at="2026-07-14T00:03:00.000000Z",
        )
        with pytest.raises(sqlite3.IntegrityError, match="occurrence"):
            db.execute(
                "UPDATE decision_requests SET revision=3,last_event_id='reply-3',"
                "source_occurrences_json=? WHERE decision_id='decision-1'",
                (resulting["source_occurrences_json"],),
            )


@pytest.mark.asyncio
async def test_occurrence_update_accepts_event_occurrence_sorted_before_prior_items(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    old_question = {
        "classification": "accepted",
        "native_question_identity_checksum": SHA_A,
        "occurrence_id": "question-1",
        "occurrence_kind": "question",
        "received_at": "2026-07-14T00:01:00.000000Z",
    }
    old_reply = {
        "classification": "duplicate",
        "native_reply_checksum": SHA_B,
        "occurrence_id": "reply-2",
        "occurrence_kind": "reply",
        "received_at": "2026-07-14T00:02:00.000000Z",
        "reply_kind": "free_form",
    }
    event_occurrence = {
        "classification": "accepted",
        "native_reply_checksum": "c" * 64,
        "occurrence_id": "reply-0",
        "occurrence_kind": "reply",
        "received_at": "2026-07-14T00:00:00.000000Z",
        "reply_kind": "free_form",
    }
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        row = install_direct_decision_with_prior_occurrences(db, [old_question, old_reply])
        resulting = dict(row)
        resulting.update(
            revision=3,
            last_event_id="reply-3",
            source_occurrences_json=canonical([event_occurrence, old_question, old_reply]),
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="reply-3",
            sequence=3,
            event_type="decision.free_form_received",
            payload={
                "decision_id": "decision-1",
                "expected_decision_revision": 2,
                "occurrence": event_occurrence,
                "resulting_decision": decision_snapshot(resulting),
            },
            created_at="2026-07-14T00:03:00.000000Z",
        )
        db.execute(
            "UPDATE decision_requests SET revision=3,last_event_id='reply-3',"
            "source_occurrences_json=? WHERE decision_id='decision-1'",
            (resulting["source_occurrences_json"],),
        )
        assert json.loads(
            db.execute(
                "SELECT source_occurrences_json FROM decision_requests WHERE decision_id='decision-1'"
            ).fetchone()[0]
        ) == [event_occurrence, old_question, old_reply]


@pytest.mark.asyncio
async def test_native_attachment_event_cannot_advance_without_persisting_exact_envelope(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    attached = service_run_row(native=True)
    attachment = native_envelope(attached)
    assert attachment is not None
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        insert_direct_run_base(db, old_state="queued")
        event_time = "2026-07-14T00:01:00.000000Z"
        insert_event(
            db,
            task_id="task-1",
            event_id="transition-2",
            sequence=2,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": "queued",
                "expected_output_validity": "current",
                "new_state": "starting",
                "resulting_output_validity": "current",
                "reason_code": "dispatch_started",
                "native_identity_attachment": attachment,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at=event_time,
        )
        with pytest.raises(sqlite3.IntegrityError, match="native identity|attachment"):
            db.execute(
                "UPDATE service_runs SET revision=2,last_event_id='transition-2',updated_at=?,"
                "state='starting',started_at=? WHERE run_id='run-1'",
                (event_time, event_time),
            )


@pytest.mark.asyncio
async def test_null_native_attachment_requires_every_native_column_unchanged(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        insert_direct_run_base(db, old_state="queued")
        event_time = "2026-07-14T00:01:00.000000Z"
        insert_event(
            db,
            task_id="task-1",
            event_id="transition-2",
            sequence=2,
            event_type="service_run.state_changed",
            payload={
                "run_id": "run-1",
                "expected_revision": 1,
                "expected_state": "queued",
                "expected_output_validity": "current",
                "new_state": "starting",
                "resulting_output_validity": "current",
                "reason_code": "dispatch_started",
                "native_identity_attachment": None,
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at=event_time,
        )
        with pytest.raises(sqlite3.IntegrityError, match="native identity|immutable"):
            db.execute(
                "UPDATE service_runs SET revision=2,last_event_id='transition-2',updated_at=?,"
                "state='starting',started_at=?,adapter_provider='forged' WHERE run_id='run-1'",
                (event_time, event_time),
            )


@pytest.mark.asyncio
async def test_two_node_depends_on_confirmation_uses_frozen_relation_field(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    with sqlite3.connect(settings.state_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        insert_task_root(db, "task-1")
        operations = [
            {
                "operation_id": "operation-1",
                "operation_kind": "upsert",
                "new_node_id": "node-1",
                "node_key": "goal-1",
                "expected_current_node_id": None,
                "kind": "goal",
                "text": "Foundation",
                "value": {},
                "depends_on_node_keys": [],
                "provenance": {"source_event_ids": []},
            },
            {
                "operation_id": "operation-2",
                "operation_kind": "upsert",
                "new_node_id": "node-2",
                "node_key": "goal-2",
                "expected_current_node_id": None,
                "kind": "goal",
                "text": "Dependent",
                "value": {},
                "depends_on_node_keys": ["goal-1"],
                "provenance": {"source_event_ids": []},
            },
        ]
        proposal = proposal_row(
            task_id="task-1", proposal_id="proposal-1", event_id="proposal-1"
        )
        proposal["changes_json"] = canonical(
            {"edge_operations": [], "node_operations": operations}
        )
        insert_event(
            db,
            task_id="task-1",
            event_id="proposal-1",
            sequence=1,
            event_type="frame.proposal_created",
            payload={"proposal": proposal_snapshot(proposal)},
        )
        insert_proposal(db, proposal)
        nodes = [
            {
                "node_id": "node-1",
                "node_key": "goal-1",
                "task_id": "task-1",
                "branch_id": "main",
                "frame_version": 1,
                "kind": "goal",
                "text": "Foundation",
                "value": {},
                "status": "confirmed",
                "supersedes_node_id": None,
                "depends_on": [],
                "provenance_event_ids": [],
            },
            {
                "node_id": "node-2",
                "node_key": "goal-2",
                "task_id": "task-1",
                "branch_id": "main",
                "frame_version": 1,
                "kind": "goal",
                "text": "Dependent",
                "value": {},
                "status": "confirmed",
                "supersedes_node_id": None,
                "depends_on": ["node-1"],
                "provenance_event_ids": [],
            },
        ]
        edge = {
            "edge_id": "edge-1",
            "task_id": "task-1",
            "branch_id": "main",
            "frame_version": 1,
            "from_node_id": "node-2",
            "to_node_id": "node-1",
            "relation": "depends_on",
        }
        confirm_time = "2026-07-14T00:01:00.000000Z"
        insert_event(
            db,
            task_id="task-1",
            event_id="confirm-2",
            sequence=2,
            event_type="frame.change_confirmed",
            frame_version=1,
            payload={
                "proposal_id": "proposal-1",
                "expected_preview_state_checksum": SHA_B,
                "expected_impact_preview_checksum": "c" * 64,
                "confirmed": {
                    "task_id": "task-1",
                    "branch_id": "main",
                    "frame_version": 1,
                    "nodes": nodes,
                    "edges": [edge],
                },
                "impact": {"evidence_impacts": [], "run_impacts": []},
                "proposal_supersessions": [],
                "expected_task_cache": None,
                "resulting_task_state": "intake",
            },
            created_at=confirm_time,
        )
        db.execute(
            "UPDATE frame_proposals SET status='accepted',decided_by_event_id='confirm-2',"
            "decided_at=? WHERE id='proposal-1'",
            (confirm_time,),
        )
        for node in nodes:
            db.execute(
                "INSERT INTO frame_nodes(node_id,task_id,branch_id,frame_version,node_key,"
                "supersedes_node_id,kind,text,value_json,status,depends_on_json,"
                "provenance_event_ids_json,provenance_json,created_by_event_id,"
                "invalidated_by_event_id,created_at,updated_at) VALUES "
                "(?,?,?,?,?,NULL,?,?,?,'confirmed',?,'[]','{\"source_event_ids\":[]}',"
                "'confirm-2',NULL,?,?)",
                (
                    node["node_id"],
                    node["task_id"],
                    node["branch_id"],
                    node["frame_version"],
                    node["node_key"],
                    node["kind"],
                    node["text"],
                    canonical(node["value"]),
                    canonical(node["depends_on"]),
                    confirm_time,
                    confirm_time,
                ),
            )
        db.execute(
            "INSERT INTO frame_edges(id,task_id,branch_id,frame_version,from_node_id,to_node_id,"
            "edge_type,created_by_event_id,created_at) VALUES "
            "('edge-1','task-1','main',1,'node-2','node-1','depends_on','confirm-2',?)",
            (confirm_time,),
        )
        assert db.execute("SELECT COUNT(*) FROM frame_nodes").fetchone() == (2,)
        assert db.execute("SELECT edge_type FROM frame_edges").fetchone() == ("depends_on",)
