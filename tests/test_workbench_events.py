from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
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
    event_checksum,
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
from orchestrator.workbench.projector import Projector
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
