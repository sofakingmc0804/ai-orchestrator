from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Sequence

import aiosqlite

from orchestrator.config import Settings
from orchestrator.workbench.events import (
    EventRegistry,
    FrameEffect,
    GENESIS_CHECKSUM,
    ValidatedDraft,
    canonical_json_text,
    event_checksum,
    normalize_timestamp,
)
from orchestrator.workbench.models import (
    BranchNotFound,
    EventActor,
    EventCause,
    EventDraft,
    EventValidationError,
    IdempotencyConflict,
    InvalidCause,
    LedgerCorruption,
    LedgerVerification,
    StaleFrameVersion,
    TaskNotFound,
    WorkbenchEvent,
    WorkbenchSnapshot,
)
from orchestrator.workbench.projector import GENESIS_TIME, PROJECTION_NAME, Projector


BUSY_TIMEOUT_MS = 5_000


class WorkbenchStore:
    def __init__(
        self,
        source: Settings | Path | str,
        *,
        registry: EventRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(source.state_path if isinstance(source, Settings) else source)
        self.registry = registry or EventRegistry.production()
        self.projector = Projector(self.registry)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: f"evt_{uuid.uuid4().hex}")

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path, isolation_level=None)
        db.row_factory = aiosqlite.Row
        try:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            yield db
        finally:
            await db.close()

    async def _finish_commit(self, db: aiosqlite.Connection) -> None:
        task = asyncio.create_task(db.commit())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            error: BaseException | None = None
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException as exc:
                    error = exc
                    break
            if error is None:
                try:
                    task.result()
                except BaseException as exc:
                    error = exc
            if error is not None:
                raise cancellation from error
            raise

    async def create_task(
        self,
        task_id: str,
        title: str,
        command_id: str,
        actor: EventActor,
    ) -> WorkbenchEvent:
        self._identity(task_id, "task_id")
        self._identity(command_id, "command_id")
        draft = EventDraft(
            event_type="task.created",
            actor=actor,
            branch_id="main",
            cause=EventCause.OWNER_REQUEST,
            payload={"title": title, "initial_branch_id": "main"},
        )
        validated = self.registry.validate(draft)
        async with self._connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                existing_task = await (
                    await db.execute("SELECT id FROM workbench_tasks WHERE id=?", (task_id,))
                ).fetchone()
                existing = await self._resolve_retry(
                    db, task_id, command_id, (validated,), expected_frame_version=None
                )
                if existing is not None:
                    await db.rollback()
                    return existing[0]
                if existing_task is not None:
                    raise IdempotencyConflict(f"task already exists under another command: {task_id}")

                created_at = normalize_timestamp(self._clock())
                event_id = self._new_event_id()
                event = self._build_event(
                    task_id=task_id,
                    command_id=command_id,
                    command_sequence=1,
                    sequence=1,
                    validated=validated,
                    frame_version=0,
                    prior_checksum=GENESIS_CHECKSUM,
                    event_id=event_id,
                    created_at=created_at,
                )
                await db.execute(
                    """
                    INSERT INTO workbench_tasks(
                        id,title,state,active_branch_id,current_frame_version,created_at,updated_at
                    ) VALUES (?,?,'intake','main',0,?,?)
                    """,
                    (task_id, title, created_at, created_at),
                )
                await db.execute(
                    """
                    INSERT INTO workbench_branches(
                        task_id,branch_id,parent_branch_id,forked_from_sequence,
                        forked_from_frame_version,created_by_event_id,status,created_at
                    ) VALUES (?,'main',NULL,NULL,NULL,NULL,'active',?)
                    """,
                    (task_id, created_at),
                )
                await self._insert_event(db, event)
                snapshot = self.projector.replay((event,), task_id=task_id, branch_id="main")
                await self._publish_snapshot(db, snapshot, event.created_at)
                await self._finish_commit(db)
                return event
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise

    async def append(
        self,
        task_id: str,
        command_id: str,
        draft: EventDraft,
        *,
        expected_frame_version: int | None = None,
    ) -> WorkbenchEvent:
        events = await self.append_batch(
            task_id,
            command_id,
            (draft,),
            expected_frame_version=expected_frame_version,
        )
        return events[0]

    async def append_batch(
        self,
        task_id: str,
        command_id: str,
        drafts: Sequence[EventDraft],
        *,
        expected_frame_version: int | None = None,
    ) -> tuple[WorkbenchEvent, ...]:
        self._identity(task_id, "task_id")
        self._identity(command_id, "command_id")
        if not drafts:
            raise EventValidationError("append_batch requires at least one draft")
        branches = {draft.branch_id for draft in drafts}
        if len(branches) != 1:
            raise EventValidationError("append_batch requires one target branch")
        validated = tuple(self.registry.validate(draft) for draft in drafts)
        if any(item.draft.event_type in {"task.created", "branch.forked"} for item in validated):
            raise EventValidationError("task and branch topology events require their dedicated APIs")
        confirms = sum(item.definition.frame_effect is FrameEffect.CONFIRM for item in validated)
        if confirms > 1:
            raise EventValidationError("Task 2 permits at most one confirmed frame event per command")

        async with self._connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._require_task(db, task_id)
                verification = await self._verify_ledger_db(db, task_id, include_projections=True)
                self._require_valid(verification)
                existing = await self._resolve_retry(
                    db,
                    task_id,
                    command_id,
                    validated,
                    expected_frame_version=expected_frame_version,
                )
                if existing is not None:
                    await db.rollback()
                    return existing

                branch_id = next(iter(branches))
                await self._require_branch(db, task_id, branch_id)
                visible = await self._visible_events(db, task_id, branch_id, at_sequence=None)
                snapshot = self.projector.replay(visible, task_id=task_id, branch_id=branch_id)
                current_frame = snapshot.frame_version
                if confirms:
                    if expected_frame_version is None or expected_frame_version != current_frame:
                        raise StaleFrameVersion(
                            f"confirmed frame command expected {expected_frame_version}, current is {current_frame}"
                        )
                elif expected_frame_version is not None and expected_frame_version != current_frame:
                    raise StaleFrameVersion(
                        f"command expected frame {expected_frame_version}, current is {current_frame}"
                    )
                visible_ids = {event.event_id for event in visible}
                for item in validated:
                    if item.draft.caused_by is not None and item.draft.caused_by not in visible_ids:
                        raise InvalidCause(
                            f"caused_by is not visible in branch {branch_id}: {item.draft.caused_by}"
                        )

                last = await self._global_head(db, task_id)
                sequence = last[0]
                prior_checksum = last[1]
                frame_version = current_frame
                events: list[WorkbenchEvent] = []
                for ordinal, item in enumerate(validated, start=1):
                    if item.definition.frame_effect is FrameEffect.CONFIRM:
                        frame_version += 1
                    sequence += 1
                    event = self._build_event(
                        task_id=task_id,
                        command_id=command_id,
                        command_sequence=ordinal,
                        sequence=sequence,
                        validated=item,
                        frame_version=frame_version,
                        prior_checksum=prior_checksum,
                        event_id=self._new_event_id(),
                        created_at=normalize_timestamp(self._clock()),
                    )
                    events.append(event)
                    prior_checksum = event.checksum

                projected = self.projector.replay(
                    (*visible, *events), task_id=task_id, branch_id=branch_id
                )
                for event in events:
                    await self._insert_event(db, event)
                await self._publish_snapshot(db, projected, events[-1].created_at)
                await self._finish_commit(db)
                return tuple(events)
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise

    async def fork_branch(
        self,
        task_id: str,
        command_id: str,
        parent_branch_id: str,
        branch_id: str,
        at_sequence: int,
        actor: EventActor,
    ) -> WorkbenchEvent:
        self._identity(task_id, "task_id")
        self._identity(command_id, "command_id")
        self._identity(branch_id, "branch_id")
        if at_sequence < 1:
            raise EventValidationError("fork boundary must include task.created")
        async with self._connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._require_task(db, task_id)
                verification = await self._verify_ledger_db(db, task_id, include_projections=True)
                self._require_valid(verification)
                await self._require_branch(db, task_id, parent_branch_id)
                boundary_events = await self._visible_events(
                    db, task_id, parent_branch_id, at_sequence=at_sequence
                )
                if not boundary_events or boundary_events[-1].sequence != at_sequence:
                    raise EventValidationError("fork boundary is not an exact visible parent event")
                if boundary_events[0].event_type != "task.created":
                    raise EventValidationError("fork boundary does not include task.created")
                fork_frame = boundary_events[-1].frame_version
                draft = EventDraft(
                    event_type="branch.forked",
                    actor=actor,
                    branch_id=parent_branch_id,
                    cause=EventCause.OWNER_REQUEST,
                    payload={
                        "new_branch_id": branch_id,
                        "parent_branch_id": parent_branch_id,
                        "forked_from_sequence": at_sequence,
                        "forked_from_frame_version": fork_frame,
                    },
                )
                validated = self.registry.validate(draft)
                existing = await self._resolve_retry(
                    db, task_id, command_id, (validated,), expected_frame_version=None
                )
                if existing is not None:
                    branch = await (
                        await db.execute(
                            """
                            SELECT parent_branch_id,forked_from_sequence,forked_from_frame_version,
                                   created_by_event_id
                            FROM workbench_branches WHERE task_id=? AND branch_id=?
                            """,
                            (task_id, branch_id),
                        )
                    ).fetchone()
                    expected = (parent_branch_id, at_sequence, fork_frame, existing[0].event_id)
                    if branch is None or tuple(branch) != expected:
                        raise LedgerCorruption(existing[0].sequence, "fork command and branch topology disagree")
                    await db.rollback()
                    return existing[0]
                duplicate = await (
                    await db.execute(
                        "SELECT 1 FROM workbench_branches WHERE task_id=? AND branch_id=?",
                        (task_id, branch_id),
                    )
                ).fetchone()
                if duplicate is not None:
                    raise IdempotencyConflict(f"branch already exists: {branch_id}")

                parent_current = await self._visible_events(db, task_id, parent_branch_id, at_sequence=None)
                parent_snapshot = self.projector.replay(
                    parent_current, task_id=task_id, branch_id=parent_branch_id
                )
                head_sequence, head_checksum = await self._global_head(db, task_id)
                event = self._build_event(
                    task_id=task_id,
                    command_id=command_id,
                    command_sequence=1,
                    sequence=head_sequence + 1,
                    validated=validated,
                    frame_version=parent_snapshot.frame_version,
                    prior_checksum=head_checksum,
                    event_id=self._new_event_id(),
                    created_at=normalize_timestamp(self._clock()),
                )
                await self._insert_event(db, event)
                await db.execute(
                    """
                    INSERT INTO workbench_branches(
                        task_id,branch_id,parent_branch_id,forked_from_sequence,
                        forked_from_frame_version,created_by_event_id,status,created_at
                    ) VALUES (?,?,?,?,?,?,'active',?)
                    """,
                    (
                        task_id,
                        branch_id,
                        parent_branch_id,
                        at_sequence,
                        fork_frame,
                        event.event_id,
                        event.created_at,
                    ),
                )
                parent_projected = self.projector.replay(
                    (*parent_current, event), task_id=task_id, branch_id=parent_branch_id
                )
                child_projected = self.projector.replay(
                    boundary_events, task_id=task_id, branch_id=branch_id
                )
                await self._publish_snapshot(db, parent_projected, event.created_at)
                child_time = boundary_events[-1].created_at if boundary_events else GENESIS_TIME
                await self._publish_snapshot(db, child_projected, child_time)
                await self._finish_commit(db)
                return event
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise

    async def snapshot(
        self,
        task_id: str,
        branch_id: str = "main",
        at_sequence: int | None = None,
        verify: bool = True,
    ) -> WorkbenchSnapshot:
        async with self._connection() as db:
            await self._require_task(db, task_id)
            await self._require_branch(db, task_id, branch_id)
            if verify:
                verification = await self._verify_ledger_db(
                    db, task_id, include_projections=at_sequence is None
                )
                self._require_valid(verification)
            visible = await self._visible_events(db, task_id, branch_id, at_sequence=at_sequence)
            return self.projector.replay(
                visible, task_id=task_id, branch_id=branch_id, at_sequence=at_sequence
            )

    async def verify_ledger(self, task_id: str) -> LedgerVerification:
        async with self._connection() as db:
            await self._require_task(db, task_id)
            return await self._verify_ledger_db(db, task_id, include_projections=True)

    async def _rebuild_projections(
        self, projector: Projector, task_id: str
    ) -> tuple[Any, ...]:
        async with self._connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._require_task(db, task_id)
                verification = await self._verify_ledger_db(db, task_id, include_projections=False)
                self._require_valid(verification)
                before = await self._global_head(db, task_id)
                branches = await (
                    await db.execute(
                        "SELECT branch_id FROM workbench_branches WHERE task_id=? ORDER BY branch_id",
                        (task_id,),
                    )
                ).fetchall()
                heads = []
                for row in branches:
                    branch_id = str(row[0])
                    visible = await self._visible_events(db, task_id, branch_id, at_sequence=None)
                    snapshot = projector.replay(visible, task_id=task_id, branch_id=branch_id)
                    updated_at = visible[-1].created_at if visible else GENESIS_TIME
                    await self._publish_snapshot(db, snapshot, updated_at)
                    heads.append(snapshot.head)
                after = await self._global_head(db, task_id)
                if after != before:
                    raise LedgerCorruption(after[0], "ledger head changed during projection rebuild")
                await self._finish_commit(db)
                return tuple(heads)
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise

    async def _verify_ledger_db(
        self, db: aiosqlite.Connection, task_id: str, *, include_projections: bool
    ) -> LedgerVerification:
        rows = await (
            await db.execute(
                "SELECT * FROM workbench_events WHERE task_id=? ORDER BY sequence", (task_id,)
            )
        ).fetchall()
        prior = GENESIS_CHECKSUM
        expected_sequence = 1
        for row in rows:
            sequence = int(row["sequence"])
            try:
                if sequence != expected_sequence:
                    raise ValueError(f"expected contiguous sequence {expected_sequence}")
                if str(row["prior_checksum"]) != prior:
                    raise ValueError("prior checksum link does not match")
                payload = json.loads(str(row["payload_json"]))
                if str(row["payload_json"]) != canonical_json_text(payload):
                    raise ValueError("payload is not stored in canonical JSON form")
                if str(row["created_at"]) != normalize_timestamp(str(row["created_at"])):
                    raise ValueError("created_at is not normalized UTC")
                envelope = self._row_envelope(row, payload)
                computed = event_checksum(envelope)
                if str(row["checksum"]) != computed:
                    raise ValueError("stored checksum does not match immutable envelope")
                # Structural validation is also part of fail-closed verification.
                self.registry.validate(
                    EventDraft(
                        event_type=str(row["event_type"]),
                        event_schema_version=int(row["event_schema_version"]),
                        actor=EventActor(kind=str(row["actor_kind"]), actor_id=str(row["actor_id"])),
                        branch_id=str(row["branch_id"]),
                        cause=EventCause(str(row["cause"])) if row["cause"] is not None else None,
                        caused_by=str(row["caused_by"]) if row["caused_by"] is not None else None,
                        payload=payload,
                    )
                )
            except Exception as exc:
                return LedgerVerification(
                    valid=False,
                    task_id=task_id,
                    checked_events=expected_sequence - 1,
                    head_sequence=int(rows[-1]["sequence"]) if rows else 0,
                    head_checksum=str(rows[-1]["checksum"]) if rows else GENESIS_CHECKSUM,
                    first_invalid_sequence=sequence,
                    reason=str(exc),
                )
            prior = str(row["checksum"])
            expected_sequence += 1

        if include_projections:
            branches = await (
                await db.execute(
                    "SELECT branch_id FROM workbench_branches WHERE task_id=? ORDER BY branch_id",
                    (task_id,),
                )
            ).fetchall()
            for branch in branches:
                branch_id = str(branch[0])
                visible = await self._visible_events(db, task_id, branch_id, at_sequence=None)
                snapshot = self.projector.replay(visible, task_id=task_id, branch_id=branch_id)
                metadata = await (
                    await db.execute(
                        """
                        SELECT head_sequence,head_event_checksum,canonical_state_json,state_checksum,updated_at
                        FROM projection_metadata
                        WHERE task_id=? AND projection_name=? AND branch_id=?
                        """,
                        (task_id, PROJECTION_NAME, branch_id),
                    )
                ).fetchone()
                expected_state = canonical_json_text(snapshot.state)
                expected_time = visible[-1].created_at if visible else GENESIS_TIME
                expected = (
                    snapshot.head.head_sequence,
                    snapshot.head.head_event_checksum,
                    expected_state,
                    snapshot.head.state_checksum,
                    expected_time,
                )
                if metadata is None or tuple(metadata) != expected:
                    return LedgerVerification(
                        valid=False,
                        task_id=task_id,
                        checked_events=len(rows),
                        head_sequence=int(rows[-1]["sequence"]) if rows else 0,
                        head_checksum=prior,
                        first_invalid_sequence=snapshot.head.head_sequence or None,
                        reason=f"projection metadata mismatch for branch {branch_id}",
                    )
        return LedgerVerification(
            valid=True,
            task_id=task_id,
            checked_events=len(rows),
            head_sequence=int(rows[-1]["sequence"]) if rows else 0,
            head_checksum=prior,
        )

    async def _visible_events(
        self,
        db: aiosqlite.Connection,
        task_id: str,
        branch_id: str,
        *,
        at_sequence: int | None,
    ) -> tuple[WorkbenchEvent, ...]:
        await self._require_branch(db, task_id, branch_id)
        global_head = (await self._global_head(db, task_id))[0]
        boundary = global_head if at_sequence is None else at_sequence
        if boundary < 0 or boundary > global_head:
            raise EventValidationError(f"invalid historical boundary: {boundary}")
        lineage: list[tuple[str, int]] = []
        current = branch_id
        cutoff = boundary
        seen: set[str] = set()
        while True:
            if current in seen:
                raise LedgerCorruption(None, "branch ancestry contains a cycle")
            seen.add(current)
            row = await (
                await db.execute(
                    """
                    SELECT parent_branch_id,forked_from_sequence
                    FROM workbench_branches WHERE task_id=? AND branch_id=?
                    """,
                    (task_id, current),
                )
            ).fetchone()
            if row is None:
                raise BranchNotFound(f"branch not found: {task_id}/{current}")
            lineage.append((current, cutoff))
            if row["parent_branch_id"] is None:
                break
            cutoff = min(cutoff, int(row["forked_from_sequence"]))
            current = str(row["parent_branch_id"])
        rows: list[aiosqlite.Row] = []
        for lineage_branch, lineage_cutoff in reversed(lineage):
            rows.extend(
                await (
                    await db.execute(
                        """
                        SELECT * FROM workbench_events
                        WHERE task_id=? AND branch_id=? AND sequence<=?
                        ORDER BY sequence
                        """,
                        (task_id, lineage_branch, lineage_cutoff),
                    )
                ).fetchall()
            )
        rows.sort(key=lambda row: int(row["sequence"]))
        return tuple(self._event_from_row(row) for row in rows)

    async def _resolve_retry(
        self,
        db: aiosqlite.Connection,
        task_id: str,
        command_id: str,
        validated: Sequence[ValidatedDraft],
        *,
        expected_frame_version: int | None,
    ) -> tuple[WorkbenchEvent, ...] | None:
        rows = await (
            await db.execute(
                """
                SELECT * FROM workbench_events
                WHERE task_id=? AND command_id=? ORDER BY command_sequence
                """,
                (task_id, command_id),
            )
        ).fetchall()
        if not rows:
            return None
        if len(rows) != len(validated):
            raise IdempotencyConflict("stored command event count differs from retry")
        events = tuple(self._event_from_row(row) for row in rows)
        for ordinal, (event, item) in enumerate(zip(events, validated), start=1):
            if event.command_sequence != ordinal:
                raise LedgerCorruption(event.sequence, "stored command ordinals are not contiguous")
            if event.idempotency_key != self._idempotency_key(task_id, command_id, ordinal):
                raise LedgerCorruption(event.sequence, "stored command idempotency key is invalid")
            draft = item.draft
            expected_payload = item.payload.model_dump(mode="json")
            if (
                event.event_type != draft.event_type
                or event.event_schema_version != draft.event_schema_version
                or event.actor != draft.actor
                or event.branch_id != draft.branch_id
                or event.cause != draft.cause
                or event.caused_by != draft.caused_by
                or event.payload != expected_payload
            ):
                raise IdempotencyConflict("stored command content differs from retry")
        first_sequence = events[0].sequence
        branch_id = events[0].branch_id
        before = await self._visible_events(
            db, task_id, branch_id, at_sequence=max(first_sequence - 1, 0)
        )
        starting_frame = before[-1].frame_version if before else 0
        confirms = any(item.definition.frame_effect is FrameEffect.CONFIRM for item in validated)
        if confirms and expected_frame_version != starting_frame:
            raise IdempotencyConflict("retry starting-frame contract differs")
        if expected_frame_version is not None and expected_frame_version != starting_frame:
            raise IdempotencyConflict("retry expected frame differs")
        return events

    def _build_event(
        self,
        *,
        task_id: str,
        command_id: str,
        command_sequence: int,
        sequence: int,
        validated: ValidatedDraft,
        frame_version: int,
        prior_checksum: str,
        event_id: str,
        created_at: str,
    ) -> WorkbenchEvent:
        draft = validated.draft
        payload = validated.payload.model_dump(mode="json")
        idempotency_key = self._idempotency_key(task_id, command_id, command_sequence)
        envelope = {
            "event_schema_version": draft.event_schema_version,
            "event_id": event_id,
            "task_id": task_id,
            "sequence": sequence,
            "event_type": draft.event_type,
            "actor_kind": draft.actor.kind,
            "actor_id": draft.actor.actor_id,
            "branch_id": draft.branch_id,
            "cause": draft.cause.value if draft.cause is not None else None,
            "caused_by": draft.caused_by,
            "command_id": command_id,
            "command_sequence": command_sequence,
            "frame_version": frame_version,
            "payload": payload,
            "idempotency_key": idempotency_key,
            "created_at": created_at,
            "prior_checksum": prior_checksum,
        }
        return WorkbenchEvent(
            event_schema_version=draft.event_schema_version,
            event_id=event_id,
            task_id=task_id,
            sequence=sequence,
            event_type=draft.event_type,
            actor=draft.actor,
            branch_id=draft.branch_id,
            cause=draft.cause,
            caused_by=draft.caused_by,
            command_id=command_id,
            command_sequence=command_sequence,
            frame_version=frame_version,
            payload=payload,
            idempotency_key=idempotency_key,
            prior_checksum=prior_checksum,
            checksum=event_checksum(envelope),
            created_at=created_at,
        )

    async def _insert_event(self, db: aiosqlite.Connection, event: WorkbenchEvent) -> None:
        await db.execute(
            """
            INSERT INTO workbench_events(
                event_id,task_id,sequence,event_type,event_schema_version,actor_kind,actor_id,
                branch_id,cause,caused_by,command_id,command_sequence,frame_version,payload_json,
                idempotency_key,prior_checksum,checksum,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event.event_id,
                event.task_id,
                event.sequence,
                event.event_type,
                event.event_schema_version,
                event.actor.kind,
                event.actor.actor_id,
                event.branch_id,
                event.cause.value if event.cause is not None else None,
                event.caused_by,
                event.command_id,
                event.command_sequence,
                event.frame_version,
                canonical_json_text(event.payload),
                event.idempotency_key,
                event.prior_checksum,
                event.checksum,
                event.created_at,
            ),
        )

    async def _publish_snapshot(
        self, db: aiosqlite.Connection, snapshot: WorkbenchSnapshot, updated_at: str
    ) -> None:
        await db.execute(
            """
            INSERT INTO projection_metadata(
                task_id,projection_name,branch_id,head_sequence,head_event_checksum,
                canonical_state_json,state_checksum,updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id,projection_name,branch_id) DO UPDATE SET
                head_sequence=excluded.head_sequence,
                head_event_checksum=excluded.head_event_checksum,
                canonical_state_json=excluded.canonical_state_json,
                state_checksum=excluded.state_checksum,
                updated_at=excluded.updated_at
            """,
            (
                snapshot.task_id,
                PROJECTION_NAME,
                snapshot.branch_id,
                snapshot.head.head_sequence,
                snapshot.head.head_event_checksum,
                canonical_json_text(snapshot.state),
                snapshot.head.state_checksum,
                updated_at,
            ),
        )

    async def _global_head(self, db: aiosqlite.Connection, task_id: str) -> tuple[int, str]:
        row = await (
            await db.execute(
                """
                SELECT sequence,checksum FROM workbench_events
                WHERE task_id=? ORDER BY sequence DESC LIMIT 1
                """,
                (task_id,),
            )
        ).fetchone()
        return (int(row["sequence"]), str(row["checksum"])) if row else (0, GENESIS_CHECKSUM)

    async def _require_task(self, db: aiosqlite.Connection, task_id: str) -> aiosqlite.Row:
        row = await (
            await db.execute("SELECT * FROM workbench_tasks WHERE id=?", (task_id,))
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"task not found: {task_id}")
        return row

    async def _require_branch(
        self, db: aiosqlite.Connection, task_id: str, branch_id: str
    ) -> aiosqlite.Row:
        row = await (
            await db.execute(
                "SELECT * FROM workbench_branches WHERE task_id=? AND branch_id=?",
                (task_id, branch_id),
            )
        ).fetchone()
        if row is None:
            raise BranchNotFound(f"branch not found: {task_id}/{branch_id}")
        return row

    def _event_from_row(self, row: aiosqlite.Row) -> WorkbenchEvent:
        return WorkbenchEvent(
            event_schema_version=int(row["event_schema_version"]),
            event_id=str(row["event_id"]),
            task_id=str(row["task_id"]),
            sequence=int(row["sequence"]),
            event_type=str(row["event_type"]),
            actor=EventActor(kind=str(row["actor_kind"]), actor_id=str(row["actor_id"])),
            branch_id=str(row["branch_id"]),
            cause=EventCause(str(row["cause"])) if row["cause"] is not None else None,
            caused_by=str(row["caused_by"]) if row["caused_by"] is not None else None,
            command_id=str(row["command_id"]),
            command_sequence=int(row["command_sequence"]),
            frame_version=int(row["frame_version"]),
            payload=json.loads(str(row["payload_json"])),
            idempotency_key=str(row["idempotency_key"]),
            prior_checksum=str(row["prior_checksum"]),
            checksum=str(row["checksum"]),
            created_at=str(row["created_at"]),
        )

    def _row_envelope(self, row: aiosqlite.Row, payload: Any) -> dict[str, Any]:
        return {
            "event_schema_version": int(row["event_schema_version"]),
            "event_id": str(row["event_id"]),
            "task_id": str(row["task_id"]),
            "sequence": int(row["sequence"]),
            "event_type": str(row["event_type"]),
            "actor_kind": str(row["actor_kind"]),
            "actor_id": str(row["actor_id"]),
            "branch_id": str(row["branch_id"]),
            "cause": str(row["cause"]) if row["cause"] is not None else None,
            "caused_by": str(row["caused_by"]) if row["caused_by"] is not None else None,
            "command_id": str(row["command_id"]),
            "command_sequence": int(row["command_sequence"]),
            "frame_version": int(row["frame_version"]),
            "payload": payload,
            "idempotency_key": str(row["idempotency_key"]),
            "created_at": str(row["created_at"]),
            "prior_checksum": str(row["prior_checksum"]),
        }

    @staticmethod
    def _idempotency_key(task_id: str, command_id: str, ordinal: int) -> str:
        value = {
            "domain": "workbench.command.event.v1",
            "task_id": task_id,
            "command_id": command_id,
            "ordinal": ordinal,
        }
        return hashlib.sha256(canonical_json_text(value).encode("utf-8")).hexdigest()

    def _new_event_id(self) -> str:
        event_id = str(self._id_factory())
        self._identity(event_id, "generated event_id")
        return event_id

    @staticmethod
    def _identity(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise EventValidationError(f"{name} must be a nonblank string")

    @staticmethod
    def _require_valid(verification: LedgerVerification) -> None:
        if not verification.valid:
            raise LedgerCorruption(verification.first_invalid_sequence, verification.reason or "unknown corruption")
