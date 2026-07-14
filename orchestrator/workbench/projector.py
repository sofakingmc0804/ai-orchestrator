from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, TYPE_CHECKING

from orchestrator.workbench.events import (
    EventRegistry,
    GENESIS_CHECKSUM,
    canonical_json_bytes,
    event_checksum,
)
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


@dataclass(frozen=True)
class BranchTopology:
    branch_id: str
    parent_branch_id: str | None
    visible_through_sequence: int
    forked_from_sequence: int | None
    forked_from_frame_version: int | None
    creation_event: WorkbenchEvent | None


@dataclass(frozen=True)
class ReplayPlan:
    task_id: str
    target_branch_id: str
    at_sequence: int | None
    topology: tuple[BranchTopology, ...]
    visible_events: tuple[WorkbenchEvent, ...]


class Projector:
    def __init__(self, registry: EventRegistry | None = None) -> None:
        self.registry = registry or EventRegistry.production()

    def replay(
        self,
        visible_events: ReplayPlan,
        *,
        task_id: str,
        branch_id: str,
        at_sequence: int | None = None,
    ) -> WorkbenchSnapshot:
        if not isinstance(visible_events, ReplayPlan):
            raise LedgerCorruption(None, "projector requires an explicit replay plan")
        plan = visible_events
        if (
            plan.task_id != task_id
            or plan.target_branch_id != branch_id
            or plan.at_sequence != at_sequence
        ):
            raise LedgerCorruption(None, "replay plan identity does not match the requested snapshot")
        events = plan.visible_events
        topology = plan.topology
        if (
            not topology
            or topology[-1].branch_id != branch_id
            or len({item.branch_id for item in topology}) != len(topology)
        ):
            raise LedgerCorruption(None, "replay plan has invalid branch lineage")
        root = topology[0]
        if (
            root.parent_branch_id is not None
            or root.forked_from_sequence is not None
            or root.forked_from_frame_version is not None
            or root.creation_event is not None
        ):
            raise LedgerCorruption(None, "replay plan root is not an unforked branch")
        for parent, child in zip(topology, topology[1:]):
            creation = child.creation_event
            boundary_event = next(
                (
                    event
                    for event in events
                    if event.sequence == child.forked_from_sequence
                ),
                None,
            )
            if (
                child.parent_branch_id != parent.branch_id
                or child.forked_from_sequence is None
                or child.forked_from_frame_version is None
                or creation is None
                or creation.task_id != task_id
                or creation.event_type != "branch.forked"
                or creation.branch_id != parent.branch_id
                or creation.payload
                != {
                    "new_branch_id": child.branch_id,
                    "parent_branch_id": parent.branch_id,
                    "forked_from_sequence": child.forked_from_sequence,
                    "forked_from_frame_version": child.forked_from_frame_version,
                }
                or creation.sequence <= child.forked_from_sequence
                or boundary_event is None
                or boundary_event.frame_version != child.forked_from_frame_version
                or self._computed_checksum(creation) != creation.checksum
            ):
                raise LedgerCorruption(None, "replay plan fork transition is invalid")
            if parent.visible_through_sequence != child.forked_from_sequence:
                raise LedgerCorruption(None, "replay plan parent cutoff does not match child fork")
        cutoffs = {item.branch_id: item.visible_through_sequence for item in topology}
        branch_order = {item.branch_id: index for index, item in enumerate(topology)}
        previous_branch_index = 0
        for event in events:
            cutoff = cutoffs.get(event.branch_id)
            if cutoff is None or event.sequence > cutoff:
                raise LedgerCorruption(
                    event.sequence,
                    "visible event lies outside the witnessed branch ancestry cutoff",
                )
            branch_index = branch_order[event.branch_id]
            if branch_index < previous_branch_index:
                raise LedgerCorruption(event.sequence, "replay plan branch events are out of lineage order")
            previous_branch_index = branch_index
            if self._computed_checksum(event) != event.checksum:
                raise LedgerCorruption(event.sequence, "replay plan event checksum is invalid")
        if not events:
            raise LedgerCorruption(None, "replay plan does not contain task.created")
        created = events[0]
        if (
            created.sequence != 1
            or created.event_type != "task.created"
            or created.branch_id != root.branch_id
            or created.payload.get("initial_branch_id") != root.branch_id
            or sum(event.event_type == "task.created" for event in events) != 1
        ):
            raise LedgerCorruption(created.sequence, "replay plan is not bound to the task root")
        if at_sequence is not None and topology[-1].visible_through_sequence != at_sequence:
            raise LedgerCorruption(None, "replay plan target cutoff does not match requested boundary")
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
            reducer_event = event.model_copy(deep=True)
            payload_before = canonical_json_bytes(reducer_event.payload)
            state = validated.definition.reducer(state, reducer_event)
            if canonical_json_bytes(reducer_event.payload) != payload_before:
                raise LedgerCorruption(event.sequence, "event reducer mutated event payload")
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

    @staticmethod
    def _computed_checksum(event: WorkbenchEvent) -> str:
        return event_checksum(
            {
                "event_schema_version": event.event_schema_version,
                "event_id": event.event_id,
                "task_id": event.task_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "actor_kind": event.actor.kind,
                "actor_id": event.actor.actor_id,
                "branch_id": event.branch_id,
                "cause": event.cause.value if event.cause is not None else None,
                "caused_by": event.caused_by,
                "command_id": event.command_id,
                "command_sequence": event.command_sequence,
                "frame_version": event.frame_version,
                "payload": event.payload,
                "idempotency_key": event.idempotency_key,
                "created_at": event.created_at,
                "prior_checksum": event.prior_checksum,
            }
        )

    async def rebuild(self, store: "WorkbenchStore", task_id: str) -> tuple[ProjectionHead, ...]:
        if self.registry is not store.registry:
            raise LedgerCorruption(None, "projection rebuild registry is not the store authority")
        return await store._rebuild_projections(self, task_id)
