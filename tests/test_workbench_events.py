from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import aiosqlite
from pydantic import BaseModel, ConfigDict

from orchestrator.config import Settings
from orchestrator.workbench.events import (
    BranchForkedPayload,
    EventDefinition,
    EventRegistry,
    FrameEffect,
    GENESIS_CHECKSUM,
    TaskCreatedPayload,
    canonical_json_bytes,
    drafts_checksum,
    event_checksum,
    manifest_checksum,
    normalize_timestamp,
)
from orchestrator.workbench.models import (
    EventActor,
    EventCause,
    EventDraft,
    EventValidationError,
    IdempotencyConflict,
    InvalidCause,
    LedgerCorruption,
    StaleFrameVersion,
    UnsupportedEventType,
)
from orchestrator.workbench.store import WorkbenchStore
from orchestrator.workbench.projector import BranchTopology, Projector, ReplayPlan
from orchestrator.state.store import StateStore


ROOT = Path(__file__).resolve().parents[1]


def settings_for(tmp_path: Path) -> Settings:
    home = tmp_path / "runtime"
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=ROOT,
    )


def sql_event_envelope(row: sqlite3.Row, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "event_schema_version": int(row["event_schema_version"]),
        "event_id": str(row["event_id"]),
        "task_id": str(row["task_id"]),
        "sequence": int(row["sequence"]),
        "event_type": str(row["event_type"]),
        "actor_kind": str(row["actor_kind"]),
        "actor_id": str(row["actor_id"]),
        "branch_id": str(row["branch_id"]),
        "cause": row["cause"],
        "caused_by": row["caused_by"],
        "command_id": str(row["command_id"]),
        "command_sequence": int(row["command_sequence"]),
        "frame_version": int(row["frame_version"]),
        "payload": json.loads(str(row["payload_json"])),
        "idempotency_key": str(row["idempotency_key"]),
        "created_at": str(row["created_at"]),
        "prior_checksum": str(row["prior_checksum"]),
    }
    values.update(changes)
    return values


def sql_manifest_fields(row: sqlite3.Row, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        key: row[key]
        for key in (
            "task_id", "command_id", "target_branch_id", "event_count", "first_sequence",
            "last_sequence", "first_event_id", "last_event_id", "starting_frame_version",
            "expected_frame_version", "confirm_ordinal", "drafts_checksum", "created_at",
        )
    }
    values.update(changes)
    return values


def test_canonical_json_and_timestamp_are_stable_and_unicode_preserving() -> None:
    assert canonical_json_bytes({"z": 1, "a": "café", "nested": {"b": 2, "a": 1}}) == (
        b'{"a":"caf\xc3\xa9","nested":{"a":1,"b":2},"z":1}'
    )
    instant = datetime(2026, 7, 14, 12, 30, 1, 42, tzinfo=timezone(timedelta(hours=-5)))
    assert normalize_timestamp(instant) == "2026-07-14T17:30:01.000042Z"
    assert normalize_timestamp("2026-07-14T17:30:01.000042+00:00") == "2026-07-14T17:30:01.000042Z"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"value": value})


