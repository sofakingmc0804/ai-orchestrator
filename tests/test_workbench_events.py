from __future__ import annotations

import hashlib
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
    IdempotencyConflict,
    StaleFrameVersion,
    UnsupportedEventType,
)
from orchestrator.workbench.store import WorkbenchStore
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
