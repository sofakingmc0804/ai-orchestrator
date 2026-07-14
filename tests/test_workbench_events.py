from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.workbench.events import (
    GENESIS_CHECKSUM,
    canonical_json_bytes,
    event_checksum,
    normalize_timestamp,
)
from orchestrator.workbench.models import EventActor, EventDraft


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
        idempotency_key="idem-1",
    )
    assert draft.event_schema_version == 1
    with pytest.raises(Exception):
        draft.event_type = "changed"  # type: ignore[misc]
    with pytest.raises(Exception):
        EventDraft(
            event_type="task.created",
            actor=EventActor(kind="owner", actor_id="matt"),
            payload={"title": "Build it"},
            idempotency_key="idem-2",
            unknown=True,  # type: ignore[call-arg]
        )