@pytest.mark.parametrize("value", [(1, 2), {1, 2}, datetime.now(timezone.utc), b"bytes"])
def test_canonical_json_rejects_values_outside_the_declared_json_domain(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes({"value": value})


def test_event_checksum_covers_the_full_immutable_envelope() -> None:
    envelope = {
        "event_schema_version": 1,
        "event_id": "evt-1",
        "task_id": "task-1",
        "sequence": 1,
        "event_type": "task.created",
        "actor_kind": "owner",
        "actor_id": "matt",
        "branch_id": "main",
        "cause": None,
        "caused_by": None,
        "command_id": "cmd-1",
        "command_sequence": 1,
        "frame_version": 0,
        "payload": {"title": "Build it"},
        "idempotency_key": "idem-1",
        "created_at": "2026-07-14T17:30:01.000042Z",
        "prior_checksum": GENESIS_CHECKSUM,
    }
    checksum = event_checksum(envelope)
    assert checksum == hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
    for field in envelope:
        changed = dict(envelope)
        changed[field] = "different"
        assert event_checksum(changed) != checksum


def test_command_manifest_checksum_helpers_match_frozen_v1_vectors() -> None:
    class NestedVector(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        count: int
        note: str | None

    class VectorPayload(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        title: str
        details: NestedVector

    class ConfirmPayload(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        confirmed: bool
        details: NestedVector

    registry = EventRegistry()
    registry.register(EventDefinition(
        event_type="fixture.nested", event_schema_version=1, payload_model=VectorPayload,
        frame_effect=FrameEffect.INHERIT, reducer=lambda state, event: state,
        authority_participant="core",
    ))
    registry.register(EventDefinition(
        event_type="fixture.confirmed", event_schema_version=1, payload_model=ConfirmPayload,
        frame_effect=FrameEffect.CONFIRM, reducer=lambda state, event: state,
        authority_participant="core",
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
    digest = drafts_checksum("task-Ω", "cmd-1", validated)
    assert digest == "196c225b4ee2d3cc000abaded415649c6fb831cf5cd09fd0bca2e70f967d62c0"
    manifest = {
        "task_id": "task-Ω", "command_id": "cmd-1", "target_branch_id": "main",
        "event_count": 2, "first_sequence": 1, "last_sequence": 2,
        "first_event_id": "event-1", "last_event_id": "event-2",
        "starting_frame_version": 0, "expected_frame_version": 0,
        "confirm_ordinal": None, "drafts_checksum": digest,
        "created_at": "2026-07-14T12:00:00.000000Z",
    }
    assert manifest_checksum(manifest) == "0c9b063cf6b4d4cc6661c0633252a5360e91b107bdccbd59889fbd801e4e2306"
    manifest["confirm_ordinal"] = 2
    assert manifest_checksum(manifest) == "eb16368cc351de06ff440dafae11f7a2f43cbae35d0c4d16838f5bb835f7f1e3"


def test_event_draft_is_typed_frozen_and_rejects_unknown_fields() -> None:
    draft = EventDraft(
        event_type="task.created",
        actor=EventActor(kind="owner", actor_id="matt"),
        payload={"title": "Build it"},
    )
    assert draft.event_schema_version == 1
    with pytest.raises(Exception):
        draft.event_type = "changed"  # type: ignore[misc]
    with pytest.raises(Exception):
        EventDraft(
            event_type="task.created",
            actor=EventActor(kind="owner", actor_id="matt"),
            payload={"title": "Build it"},
            unknown=True,  # type: ignore[call-arg]
        )


def test_draft_forbids_store_owned_authority_fields() -> None:
    base = {
        "event_type": "task.created",
        "actor": EventActor(kind="owner", actor_id="matt"),
        "payload": {"title": "Build it", "initial_branch_id": "main"},
    }
    for field, value in (
        ("idempotency_key", "caller-key"),
        ("expected_frame_version", 0),
        ("event_id", "caller-event"),
        ("created_at", "2026-01-01T00:00:00Z"),
    ):
        with pytest.raises(Exception):
            EventDraft(**base, **{field: value})
    with pytest.raises(Exception):
        EventDraft(**base, cause="because I felt like it")
    assert EventDraft(**base, cause=EventCause.OWNER_REQUEST).cause is EventCause.OWNER_REQUEST


def test_production_registry_is_bounded_typed_and_declares_frame_effects() -> None:
    registry = EventRegistry.production()
    created = registry.validate(
        EventDraft(
            event_type="task.created",
            actor=EventActor(kind="owner", actor_id="matt"),
            payload={"title": "Build it", "initial_branch_id": "main"},
        )
    )
    assert isinstance(created.payload, TaskCreatedPayload)
    assert created.definition.frame_effect is FrameEffect.INHERIT
    forked = registry.validate(
        EventDraft(
            event_type="branch.forked",
            actor=EventActor(kind="owner", actor_id="matt"),
            branch_id="main",
            payload={
                "new_branch_id": "option-b",
                "parent_branch_id": "main",
                "forked_from_sequence": 1,
                "forked_from_frame_version": 0,
            },
        )
    )
    assert isinstance(forked.payload, BranchForkedPayload)
    with pytest.raises(UnsupportedEventType):
        registry.validate(
            EventDraft(
                event_type="frame.confirmed",
                actor=EventActor(kind="owner", actor_id="matt"),
                payload={},
            )
        )
    with pytest.raises(Exception):
        registry.validate(
            EventDraft(
                event_type="task.created",
                actor=EventActor(kind="owner", actor_id="matt"),
                payload={"title": "Build it", "initial_branch_id": "main", "extra": True},
            )
        )


class ValuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str


class NestedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    nested: dict[str, str]


class IntegerValuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: int


def fixture_registry(*, effect: FrameEffect = FrameEffect.INHERIT) -> EventRegistry:
    registry = EventRegistry.production().copy()

    def reduce_value(state: dict[str, object], event: object) -> dict[str, object]:
        result = dict(state)
        result["value"] = getattr(event, "payload")["value"]
        return result

    registry.register(
        EventDefinition(
            event_type="fixture.value",
            event_schema_version=1,
            payload_model=ValuePayload,
            frame_effect=effect,
            reducer=reduce_value,
            authority_participant="core",
        )
    )
    return registry


def integer_fixture_registry() -> EventRegistry:
    registry = EventRegistry.production().copy()

    def reduce_value(state: dict[str, object], event: object) -> dict[str, object]:
        result = dict(state)
        result["value"] = getattr(event, "payload")["value"]
        return result

    registry.register(
        EventDefinition(
            event_type="fixture.integer",
            event_schema_version=1,
            payload_model=IntegerValuePayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=reduce_value,
            authority_participant="core",
        )
    )
    return registry


@pytest.mark.asyncio
async def test_create_task_is_atomic_and_exact_command_retry_returns_original(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    ids = iter(("evt-created", "must-not-be-used"))
    store = WorkbenchStore(
        settings,
        id_factory=lambda: next(ids),
        clock=lambda: datetime(2026, 7, 14, 12, 0, tzinfo=timezone(timedelta(hours=-5))),
    )
    actor = EventActor(kind="owner", actor_id="matt")

    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    retried = await store.create_task("task-1", "Build it", "cmd-create", actor)

    assert retried == created
    assert created.sequence == 1
    assert created.command_sequence == 1
    assert created.created_at == "2026-07-14T17:00:00.000000Z"
    assert created.prior_checksum == GENESIS_CHECKSUM
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT COUNT(*) FROM workbench_tasks").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM workbench_branches").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM workbench_events").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM projection_metadata").fetchone() == (1,)

    with pytest.raises(IdempotencyConflict):
        await store.create_task("task-1", "Changed", "cmd-create", actor)


@pytest.mark.asyncio
async def test_single_append_exact_retry_changed_content_and_confirm_version(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry(effect=FrameEffect.CONFIRM)
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    draft = EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"})

    with pytest.raises(StaleFrameVersion):
        await store.append("task-1", "cmd-value", draft)
    event = await store.append("task-1", "cmd-value", draft, expected_frame_version=0)
    assert event.frame_version == 1
    assert await store.append("task-1", "cmd-value", draft, expected_frame_version=0) == event
    with pytest.raises(IdempotencyConflict):
        await store.append("task-1", "cmd-value", draft, expected_frame_version=1)
    with pytest.raises(IdempotencyConflict):
        await store.append(
            "task-1",
            "cmd-value",
            EventDraft(event_type="fixture.value", actor=actor, payload={"value": "B"}),
            expected_frame_version=0,
        )


@pytest.mark.asyncio
async def test_batch_is_contiguous_chained_atomic_and_whole_command_idempotent(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    drafts = (
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"}),
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "B"}),
    )

    events = await store.append_batch("task-1", "cmd-batch", drafts)
    assert [event.sequence for event in events] == [2, 3]
    assert [event.command_sequence for event in events] == [1, 2]
    assert events[0].prior_checksum == created.checksum
    assert events[1].prior_checksum == events[0].checksum
    assert len({event.idempotency_key for event in events}) == 2
    assert await store.append_batch("task-1", "cmd-batch", drafts) == events

    with pytest.raises(IdempotencyConflict):
        await store.append_batch("task-1", "cmd-batch", drafts[:1])
    with pytest.raises(IdempotencyConflict):
        await store.append_batch(
            "task-1",
            "cmd-batch",
            (
                drafts[0],
                EventDraft(event_type="fixture.value", actor=actor, payload={"value": "changed"}),
            ),
        )
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT sequence FROM workbench_events ORDER BY sequence").fetchall() == [
            (1,),
            (2,),
            (3,),
        ]
        assert db.execute(
            """
            SELECT event_count,first_sequence,last_sequence,first_event_id,last_event_id,
                   starting_frame_version,expected_frame_version,confirm_ordinal,created_at
            FROM workbench_command_manifests
            WHERE task_id='task-1' AND command_id='cmd-batch'
            """
        ).fetchone() == (
            2, 2, 3, events[0].event_id, events[1].event_id, 0, 0, None, events[0].created_at
        )
        assert db.execute(
            "SELECT COUNT(DISTINCT created_at) FROM workbench_events WHERE command_id='cmd-batch'"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_batch_rejects_empty_cross_branch_and_multiple_confirms(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    draft = EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"})
    store = WorkbenchStore(settings, registry=fixture_registry(effect=FrameEffect.CONFIRM))
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with pytest.raises(EventValidationError, match="at least one"):
        await store.append_batch("task-1", "cmd-empty", ())
    with pytest.raises(EventValidationError, match="one target branch"):
        await store.append_batch(
            "task-1",
            "cmd-branches",
            (draft, draft.model_copy(update={"branch_id": "other"})),
        )
    with pytest.raises(EventValidationError, match="at most one"):
        await store.append_batch(
            "task-1", "cmd-confirm", (draft, draft), expected_frame_version=0
        )


@pytest.mark.asyncio
async def test_reducer_fault_rolls_back_entire_command(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()

    def explode(state: dict[str, object], event: object) -> dict[str, object]:
        raise RuntimeError("fixture reducer fault")

    registry.register(
        EventDefinition(
            event_type="fixture.explode",
            event_schema_version=1,
            payload_model=ValuePayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=explode,
            authority_participant="core",
        )
    )
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with pytest.raises(RuntimeError, match="reducer fault"):
        await store.append_batch(
            "task-1",
            "cmd-fault",
            (
                EventDraft(event_type="fixture.value", actor=actor, payload={"value": "before"}),
                EventDraft(event_type="fixture.explode", actor=actor, payload={"value": "boom"}),
            ),
        )
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT COUNT(*) FROM workbench_events").fetchone() == (1,)
    assert (await store.snapshot("task-1")).state.get("value") is None


@pytest.mark.asyncio
async def test_fork_snapshot_stops_parent_at_cutoff_and_excludes_siblings(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    parent_before = await store.append(
        "task-1",
        "cmd-parent-before",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "parent-before"}),
    )
    fork = await store.fork_branch(
        "task-1", "cmd-fork", "main", "option-b", parent_before.sequence, actor
    )
    assert await store.fork_branch(
        "task-1", "cmd-fork", "main", "option-b", parent_before.sequence, actor
    ) == fork
    parent_after = await store.append(
        "task-1",
        "cmd-parent-after",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "parent-after"}),
    )
    child = await store.append(
        "task-1",
        "cmd-child",
        EventDraft(
            event_type="fixture.value",
            actor=actor,
            branch_id="option-b",
            caused_by=parent_before.event_id,
            payload={"value": "child"},
        ),
    )
    with pytest.raises(IdempotencyConflict):
        await store.append(
            "task-1",
            "cmd-child",
            EventDraft(
                event_type="fixture.value",
                actor=actor,
                branch_id="main",
                caused_by=parent_before.event_id,
                payload={"value": "child"},
            ),
        )
    await store.fork_branch("task-1", "cmd-sibling", "main", "option-c", parent_after.sequence, actor)
    sibling = await store.append(
        "task-1",
        "cmd-sibling-value",
        EventDraft(
            event_type="fixture.value",
            actor=actor,
            branch_id="option-c",
            payload={"value": "sibling"},
        ),
    )

    child_snapshot = await store.snapshot("task-1", "option-b")
    assert child_snapshot.state["value"] == "child"
    assert child_snapshot.head.head_sequence == child.sequence
    historical = await store.snapshot("task-1", "option-b", at_sequence=parent_before.sequence)
    assert historical.state["value"] == "parent-before"
    assert historical.head.head_sequence == parent_before.sequence
    with pytest.raises(InvalidCause):
        await store.append(
            "task-1",
            "cmd-bad-parent-cause",
            EventDraft(
                event_type="fixture.value",
                actor=actor,
                branch_id="option-b",
                caused_by=parent_after.event_id,
                payload={"value": "bad"},
            ),
        )
    with pytest.raises(InvalidCause):
        await store.append(
            "task-1",
            "cmd-bad-sibling-cause",
            EventDraft(
                event_type="fixture.value",
                actor=actor,
                branch_id="option-b",
                caused_by=sibling.event_id,
                payload={"value": "bad"},
            ),
        )
    assert created.event_id != parent_before.event_id


@pytest.mark.asyncio
async def test_fork_rejects_zero_future_nonvisible_and_existing_branch(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with pytest.raises(EventValidationError):
        await store.fork_branch("task-1", "cmd-zero", "main", "zero", 0, actor)
    with pytest.raises(EventValidationError):
        await store.fork_branch("task-1", "cmd-future", "main", "future", 99, actor)
    await store.fork_branch("task-1", "cmd-fork", "main", "option-b", 1, actor)
    with pytest.raises(IdempotencyConflict):
        await store.fork_branch("task-1", "cmd-other", "main", "option-b", 1, actor)


@pytest.mark.asyncio
async def test_non_head_corruption_reports_first_invalid_sequence_and_blocks_append(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.append(
        "task-1",
        "cmd-a",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"}),
    )
    await store.append(
        "task-1",
        "cmd-b",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "B"}),
    )
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_events_no_update")
        db.execute(
            "UPDATE workbench_events SET payload_json=? WHERE task_id='task-1' AND sequence=2",
            (json.dumps({"value": "tampered"}, sort_keys=True, separators=(",", ":")),),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert verification.first_invalid_sequence == 2
    assert "checksum" in str(verification.reason)
    with pytest.raises(LedgerCorruption) as failure:
        await store.append(
            "task-1",
            "cmd-c",
            EventDraft(event_type="fixture.value", actor=actor, payload={"value": "C"}),
        )
    assert failure.value.sequence == 2


@pytest.mark.asyncio
async def test_projection_corruption_is_repaired_by_atomic_metadata_only_rebuild(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.append(
        "task-1",
        "cmd-a",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"}),
    )
    with sqlite3.connect(settings.state_path) as db:
        authority_before = db.execute(
            "SELECT * FROM workbench_events ORDER BY sequence"
        ).fetchall()
        db.execute(
            "UPDATE projection_metadata SET canonical_state_json='{}',state_checksum=? WHERE task_id='task-1'",
            ("0" * 64,),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "projection metadata" in str(verification.reason)
    first_heads = await Projector(store.registry).rebuild(store, "task-1")
    second_heads = await Projector(store.registry).rebuild(store, "task-1")
    assert first_heads == second_heads
    assert (await store.verify_ledger("task-1")).valid is True
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT * FROM workbench_events ORDER BY sequence").fetchall() == authority_before
        assert db.execute(
            "SELECT updated_at FROM projection_metadata WHERE task_id='task-1'"
        ).fetchone() == db.execute(
            "SELECT created_at FROM workbench_events WHERE task_id='task-1' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()


@pytest.mark.asyncio
async def test_historical_snapshot_never_publishes_projection_metadata(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.append(
        "task-1",
        "cmd-a",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"}),
    )
    with sqlite3.connect(settings.state_path) as db:
        before = db.execute("SELECT * FROM projection_metadata").fetchall()
    historical = await store.snapshot("task-1", at_sequence=created.sequence)
    assert historical.head.head_sequence == created.sequence
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT * FROM projection_metadata").fetchall() == before


@pytest.mark.asyncio
async def test_cancellation_after_event_insert_rolls_back_event_and_projection(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    publish_started = asyncio.Event()
    release = asyncio.Event()
    original_publish = store._publish_snapshot

    async def paused_publish(db: object, snapshot: object, updated_at: str) -> None:
        publish_started.set()
        await release.wait()
        await original_publish(db, snapshot, updated_at)  # type: ignore[arg-type]

    store._publish_snapshot = paused_publish  # type: ignore[method-assign]
    task = asyncio.create_task(
        store.append(
            "task-1",
            "cmd-cancel",
            EventDraft(event_type="fixture.value", actor=actor, payload={"value": "cancel"}),
        )
    )
    await asyncio.wait_for(publish_started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT COUNT(*) FROM workbench_events").fetchone() == (1,)
    store._publish_snapshot = original_publish  # type: ignore[method-assign]
    assert (await store.verify_ledger("task-1")).valid is True


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_rollback_and_preserves_original_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with sqlite3.connect(settings.state_path) as db:
        before = (
            db.execute("SELECT COUNT(*) FROM workbench_events").fetchone(),
            db.execute("SELECT COUNT(*) FROM workbench_command_manifests").fetchone(),
            db.execute("SELECT COUNT(*) FROM projection_metadata").fetchone(),
        )

    publish_started = asyncio.Event()
    rollback_started = asyncio.Event()
    release_rollback = asyncio.Event()
    original_publish = store._publish_snapshot
    original_rollback = aiosqlite.Connection.rollback

    async def paused_publish(db: object, snapshot: object, updated_at: str) -> None:
        publish_started.set()
        await asyncio.Future()

    async def paused_rollback(connection: aiosqlite.Connection) -> None:
        rollback_started.set()
        await release_rollback.wait()
        await original_rollback(connection)

    store._publish_snapshot = paused_publish  # type: ignore[method-assign]
    monkeypatch.setattr(aiosqlite.Connection, "rollback", paused_rollback)
    task = asyncio.create_task(
        store.append(
            "task-1",
            "cmd-double-cancel",
            EventDraft(event_type="fixture.value", actor=actor, payload={"value": "cancel"}),
        )
    )
    await asyncio.wait_for(publish_started.wait(), timeout=5)
    task.cancel("first cancellation")
    await asyncio.wait_for(rollback_started.wait(), timeout=5)
    task.cancel("second cancellation")
    await asyncio.sleep(0)
    release_rollback.set()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(task, timeout=5)
    assert cancelled.value.args == ("first cancellation",)

    monkeypatch.setattr(aiosqlite.Connection, "rollback", original_rollback)
    store._publish_snapshot = original_publish  # type: ignore[method-assign]
    with sqlite3.connect(settings.state_path) as db:
        after = (
            db.execute("SELECT COUNT(*) FROM workbench_events").fetchone(),
            db.execute("SELECT COUNT(*) FROM workbench_command_manifests").fetchone(),
            db.execute("SELECT COUNT(*) FROM projection_metadata").fetchone(),
        )
    assert after == before
    assert (await store.verify_ledger("task-1")).valid is True
    appended = await store.append(
        "task-1",
        "cmd-after-double-cancel",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "open"}),
    )
    assert appended.sequence == 2


@pytest.mark.asyncio
async def test_two_store_contention_keeps_global_sequence_contiguous_and_retry_exact(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    first = WorkbenchStore(settings, registry=registry)
    second = WorkbenchStore(settings, registry=registry)
    await first.create_task("task-1", "Build it", "cmd-create", actor)
    draft_a = EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"})
    draft_b = EventDraft(event_type="fixture.value", actor=actor, payload={"value": "B"})
    event_a, event_b = await asyncio.wait_for(
        asyncio.gather(
            first.append("task-1", "cmd-a", draft_a),
            second.append("task-1", "cmd-b", draft_b),
        ),
        timeout=10,
    )
    assert sorted((event_a.sequence, event_b.sequence)) == [2, 3]
    retry_one, retry_two = await asyncio.gather(
        first.append("task-1", "cmd-a", draft_a),
        second.append("task-1", "cmd-a", draft_a),
    )
    assert retry_one == retry_two == event_a
    assert (await first.verify_ledger("task-1")).valid is True
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT sequence FROM workbench_events ORDER BY sequence").fetchall() == [
            (1,),
            (2,),
            (3,),
        ]


@pytest.mark.asyncio
async def test_concurrent_multi_event_commands_have_one_atomic_manifest_each(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    first = WorkbenchStore(settings, registry=registry)
    second = WorkbenchStore(settings, registry=registry)
    await first.create_task("task-1", "Build it", "cmd-create", actor)
    drafts_a = tuple(
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": value})
        for value in ("A1", "A2")
    )
    drafts_b = tuple(
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": value})
        for value in ("B1", "B2")
    )
    result_a, result_b = await asyncio.gather(
        first.append_batch("task-1", "cmd-a", drafts_a),
        second.append_batch("task-1", "cmd-b", drafts_b),
    )
    assert [event.sequence for event in result_a] in ([2, 3], [4, 5])
    assert [event.sequence for event in result_b] in ([2, 3], [4, 5])
    assert result_a[0].sequence != result_b[0].sequence

    retry_a, retry_b = await asyncio.gather(
        first.append_batch("task-1", "cmd-a", drafts_a),
        second.append_batch("task-1", "cmd-a", drafts_a),
    )
    assert retry_a == retry_b == result_a
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute(
            "SELECT command_id,event_count,first_sequence,last_sequence "
            "FROM workbench_command_manifests WHERE task_id='task-1' ORDER BY first_sequence"
        ).fetchall() == [
            ("cmd-create", 1, 1, 1),
            (result_a[0].command_id if result_a[0].sequence == 2 else result_b[0].command_id, 2, 2, 3),
            (result_a[0].command_id if result_a[0].sequence == 4 else result_b[0].command_id, 2, 4, 5),
        ]
        assert db.execute(
            "SELECT command_id,COUNT(*),MIN(sequence),MAX(sequence) "
            "FROM workbench_events GROUP BY command_id ORDER BY MIN(sequence)"
        ).fetchall() == [
            ("cmd-create", 1, 1, 1),
            (result_a[0].command_id if result_a[0].sequence == 2 else result_b[0].command_id, 2, 2, 3),
            (result_a[0].command_id if result_a[0].sequence == 4 else result_b[0].command_id, 2, 4, 5),
        ]
    assert (await first.verify_ledger("task-1")).valid is True

    await first.create_task("task-2", "Race it", "cmd-create-2", actor)
    race_results = await asyncio.gather(
        first.append_batch("task-2", "cmd-race", drafts_a),
        second.append_batch("task-2", "cmd-race", drafts_b),
        return_exceptions=True,
    )
    successes = [result for result in race_results if isinstance(result, tuple)]
    conflicts = [result for result in race_results if isinstance(result, IdempotencyConflict)]
    assert len(successes) == len(conflicts) == 1
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM workbench_command_manifests "
            "WHERE task_id='task-2' AND command_id='cmd-race'"
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT COUNT(*) FROM workbench_events "
            "WHERE task_id='task-2' AND command_id='cmd-race'"
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_store_connections_enable_foreign_keys_and_bounded_busy_timeout(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    store = WorkbenchStore(settings)
    async with store._connection() as db:
        assert (await (await db.execute("PRAGMA foreign_keys")).fetchone())[0] == 1
        assert (await (await db.execute("PRAGMA busy_timeout")).fetchone())[0] == 5000


@pytest.mark.asyncio
async def test_missing_unknown_invalid_and_cross_task_inputs_fail_closed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    draft = EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"})
    with pytest.raises(Exception, match="task not found"):
        await store.append("missing", "cmd", draft)
    with pytest.raises(EventValidationError):
        await store.create_task("blank-title", "   ", "cmd-blank", actor)
    first_created = await store.create_task("task-1", "One", "cmd-create-1", actor)
    await store.create_task("task-2", "Two", "cmd-create-2", actor)
    with pytest.raises(Exception):
        await store.append(
            "task-1",
            "cmd-unknown",
            EventDraft(event_type="unknown.event", actor=actor, payload={}),
        )
    with pytest.raises(Exception):
        await store.append(
            "task-1",
            "cmd-version",
            EventDraft(
                event_type="fixture.value",
                event_schema_version=2,
                actor=actor,
                payload={"value": "A"},
            ),
        )
    with pytest.raises(Exception):
        await store.append(
            "task-1",
            "cmd-extra",
            EventDraft(
                event_type="fixture.value",
                actor=actor,
                payload={"value": "A", "extra": True},
            ),
        )
    with pytest.raises(InvalidCause):
        await store.append(
            "task-2",
            "cmd-cross-cause",
            EventDraft(
                event_type="fixture.value",
                actor=actor,
                caused_by=first_created.event_id,
                payload={"value": "cross"},
            ),
        )


@pytest.mark.asyncio
async def test_confirm_and_propose_effects_are_registry_metadata_not_event_names(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry(effect=FrameEffect.CONFIRM)

    def reduce_proposal(state: dict[str, object], event: object) -> dict[str, object]:
        result = dict(state)
        result["proposal"] = getattr(event, "payload")["value"]
        return result

    registry.register(
        EventDefinition(
            event_type="fixture.looks_confirmed_but_is_proposal",
            event_schema_version=1,
            payload_model=ValuePayload,
            frame_effect=FrameEffect.PROPOSE,
            reducer=reduce_proposal,
            authority_participant="core",
        )
    )
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.append(
        "task-1",
        "cmd-confirm",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "confirmed"}),
        expected_frame_version=0,
    )
    proposal = await store.append(
        "task-1",
        "cmd-proposal",
        EventDraft(
            event_type="fixture.looks_confirmed_but_is_proposal",
            actor=actor,
            payload={"value": "proposal"},
        ),
    )
    assert proposal.frame_version == 1
    with pytest.raises(StaleFrameVersion):
        await store.append(
            "task-1",
            "cmd-stale",
            EventDraft(event_type="fixture.value", actor=actor, payload={"value": "stale"}),
            expected_frame_version=0,
        )
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute(
            "SELECT current_frame_version FROM workbench_tasks WHERE id='task-1'"
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_rebuild_holds_write_boundary_so_concurrent_append_is_not_lost(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    rebuilding = WorkbenchStore(settings, registry=registry)
    appending = WorkbenchStore(settings, registry=registry)
    await rebuilding.create_task("task-1", "Build it", "cmd-create", actor)
    live_before = await rebuilding.snapshot("task-1")
    publish_started = asyncio.Event()
    release = asyncio.Event()
    original_publish = rebuilding._publish_snapshot

    async def paused_publish(db: object, snapshot: object, updated_at: str) -> None:
        publish_started.set()
        await release.wait()
        await original_publish(db, snapshot, updated_at)  # type: ignore[arg-type]

    rebuilding._publish_snapshot = paused_publish  # type: ignore[method-assign]
    rebuild_task = asyncio.create_task(Projector(registry).rebuild(rebuilding, "task-1"))
    await asyncio.wait_for(publish_started.wait(), timeout=5)
    append_task = asyncio.create_task(
        appending.append(
            "task-1",
            "cmd-during-rebuild",
            EventDraft(event_type="fixture.value", actor=actor, payload={"value": "after-lock"}),
        )
    )
    await asyncio.sleep(0.05)
    assert append_task.done() is False
    release.set()
    await rebuild_task
    appended = await append_task
    assert appended.sequence == 2
    live_after = await rebuilding.snapshot("task-1")
    assert live_before.state != live_after.state
    assert live_after.state["value"] == "after-lock"
    rebuilt = await Projector(registry).rebuild(rebuilding, "task-1")
    assert rebuilt[0] == live_after.head


@pytest.mark.asyncio
async def test_create_task_retry_verifies_chain_and_projection_before_return(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with sqlite3.connect(settings.state_path) as db:
        db.execute(
            "UPDATE projection_metadata SET state_checksum=? WHERE task_id='task-1'",
            ("0" * 64,),
        )
    with pytest.raises(LedgerCorruption, match="projection metadata"):
        await store.create_task("task-1", "Build it", "cmd-create", actor)


@pytest.mark.asyncio
async def test_rebuild_rejects_branch_topology_not_bound_to_fork_event(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    before = await store.append(
        "task-1",
        "cmd-before",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "before"}),
    )
    await store.fork_branch("task-1", "cmd-fork", "main", "child", before.sequence, actor)
    after = await store.append(
        "task-1",
        "cmd-after",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "after"}),
    )
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_branches_identity_immutable")
        db.execute("DROP TRIGGER workbench_branches_task3_transition")
        db.execute(
            "UPDATE workbench_branches SET forked_from_sequence=? WHERE task_id='task-1' AND branch_id='child'",
            (after.sequence,),
        )
    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "branch topology" in str(verification.reason)
    with pytest.raises(LedgerCorruption, match="branch topology"):
        await Projector(store.registry).rebuild(store, "task-1")


@pytest.mark.asyncio
async def test_projector_rejects_unwitnessed_or_mislabeled_visible_event_tuple(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings)
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    with pytest.raises(LedgerCorruption, match="replay plan"):
        Projector(store.registry).replay(
            (created,), task_id="task-1", branch_id="sibling-does-not-exist"
        )


@pytest.mark.asyncio
async def test_snapshot_verification_and_visible_read_share_one_database_snapshot(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    event = await store.append(
        "task-1",
        "cmd-value",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "verified"}),
    )
    verified = asyncio.Event()
    release_read = asyncio.Event()
    original_verify = store._verify_ledger_db

    async def pause_after_verify(db: object, task_id: str, *, include_projections: bool):
        result = await original_verify(db, task_id, include_projections=include_projections)  # type: ignore[arg-type]
        verified.set()
        await release_read.wait()
        return result

    store._verify_ledger_db = pause_after_verify  # type: ignore[method-assign]
    snapshot_task = asyncio.create_task(store.snapshot("task-1"))
    await asyncio.wait_for(verified.wait(), timeout=5)

    def corrupt_after_verification() -> None:
        with sqlite3.connect(settings.state_path, timeout=5) as db:
            db.execute("DROP TRIGGER workbench_events_no_update")
            db.execute(
                "UPDATE workbench_events SET payload_json=? WHERE event_id=?",
                ('{"value":"corrupted-after-verification"}', event.event_id),
            )

    writer = asyncio.create_task(asyncio.to_thread(corrupt_after_verification))
    await asyncio.sleep(0.05)
    release_read.set()
    snapshot = await asyncio.wait_for(snapshot_task, timeout=5)
    await asyncio.wait_for(writer, timeout=5)

    assert snapshot.state["value"] == "verified"
    store._verify_ledger_db = original_verify  # type: ignore[method-assign]
    assert (await store.verify_ledger("task-1")).valid is False


@pytest.mark.asyncio
async def test_snapshot_verify_false_still_rejects_corrupt_authority_chain(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    event = await store.append(
        "task-1",
        "cmd-value",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "verified"}),
    )
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_events_no_update")
        db.execute(
            "UPDATE workbench_events SET payload_json=? WHERE event_id=?",
            ('{"value":"tampered"}', event.event_id),
        )
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1", verify=False)


@pytest.mark.asyncio
async def test_projector_rejects_bare_events_relabelled_as_a_real_sibling(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    left_fork = await store.fork_branch(
        "task-1", "cmd-left", "main", "left", created.sequence, actor
    )
    right_fork = await store.fork_branch(
        "task-1", "cmd-right", "main", "right", created.sequence, actor
    )
    left = await store.append(
        "task-1",
        "cmd-left-value",
        EventDraft(
            event_type="fixture.value",
            actor=actor,
            branch_id="left",
            payload={"value": "left-only"},
        ),
    )
    with pytest.raises(LedgerCorruption, match="replay plan"):
        Projector(store.registry).replay(
            (created, left), task_id="task-1", branch_id="right"
        )
    with pytest.raises(LedgerCorruption, match="topology visibility witness"):
        store._replay(
            store.projector,
            (created, left),
            task_id="task-1",
            branch_id="right",
        )
    fabricated = ReplayPlan(
        task_id="task-1",
        target_branch_id="right",
        at_sequence=None,
        topology=(
            BranchTopology("main", None, created.sequence, None, None, None),
            BranchTopology(
                "left", "main", created.sequence, created.sequence, 0, left_fork
            ),
            BranchTopology(
                "right", "left", left.sequence, created.sequence, 0, right_fork
            ),
        ),
        visible_events=(created, left),
    )
    with sqlite3.connect(settings.state_path) as db:
        projection_before = db.execute("SELECT * FROM projection_metadata ORDER BY branch_id").fetchall()
    with pytest.raises(LedgerCorruption, match="fork transition"):
        Projector(store.registry).replay(
            fabricated, task_id="task-1", branch_id="right"
        )
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT * FROM projection_metadata ORDER BY branch_id").fetchall() == projection_before


@pytest.mark.asyncio
async def test_rebuild_rejects_projector_with_a_different_registry_instance(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    authoritative = fixture_registry()
    store = WorkbenchStore(settings, registry=authoritative)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.append(
        "task-1",
        "cmd-value",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "good"}),
    )
    hostile = EventRegistry.production()

    def evil_reducer(state: dict[str, object], event: object) -> dict[str, object]:
        return {"value": "EVIL"}

    hostile.register(
        EventDefinition(
            event_type="fixture.value",
            event_schema_version=1,
            payload_model=ValuePayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=evil_reducer,
            authority_participant="core",
        )
    )
    with pytest.raises(LedgerCorruption, match="registry"):
        await Projector(hostile).rebuild(store, "task-1")
    assert (await store.snapshot("task-1")).state["value"] == "good"


@pytest.mark.asyncio
async def test_reducer_cannot_mutate_nested_event_payload_during_any_replay_path(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    mutation_enabled = {"value": True}

    def mutating_reducer(state: dict[str, object], event: object) -> dict[str, object]:
        payload = getattr(event, "payload")
        if mutation_enabled["value"]:
            payload["nested"]["value"] = "MUTATED"
        result = dict(state)
        result["nested"] = payload["nested"]["value"]
        return result

    registry.register(
        EventDefinition(
            event_type="fixture.nested",
            event_schema_version=1,
            payload_model=NestedPayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=mutating_reducer,
            authority_participant="core",
        )
    )
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    nested = EventDraft(
        event_type="fixture.nested",
        actor=actor,
        payload={"nested": {"value": "original"}},
    )
    with pytest.raises(LedgerCorruption, match="mutated event payload"):
        await store.append("task-1", "cmd-single-mutate", nested)
    with pytest.raises(LedgerCorruption, match="mutated event payload"):
        await store.append_batch(
            "task-1",
            "cmd-batch-mutate",
            (
                EventDraft(event_type="fixture.value", actor=actor, payload={"value": "before"}),
                nested,
            ),
        )
    mutation_enabled["value"] = False
    stored = await store.append("task-1", "cmd-valid-nested", nested)
    assert stored.payload == {"nested": {"value": "original"}}
    mutation_enabled["value"] = True
    with pytest.raises(LedgerCorruption, match="mutated event payload"):
        await store.snapshot("task-1")
    with pytest.raises(LedgerCorruption, match="mutated event payload"):
        await Projector(registry).rebuild(store, "task-1")
    with sqlite3.connect(settings.state_path) as db:
        assert json.loads(
            db.execute(
                "SELECT payload_json FROM workbench_events WHERE command_id='cmd-valid-nested'"
            ).fetchone()[0]
        ) == {"nested": {"value": "original"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted_ordinals", [(3,), (2,), (1, 2, 3)])
async def test_deleted_tail_middle_or_whole_command_is_never_rebuilt_or_retried(
    tmp_path: Path, deleted_ordinals: tuple[int, ...]
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    drafts = tuple(
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": value})
        for value in ("A", "B", "C")
    )
    await store.append_batch("task-1", "cmd-three", drafts)
    with sqlite3.connect(settings.state_path) as db:
        projection_before = db.execute("SELECT * FROM projection_metadata").fetchall()
        db.execute("DROP TRIGGER workbench_events_no_delete")
        placeholders = ",".join("?" for _ in deleted_ordinals)
        db.execute(
            f"DELETE FROM workbench_events WHERE task_id='task-1' AND command_id='cmd-three' "
            f"AND command_sequence IN ({placeholders})",
            deleted_ordinals,
        )
    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    with pytest.raises((LedgerCorruption, IdempotencyConflict, EventValidationError)):
        await store.append_batch(
            "task-1", "cmd-three", tuple(draft for index, draft in enumerate(drafts, 1) if index not in deleted_ordinals)
        )
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1")
    with pytest.raises(LedgerCorruption):
        await Projector(registry).rebuild(store, "task-1")
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT * FROM projection_metadata").fetchall() == projection_before


@pytest.mark.asyncio
async def test_orphan_event_and_manifest_tamper_fail_before_projection(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.append(
        "task-1", "cmd-value",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"}),
    )
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_command_manifests_no_delete")
        db.execute(
            "DELETE FROM workbench_command_manifests WHERE task_id='task-1' AND command_id='cmd-value'"
        )
    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "manifest" in str(verification.reason)

    other = settings_for(tmp_path / "other")
    await StateStore(other).initialize()
    other_store = WorkbenchStore(other, registry=fixture_registry())
    await other_store.create_task("task-2", "Build it", "cmd-create", actor)
    await other_store.append(
        "task-2", "cmd-value",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"}),
    )
    with sqlite3.connect(other.state_path) as db:
        db.execute("DROP TRIGGER workbench_command_manifests_no_update")
        db.execute(
            "UPDATE workbench_command_manifests SET drafts_checksum=? "
            "WHERE task_id='task-2' AND command_id='cmd-value'",
            ("0" * 64,),
        )
    tampered = await other_store.verify_ledger("task-2")
    assert tampered.valid is False
    assert "manifest checksum" in str(tampered.reason)


@pytest.mark.asyncio
async def test_missing_interior_event_fault_rolls_back_manifest_events_and_projection(tmp_path: Path) -> None:
    class OmitInteriorStore(WorkbenchStore):
        async def _insert_event(self, db: object, event: object) -> None:
            if getattr(event, "command_id") == "cmd-three" and getattr(event, "command_sequence") == 2:
                return
            await super()._insert_event(db, event)  # type: ignore[arg-type]

    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = OmitInteriorStore(settings, registry=fixture_registry())
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with sqlite3.connect(settings.state_path) as db:
        projection_before = db.execute("SELECT * FROM projection_metadata").fetchall()
    drafts = tuple(
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": value})
        for value in ("A", "B", "C")
    )
    with pytest.raises(LedgerCorruption):
        await store.append_batch("task-1", "cmd-three", drafts)
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM workbench_events WHERE command_id='cmd-three'"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT COUNT(*) FROM workbench_command_manifests WHERE command_id='cmd-three'"
        ).fetchone() == (0,)
        assert db.execute("SELECT * FROM projection_metadata").fetchall() == projection_before


@pytest.mark.asyncio
async def test_coherently_rehashed_frame_tamper_fails_registry_transition_verification(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry(effect=FrameEffect.CONFIRM)
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    event = await store.append(
        "task-1", "cmd-confirm",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "confirmed"}),
        expected_frame_version=0,
    )
    with sqlite3.connect(settings.state_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("DROP TRIGGER workbench_events_no_update")
        row = db.execute("SELECT * FROM workbench_events WHERE event_id=?", (event.event_id,)).fetchone()
        assert row is not None
        checksum = event_checksum(sql_event_envelope(row, frame_version=7))
        db.execute(
            "UPDATE workbench_events SET frame_version=7,checksum=? WHERE event_id=?",
            (checksum, event.event_id),
        )
    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "frame transition" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await Projector(registry).rebuild(store, "task-1")


@pytest.mark.asyncio
async def test_coherently_rehashed_post_fork_parent_cause_still_fails_visibility(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    store = WorkbenchStore(settings, registry=registry)
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.fork_branch("task-1", "cmd-fork", "main", "child", created.sequence, actor)
    parent_after = await store.append(
        "task-1", "cmd-parent-after",
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "parent"}),
    )
    child = await store.append(
        "task-1", "cmd-child",
        EventDraft(
            event_type="fixture.value", actor=actor, branch_id="child", payload={"value": "child"}
        ),
    )
    with sqlite3.connect(settings.state_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("DROP TRIGGER workbench_events_no_update")
        db.execute("DROP TRIGGER workbench_command_manifests_no_update")
        event_row = db.execute(
            "SELECT * FROM workbench_events WHERE event_id=?", (child.event_id,)
        ).fetchone()
        assert event_row is not None
        changed_event_checksum = event_checksum(
            sql_event_envelope(event_row, caused_by=parent_after.event_id)
        )
        db.execute(
            "UPDATE workbench_events SET caused_by=?,checksum=? WHERE event_id=?",
            (parent_after.event_id, changed_event_checksum, child.event_id),
        )
        changed_draft = registry.validate(
            EventDraft(
                event_type="fixture.value",
                actor=actor,
                branch_id="child",
                caused_by=parent_after.event_id,
                payload={"value": "child"},
            )
        )
        changed_drafts_checksum = drafts_checksum("task-1", "cmd-child", (changed_draft,))
        manifest_row = db.execute(
            "SELECT * FROM workbench_command_manifests WHERE task_id='task-1' AND command_id='cmd-child'"
        ).fetchone()
        assert manifest_row is not None
        fields = sql_manifest_fields(manifest_row, drafts_checksum=changed_drafts_checksum)
        db.execute(
            "UPDATE workbench_command_manifests SET drafts_checksum=?,manifest_checksum=? "
            "WHERE task_id='task-1' AND command_id='cmd-child'",
            (changed_drafts_checksum, manifest_checksum(fields)),
        )
    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "cause is not visible" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1", "child")


@pytest.mark.asyncio
async def test_coherently_rehashed_intra_command_cause_is_not_visible_at_atomic_boundary(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    drafts = (
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"}),
        EventDraft(event_type="fixture.value", actor=actor, payload={"value": "B"}),
    )
    events = await store.append_batch("task-1", "cmd-batch", drafts)
    with sqlite3.connect(settings.state_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("DROP TRIGGER workbench_events_no_update")
        db.execute("DROP TRIGGER workbench_command_manifests_no_update")
        second_row = db.execute(
            "SELECT * FROM workbench_events WHERE event_id=?", (events[1].event_id,)
        ).fetchone()
        assert second_row is not None
        changed_event_checksum = event_checksum(
            sql_event_envelope(second_row, caused_by=events[0].event_id)
        )
        db.execute(
            "UPDATE workbench_events SET caused_by=?,checksum=? WHERE event_id=?",
            (events[0].event_id, changed_event_checksum, events[1].event_id),
        )
        changed = (
            registry.validate(drafts[0]),
            registry.validate(
                EventDraft(
                    event_type="fixture.value",
                    actor=actor,
                    caused_by=events[0].event_id,
                    payload={"value": "B"},
                )
            ),
        )
        changed_drafts_checksum = drafts_checksum("task-1", "cmd-batch", changed)
        manifest_row = db.execute(
            "SELECT * FROM workbench_command_manifests "
            "WHERE task_id='task-1' AND command_id='cmd-batch'"
        ).fetchone()
        assert manifest_row is not None
        fields = sql_manifest_fields(manifest_row, drafts_checksum=changed_drafts_checksum)
        db.execute(
            "UPDATE workbench_command_manifests SET drafts_checksum=?,manifest_checksum=? "
            "WHERE task_id='task-1' AND command_id='cmd-batch'",
            (changed_drafts_checksum, manifest_checksum(fields)),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "cause is not visible" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await store.append_batch("task-1", "cmd-batch", drafts)
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1")
    with pytest.raises(LedgerCorruption):
        await Projector(registry).rebuild(store, "task-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "fractional_value"),
    (("event_schema_version", 1.5), ("command_sequence", 1.5), ("frame_version", 0.5)),
)
async def test_fractional_event_integer_fields_fail_every_authority_surface(
    tmp_path: Path, column: str, fractional_value: float
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = fixture_registry()
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    draft = EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"})
    event = await store.append("task-1", "cmd-value", draft)
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_events_no_update")
        db.execute(
            f"UPDATE workbench_events SET {column}=? WHERE event_id=?",
            (fractional_value, event.event_id),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "integer storage" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await store.append("task-1", "cmd-value", draft)
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1")
    with pytest.raises(LedgerCorruption):
        await Projector(registry).rebuild(store, "task-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_value", ("1", True))
async def test_coercible_but_non_normalized_payload_fails_every_authority_surface(
    tmp_path: Path, changed_value: object
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    registry = integer_fixture_registry()
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    draft = EventDraft(event_type="fixture.integer", actor=actor, payload={"value": 1})
    event = await store.append("task-1", "cmd-value", draft)
    with sqlite3.connect(settings.state_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("DROP TRIGGER workbench_events_no_update")
        row = db.execute(
            "SELECT * FROM workbench_events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        changed_payload = {"value": changed_value}
        changed_checksum = event_checksum(sql_event_envelope(row, payload=changed_payload))
        db.execute(
            "UPDATE workbench_events SET payload_json=?,checksum=? WHERE event_id=?",
            (canonical_json_bytes(changed_payload).decode("utf-8"), changed_checksum, event.event_id),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "registry-normalized payload" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await store.append("task-1", "cmd-value", draft)
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1", verify=False)
    with pytest.raises(LedgerCorruption):
        await Projector(registry).rebuild(store, "task-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ("missing_task", "missing_root_branch", "missing_manifest", "missing_projection"),
)
async def test_create_task_retry_never_returns_from_incomplete_authority(
    tmp_path: Path, corruption: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with sqlite3.connect(settings.state_path) as db:
        if corruption == "missing_task":
            db.execute("DELETE FROM workbench_tasks WHERE id='task-1'")
        elif corruption == "missing_root_branch":
            db.execute("DROP TRIGGER workbench_branches_no_delete")
            db.execute("DELETE FROM workbench_branches WHERE task_id='task-1'")
        elif corruption == "missing_manifest":
            db.execute("DROP TRIGGER workbench_command_manifests_no_delete")
            db.execute("DELETE FROM workbench_command_manifests WHERE task_id='task-1'")
        else:
            db.execute("DELETE FROM projection_metadata WHERE task_id='task-1'")

    with pytest.raises(LedgerCorruption):
        await store.create_task("task-1", "Build it", "cmd-create", actor)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "fractional_value"),
    (("forked_from_sequence", 1.5), ("forked_from_frame_version", 0.5)),
)
async def test_fractional_branch_topology_fields_fail_every_authority_surface(
    tmp_path: Path, column: str, fractional_value: float
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings)
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.fork_branch(
        "task-1", "cmd-fork", "main", "child", created.sequence, actor
    )
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_branches_identity_immutable")
        db.execute("DROP TRIGGER workbench_branches_task3_transition")
        db.execute(
            f"UPDATE workbench_branches SET {column}=? "
            "WHERE task_id='task-1' AND branch_id='child'",
            (fractional_value,),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "integer storage" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await store.fork_branch(
            "task-1", "cmd-fork", "main", "child", created.sequence, actor
        )
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1", "child")
    with pytest.raises(LedgerCorruption):
        await Projector(store.registry).rebuild(store, "task-1")


@pytest.mark.asyncio
async def test_blob_text_alias_fails_every_authority_surface(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="b'matt'")
    registry = fixture_registry()
    store = WorkbenchStore(settings, registry=registry)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    draft = EventDraft(event_type="fixture.value", actor=actor, payload={"value": "A"})
    event = await store.append("task-1", "cmd-value", draft)
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_events_no_update")
        db.execute(
            "UPDATE workbench_events SET actor_id=? WHERE event_id=?",
            (sqlite3.Binary(b"matt"), event.event_id),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "text storage" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await store.append("task-1", "cmd-value", draft)
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1")
    with pytest.raises(LedgerCorruption):
        await Projector(registry).rebuild(store, "task-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "changed_value"),
    (
        ("title", "Changed"),
        ("state", "ready"),
        ("active_branch_id", "ghost"),
        ("current_frame_version", 1),
        ("created_at", "2026-07-14T00:00:00.000000Z"),
        ("updated_at", "2026-07-14T00:00:00.000000Z"),
    ),
)
async def test_task_row_semantics_remain_bound_to_task_created(
    tmp_path: Path, column: str, changed_value: object
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings)
    await store.create_task("task-1", "Build it", "cmd-create", actor)
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_tasks_task3_transition")
        db.execute(
            f"UPDATE workbench_tasks SET {column}=? WHERE id='task-1'", (changed_value,)
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    assert "bound to task.created" in str(verification.reason)
    with pytest.raises(LedgerCorruption):
        await store.create_task("task-1", "Build it", "cmd-create", actor)
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1")
    with pytest.raises(LedgerCorruption):
        await Projector(store.registry).rebuild(store, "task-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("branch_id", "column", "changed_value"),
    (
        ("main", "status", "merged"),
        ("main", "created_at", "2026-07-14T00:00:00.000000Z"),
        ("child", "status", "merged"),
        ("child", "created_at", "2026-07-14T00:00:00.000000Z"),
    ),
)
async def test_branch_state_and_time_remain_bound_to_creation_event(
    tmp_path: Path, branch_id: str, column: str, changed_value: str
) -> None:
    settings = settings_for(tmp_path)
    await StateStore(settings).initialize()
    actor = EventActor(kind="owner", actor_id="matt")
    store = WorkbenchStore(settings)
    created = await store.create_task("task-1", "Build it", "cmd-create", actor)
    await store.fork_branch(
        "task-1", "cmd-fork", "main", "child", created.sequence, actor
    )
    with sqlite3.connect(settings.state_path) as db:
        db.execute("DROP TRIGGER workbench_branches_task3_transition")
        if column == "created_at":
            db.execute("DROP TRIGGER workbench_branches_identity_immutable")
        db.execute(
            f"UPDATE workbench_branches SET {column}=? "
            "WHERE task_id='task-1' AND branch_id=?",
            (changed_value, branch_id),
        )

    verification = await store.verify_ledger("task-1")
    assert verification.valid is False
    with pytest.raises(LedgerCorruption):
        await store.create_task("task-1", "Build it", "cmd-create", actor)
    with pytest.raises(LedgerCorruption):
        await store.snapshot("task-1", branch_id)
    with pytest.raises(LedgerCorruption):
        await Projector(store.registry).rebuild(store, "task-1")
