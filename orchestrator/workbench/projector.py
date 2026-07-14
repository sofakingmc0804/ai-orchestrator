from __future__ import annotations

import hashlib
from typing import Iterable, TYPE_CHECKING

from orchestrator.workbench.events import EventRegistry, GENESIS_CHECKSUM, canonical_json_bytes
from orchestrator.workbench.models import (
    EventDraft,
    LedgerCorruption,
    ProjectionHead,
    WorkbenchEvent,
    WorkbenchSnapshot,
)

if TYPE_CHECKING:
    from orchestrator.workbench.store import WorkbenchStore


PROJECTION_NAME = "workbench.canonical.v1"
GENESIS_TIME = "1970-01-01T00:00:00.000000Z"


class Projector:
    def __init__(self, registry: EventRegistry | None = None) -> None:
        self.registry = registry or EventRegistry.production()

    def replay(
        self,
        visible_events: Iterable[WorkbenchEvent],
        *,
        task_id: str,
        branch_id: str,
        at_sequence: int | None = None,
    ) -> WorkbenchSnapshot:
        events = tuple(visible_events)
        previous = 0
        state: dict[str, object] = {}
        frame_version = 0
        for event in events:
            if event.task_id != task_id:
                raise LedgerCorruption(event.sequence, "projector received an event from another task")
            if event.sequence <= previous:
                raise LedgerCorruption(event.sequence, "visible events are not strictly ordered")
            if at_sequence is not None and event.sequence > at_sequence:
                raise LedgerCorruption(event.sequence, "visible event exceeds the requested boundary")
            validated = self.registry.validate(
                EventDraft(
                    event_type=event.event_type,
                    event_schema_version=event.event_schema_version,
                    actor=event.actor,
                    branch_id=event.branch_id,
                    cause=event.cause,
                    caused_by=event.caused_by,
                    payload=event.payload,
                )
            )
            state = validated.definition.reducer(state, event)
            if not isinstance(state, dict):
                raise LedgerCorruption(event.sequence, "event reducer did not return an object state")
            frame_version = event.frame_version
            previous = event.sequence

        state = dict(state)
        state["projection"] = {
            "task_id": task_id,
            "branch_id": branch_id,
            "frame_version": frame_version,
        }
        state_checksum = hashlib.sha256(canonical_json_bytes(state)).hexdigest()
        head_event = events[-1] if events else None
        head = ProjectionHead(
            task_id=task_id,
            projection_name=PROJECTION_NAME,
            branch_id=branch_id,
            head_sequence=head_event.sequence if head_event else 0,
            head_event_checksum=head_event.checksum if head_event else GENESIS_CHECKSUM,
            state_checksum=state_checksum,
        )
        return WorkbenchSnapshot(
            task_id=task_id,
            branch_id=branch_id,
            at_sequence=at_sequence,
            frame_version=frame_version,
            state=state,
            head=head,
        )

    async def rebuild(self, store: "WorkbenchStore", task_id: str) -> tuple[ProjectionHead, ...]:
        return await store._rebuild_projections(self, task_id)
