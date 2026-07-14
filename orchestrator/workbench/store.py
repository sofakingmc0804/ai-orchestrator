from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Mapping, Sequence

import aiosqlite

from orchestrator.config import Settings
from orchestrator.workbench.events import (
    EventRegistry,
    FrameEffect,
    GENESIS_CHECKSUM,
    ValidatedDraft,
    canonical_json_text,
    drafts_checksum,
    event_checksum,
    manifest_checksum,
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
from orchestrator.workbench.projector import (
    BranchTopology,
    GENESIS_TIME,
    PROJECTION_NAME,
    Projector,
    ReplayPlan,
)


BUSY_TIMEOUT_MS = 5_000


@dataclass(frozen=True)
class _StoreVisibleEvents(Sequence[WorkbenchEvent]):
    events: tuple[WorkbenchEvent, ...]
    task_id: str
    branch_id: str
    at_sequence: int | None
    topology: tuple[BranchTopology, ...]

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: Any) -> Any:
        return self.events[index]

    def extended(self, events: Iterable[WorkbenchEvent]) -> "_StoreVisibleEvents":
        additions = tuple(events)
        if any(event.task_id != self.task_id or event.branch_id != self.branch_id for event in additions):
            raise LedgerCorruption(None, "cannot extend visibility with another task or branch")
        if self.at_sequence is not None:
            raise LedgerCorruption(None, "cannot extend a historical visibility boundary")
        if not additions:
            return self
        prior_sequence = self.events[-1].sequence if self.events else 0
        if any(event.sequence <= prior_sequence for event in additions):
            raise LedgerCorruption(None, "visibility extension is not after the witnessed head")
        topology = (
            *self.topology[:-1],
            replace(self.topology[-1], visible_through_sequence=additions[-1].sequence),
        )
        return _StoreVisibleEvents(
            (*self.events, *additions),
            self.task_id,
            self.branch_id,
            None,
            topology,
        )


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
        except BaseException as original:
            try:
                await self._finish_close(db)
            except asyncio.CancelledError:
                if not isinstance(original, asyncio.CancelledError):
                    raise
            except BaseException as cleanup_error:
                raise original from cleanup_error
            raise
        else:
            await self._finish_close(db)

    async def _finish_close(self, db: aiosqlite.Connection) -> None:
        task = asyncio.create_task(db.close())
        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                continue
        task.result()
        if cancellation is not None:
            raise cancellation

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

    async def _finish_rollback(self, db: aiosqlite.Connection) -> None:
        if not db.in_transaction:
            return
        task = asyncio.create_task(db.rollback())
        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                continue
        task.result()
        if cancellation is not None:
            raise cancellation

    async def _rollback_after_error(
        self, db: aiosqlite.Connection, original: BaseException
    ) -> None:
        try:
            await self._finish_rollback(db)
        except asyncio.CancelledError:
            if not isinstance(original, asyncio.CancelledError):
                raise
        except BaseException as cleanup_error:
            raise original from cleanup_error

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
                if existing_task is None:
                    orphan = await (
                        await db.execute(
                            """
                            SELECT 1 FROM (
                                SELECT task_id FROM workbench_events WHERE task_id=?
                                UNION ALL
                                SELECT task_id FROM workbench_command_manifests WHERE task_id=?
                                UNION ALL
                                SELECT task_id FROM workbench_branches WHERE task_id=?
                                UNION ALL
                                SELECT task_id FROM projection_metadata WHERE task_id=?
                            ) LIMIT 1
                            """,
                            (task_id, task_id, task_id, task_id),
                        )
                    ).fetchone()
                    if orphan is not None:
                        raise LedgerCorruption(None, "task authority row is missing")
                else:
                    await self._require_task(db, task_id)
                if existing_task is not None:
                    verification = await self._verify_ledger_db(
                        db, task_id, include_projections=True
                    )
                    self._require_valid(verification)
                existing = await self._resolve_retry(
                    db, task_id, command_id, (validated,), expected_frame_version=None
                )
                if existing is not None:
                    await self._finish_rollback(db)
                    return existing[0]
                if existing_task is not None:
                    raise IdempotencyConflict(f"task already exists under another command: {task_id}")

                created_at = normalize_timestamp(self._clock())
                events, manifest = self._prepare_command(
                    task_id=task_id,
                    command_id=command_id,
                    validated=(validated,),
                    target_branch_id="main",
                    first_sequence=1,
                    starting_frame_version=0,
                    expected_frame_version=0,
                    prior_checksum=GENESIS_CHECKSUM,
                    created_at=created_at,
                )
                event = events[0]
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
                await self._insert_manifest(db, manifest)
                await self._insert_event(db, event)
                await db.execute(
                    "UPDATE workbench_tasks SET last_transition_event_id=? WHERE id=?",
                    (event.event_id, task_id),
                )
                await db.execute(
                    "UPDATE workbench_branches SET last_transition_event_id=? "
                    "WHERE task_id=? AND branch_id='main'",
                    (event.event_id, task_id),
                )
                staged = await self._verify_ledger_db(db, task_id, include_projections=False)
                self._require_valid(staged)
                visible = await self._visible_events(db, task_id, "main", at_sequence=None)
                snapshot = self._replay(
                    self.projector, visible, task_id=task_id, branch_id="main"
                )
                await self._publish_snapshot(db, snapshot, event.created_at)
                await self._finish_commit(db)
                return event
            except BaseException as original:
                await self._rollback_after_error(db, original)
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
                    await self._finish_rollback(db)
                    return existing

                branch_id = next(iter(branches))
                await self._require_branch(db, task_id, branch_id)
                visible = await self._visible_events(db, task_id, branch_id, at_sequence=None)
                snapshot = self._replay(
                    self.projector, visible, task_id=task_id, branch_id=branch_id
                )
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
                events, manifest = self._prepare_command(
                    task_id=task_id,
                    command_id=command_id,
                    validated=validated,
                    target_branch_id=branch_id,
                    first_sequence=last[0] + 1,
                    starting_frame_version=current_frame,
                    expected_frame_version=current_frame,
                    prior_checksum=last[1],
                    created_at=normalize_timestamp(self._clock()),
                )

                projected = self._replay(
                    self.projector,
                    visible.extended(events),
                    task_id=task_id,
                    branch_id=branch_id,
                )
                await self._insert_manifest(db, manifest)
                for event in events:
                    await self._insert_event(db, event)
                staged = await self._verify_ledger_db(db, task_id, include_projections=False)
                self._require_valid(staged)
                await self._publish_snapshot(db, projected, events[-1].created_at)
                await self._finish_commit(db)
                return tuple(events)
            except BaseException as original:
                await self._rollback_after_error(db, original)
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
                    actual = (
                        self._strict_text(branch["parent_branch_id"], "branch parent id"),
                        self._strict_integer(branch["forked_from_sequence"], "branch fork sequence"),
                        self._strict_integer(
                            branch["forked_from_frame_version"], "branch fork frame version"
                        ),
                        self._strict_text(
                            branch["created_by_event_id"], "branch creation event id"
                        ),
                    ) if branch is not None else None
                    expected = (parent_branch_id, at_sequence, fork_frame, existing[0].event_id)
                    if actual != expected:
                        raise LedgerCorruption(existing[0].sequence, "fork command and branch topology disagree")
                    await self._finish_rollback(db)
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
                parent_snapshot = self._replay(
                    self.projector,
                    parent_current,
                    task_id=task_id,
                    branch_id=parent_branch_id,
                )
                head_sequence, head_checksum = await self._global_head(db, task_id)
                events, manifest = self._prepare_command(
                    task_id=task_id,
                    command_id=command_id,
                    validated=(validated,),
                    target_branch_id=parent_branch_id,
                    first_sequence=head_sequence + 1,
                    starting_frame_version=parent_snapshot.frame_version,
                    expected_frame_version=parent_snapshot.frame_version,
                    prior_checksum=head_checksum,
                    created_at=normalize_timestamp(self._clock()),
                )
                event = events[0]
                await self._insert_manifest(db, manifest)
                await self._insert_event(db, event)
                await db.execute(
                    """
                    INSERT INTO workbench_branches(
                        task_id,branch_id,parent_branch_id,forked_from_sequence,
                        forked_from_frame_version,created_by_event_id,status,created_at,
                        last_transition_event_id
                    ) VALUES (?,?,?,?,?,?,'active',?,?)
                    """,
                    (
                        task_id,
                        branch_id,
                        parent_branch_id,
                        at_sequence,
                        fork_frame,
                        event.event_id,
                        event.created_at,
                        event.event_id,
                    ),
                )
                staged = await self._verify_ledger_db(db, task_id, include_projections=False)
                self._require_valid(staged)
                parent_projected = self._replay(
                    self.projector,
                    parent_current.extended((event,)),
                    task_id=task_id,
                    branch_id=parent_branch_id,
                )
                child_visible = await self._visible_events(
                    db, task_id, branch_id, at_sequence=None
                )
                child_projected = self._replay(
                    self.projector,
                    child_visible,
                    task_id=task_id,
                    branch_id=branch_id,
                )
                await self._publish_snapshot(db, parent_projected, event.created_at)
                child_time = boundary_events[-1].created_at if boundary_events else GENESIS_TIME
                await self._publish_snapshot(db, child_projected, child_time)
                await self._finish_commit(db)
                return event
            except BaseException as original:
                await self._rollback_after_error(db, original)
                raise

    async def snapshot(
        self,
        task_id: str,
        branch_id: str = "main",
        at_sequence: int | None = None,
        verify: bool = True,
    ) -> WorkbenchSnapshot:
        async with self._connection() as db:
            try:
                await db.execute("BEGIN")
                await self._require_task(db, task_id)
                await self._require_branch(db, task_id, branch_id)
                # Authority verification is never optional.  ``verify=False``
                # skips only the disposable current projection comparison.
                verification = await self._verify_ledger_db(
                    db,
                    task_id,
                    include_projections=bool(verify and at_sequence is None),
                )
                self._require_valid(verification)
                visible = await self._visible_events(db, task_id, branch_id, at_sequence=at_sequence)
                result = self._replay(
                    self.projector,
                    visible,
                    task_id=task_id,
                    branch_id=branch_id,
                    at_sequence=at_sequence,
                )
                await self._finish_rollback(db)
                return result
            except BaseException as original:
                await self._rollback_after_error(db, original)
                raise

    async def verify_ledger(self, task_id: str) -> LedgerVerification:
        async with self._connection() as db:
            try:
                await db.execute("BEGIN")
                await self._require_task(db, task_id)
                result = await self._verify_ledger_db(db, task_id, include_projections=True)
                await self._finish_rollback(db)
                return result
            except BaseException as original:
                await self._rollback_after_error(db, original)
                raise

    async def _rebuild_projections(
        self, projector: Projector, task_id: str
    ) -> tuple[Any, ...]:
        if projector.registry is not self.registry:
            raise LedgerCorruption(None, "projection rebuild registry is not the store authority")
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
                    branch_id = self._strict_text(row[0], "branch id")
                    visible = await self._visible_events(db, task_id, branch_id, at_sequence=None)
                    snapshot = self._replay(
                        projector, visible, task_id=task_id, branch_id=branch_id
                    )
                    updated_at = visible[-1].created_at if visible else GENESIS_TIME
                    await self._publish_snapshot(db, snapshot, updated_at)
                    heads.append(snapshot.head)
                after = await self._global_head(db, task_id)
                if after != before:
                    raise LedgerCorruption(after[0], "ledger head changed during projection rebuild")
                await self._finish_commit(db)
                return tuple(heads)
            except BaseException as original:
                await self._rollback_after_error(db, original)
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
            sequence = row["sequence"] if type(row["sequence"]) is int else expected_sequence
            try:
                sequence = self._strict_integer(row["sequence"], "event sequence")
                event_schema_version = self._strict_integer(
                    row["event_schema_version"], "event schema version"
                )
                self._strict_integer(row["command_sequence"], "event command sequence")
                self._strict_integer(row["frame_version"], "event frame version")
                if sequence != expected_sequence:
                    raise ValueError(f"expected contiguous sequence {expected_sequence}")
                if self._strict_text(row["prior_checksum"], "event prior checksum") != prior:
                    raise ValueError("prior checksum link does not match")
                payload_text = self._strict_text(row["payload_json"], "event payload json")
                payload = json.loads(payload_text)
                if payload_text != canonical_json_text(payload):
                    raise ValueError("payload is not stored in canonical JSON form")
                created_at = self._strict_text(row["created_at"], "event created at")
                if created_at != normalize_timestamp(created_at):
                    raise ValueError("created_at is not normalized UTC")
                envelope = self._row_envelope(row, payload)
                computed = event_checksum(envelope)
                if self._strict_text(row["checksum"], "event checksum") != computed:
                    raise ValueError("stored checksum does not match immutable envelope")
                # Structural validation is also part of fail-closed verification.
                validated = self.registry.validate(
                    EventDraft(
                        event_type=self._strict_text(row["event_type"], "event type"),
                        event_schema_version=event_schema_version,
                        actor=EventActor(
                            kind=self._strict_text(row["actor_kind"], "event actor kind"),
                            actor_id=self._strict_text(row["actor_id"], "event actor id"),
                        ),
                        branch_id=self._strict_text(row["branch_id"], "event branch id"),
                        cause=(
                            EventCause(self._strict_text(row["cause"], "event cause"))
                            if row["cause"] is not None
                            else None
                        ),
                        caused_by=self._strict_text(
                            row["caused_by"], "event caused by", nullable=True
                        ),
                        payload=payload,
                    )
                )
                if canonical_json_text(payload) != canonical_json_text(
                    validated.payload.model_dump(mode="json")
                ):
                    raise ValueError("stored payload differs from registry-normalized payload")
            except Exception as exc:
                return LedgerVerification(
                    valid=False,
                    task_id=task_id,
                    checked_events=expected_sequence - 1,
                    head_sequence=(
                        rows[-1]["sequence"]
                        if rows and type(rows[-1]["sequence"]) is int
                        else 0
                    ),
                    head_checksum=(
                        rows[-1]["checksum"]
                        if rows and type(rows[-1]["checksum"]) is str
                        else GENESIS_CHECKSUM
                    ),
                    first_invalid_sequence=sequence,
                    reason=str(exc),
                )
            prior = self._strict_text(row["checksum"], "event checksum")
            expected_sequence += 1

        try:
            topology_failure = await self._verify_branch_topology(db, task_id, rows)
        except Exception as exc:
            topology_failure = self._topology_failure(
                task_id, rows, None, str(exc)
            )
        if topology_failure is not None:
            return topology_failure
        try:
            task_failure = await self._verify_task_authority(db, task_id, rows)
        except Exception as exc:
            task_failure = self._topology_failure(task_id, rows, 1, str(exc))
        if task_failure is not None:
            return task_failure
        try:
            manifest_failure = await self._verify_command_manifests(db, task_id, rows)
        except Exception as exc:
            manifest_failure = self._manifest_failure(
                task_id,
                self._strict_integer(rows[-1]["sequence"], "event sequence") if rows else 0,
                self._strict_text(rows[-1]["checksum"], "event checksum")
                if rows
                else GENESIS_CHECKSUM,
                None,
                str(exc),
            )
        if manifest_failure is not None:
            return manifest_failure

        if include_projections:
            branches = await (
                await db.execute(
                    "SELECT branch_id FROM workbench_branches WHERE task_id=? ORDER BY branch_id",
                    (task_id,),
                )
            ).fetchall()
            for branch in branches:
                branch_id = self._strict_text(branch[0], "branch id")
                visible = await self._visible_events(db, task_id, branch_id, at_sequence=None)
                snapshot = self._replay(
                    self.projector, visible, task_id=task_id, branch_id=branch_id
                )
                metadata = await (
                    await db.execute(
                        """
                        SELECT task_id,projection_name,branch_id,head_sequence,head_event_checksum,
                               canonical_state_json,state_checksum,updated_at
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
                try:
                    if metadata is not None:
                        actual = (
                            self._strict_integer(
                                metadata["head_sequence"], "projection head sequence"
                            ),
                            self._strict_text(
                                metadata["head_event_checksum"], "projection head checksum"
                            ),
                            self._strict_text(
                                metadata["canonical_state_json"], "projection canonical state"
                            ),
                            self._strict_text(
                                metadata["state_checksum"], "projection state checksum"
                            ),
                            self._strict_text(metadata["updated_at"], "projection updated at"),
                        )
                        if (
                            self._strict_text(metadata["task_id"], "projection task id")
                            != task_id
                            or self._strict_text(
                                metadata["projection_name"], "projection name"
                            )
                            != PROJECTION_NAME
                            or self._strict_text(
                                metadata["branch_id"], "projection branch id"
                            )
                            != branch_id
                        ):
                            actual = None
                    else:
                        actual = None
                except Exception:
                    actual = None
                if actual != expected:
                    return LedgerVerification(
                        valid=False,
                        task_id=task_id,
                        checked_events=len(rows),
                        head_sequence=self._strict_integer(
                            rows[-1]["sequence"], "event sequence"
                        )
                        if rows
                        else 0,
                        head_checksum=prior,
                        first_invalid_sequence=snapshot.head.head_sequence or None,
                        reason=f"projection metadata mismatch for branch {branch_id}",
                    )
        return LedgerVerification(
            valid=True,
            task_id=task_id,
            checked_events=len(rows),
            head_sequence=self._strict_integer(rows[-1]["sequence"], "event sequence")
            if rows
            else 0,
            head_checksum=prior,
        )

    async def _verify_command_manifests(
        self,
        db: aiosqlite.Connection,
        task_id: str,
        event_rows: Sequence[aiosqlite.Row],
    ) -> LedgerVerification | None:
        manifests = await (
            await db.execute(
                """
                SELECT * FROM workbench_command_manifests
                WHERE task_id=? ORDER BY first_sequence
                """,
                (task_id,),
            )
        ).fetchall()
        head_sequence = (
            event_rows[-1]["sequence"]
            if event_rows and type(event_rows[-1]["sequence"]) is int
            else 0
        )
        head_checksum = (
            self._strict_text(event_rows[-1]["checksum"], "event checksum")
            if event_rows
            else GENESIS_CHECKSUM
        )
        if not manifests:
            return self._manifest_failure(
                task_id,
                head_sequence,
                head_checksum,
                1 if event_rows else None,
                "ledger has no command manifests",
            )

        expected_first = 1
        claimed_sequences: set[int] = set()
        root_events = [
            row
            for row in event_rows
            if self._strict_text(row["event_type"], "event type") == "task.created"
        ]
        if (
            len(root_events) != 1
            or self._strict_integer(root_events[0]["sequence"], "event sequence") != 1
        ):
            return self._manifest_failure(
                task_id, head_sequence, head_checksum, 1, "ledger must have one task.created at sequence 1"
            )

        for manifest in manifests:
            try:
                first_sequence = self._strict_integer(
                    manifest["first_sequence"], "manifest first sequence"
                )
                last_sequence = self._strict_integer(
                    manifest["last_sequence"], "manifest last sequence"
                )
                event_count = self._strict_integer(
                    manifest["event_count"], "manifest event count"
                )
                command_id = self._strict_text(manifest["command_id"], "manifest command id")
                branch_id = self._strict_text(
                    manifest["target_branch_id"], "manifest target branch id"
                )
                fields = {
                    "task_id": self._strict_text(manifest["task_id"], "manifest task id"),
                    "command_id": command_id,
                    "target_branch_id": branch_id,
                    "event_count": event_count,
                    "first_sequence": first_sequence,
                    "last_sequence": last_sequence,
                    "first_event_id": self._strict_text(
                        manifest["first_event_id"], "manifest first event id"
                    ),
                    "last_event_id": self._strict_text(
                        manifest["last_event_id"], "manifest last event id"
                    ),
                    "starting_frame_version": self._strict_integer(
                        manifest["starting_frame_version"], "manifest starting frame version"
                    ),
                    "expected_frame_version": self._strict_integer(
                        manifest["expected_frame_version"], "manifest expected frame version"
                    ),
                    "confirm_ordinal": (
                        self._strict_integer(
                            manifest["confirm_ordinal"], "manifest confirm ordinal"
                        )
                        if manifest["confirm_ordinal"] is not None
                        else None
                    ),
                    "drafts_checksum": self._strict_text(
                        manifest["drafts_checksum"], "manifest drafts checksum"
                    ),
                    "created_at": self._strict_text(
                        manifest["created_at"], "manifest created at"
                    ),
                }
                if manifest_checksum(fields) != self._strict_text(
                    manifest["manifest_checksum"], "manifest checksum"
                ):
                    raise ValueError("manifest checksum mismatch")
                if first_sequence != expected_first or last_sequence != first_sequence + event_count - 1:
                    raise ValueError("manifest ranges are not contiguous from sequence one")
                if fields["created_at"] != normalize_timestamp(fields["created_at"]):
                    raise ValueError("manifest created_at is not normalized UTC")
            except Exception as exc:
                return self._manifest_failure(
                    task_id,
                    head_sequence,
                    head_checksum,
                    (
                        manifest["first_sequence"]
                        if type(manifest["first_sequence"]) is int
                        else None
                    ),
                    str(exc),
                )

            command_rows = [
                row
                for row in event_rows
                if self._strict_text(row["command_id"], "event command id") == command_id
            ]
            command_rows.sort(
                key=lambda row: self._strict_integer(
                    row["command_sequence"], "event command sequence"
                )
            )
            if len(command_rows) != event_count:
                return self._manifest_failure(
                    task_id,
                    head_sequence,
                    head_checksum,
                    first_sequence,
                    f"command {command_id} event cardinality mismatch",
                )
            validated: list[ValidatedDraft] = []
            for ordinal, row in enumerate(command_rows, start=1):
                sequence = self._strict_integer(row["sequence"], "event sequence")
                if sequence in claimed_sequences:
                    return self._manifest_failure(
                        task_id, head_sequence, head_checksum, sequence, "event claimed by multiple manifests"
                    )
                claimed_sequences.add(sequence)
                if (
                    self._strict_integer(row["command_sequence"], "event command sequence")
                    != ordinal
                    or sequence != first_sequence + ordinal - 1
                    or self._strict_text(row["task_id"], "event task id") != task_id
                    or self._strict_text(row["command_id"], "event command id") != command_id
                    or self._strict_text(row["branch_id"], "event branch id") != branch_id
                    or (
                        ordinal == 1
                        and self._strict_text(row["event_id"], "event id")
                        != fields["first_event_id"]
                    )
                    or (
                        ordinal == event_count
                        and self._strict_text(row["event_id"], "event id")
                        != fields["last_event_id"]
                    )
                    or self._strict_text(row["created_at"], "event created at")
                    != fields["created_at"]
                    or self._strict_text(row["idempotency_key"], "event idempotency key")
                    != self._idempotency_key(task_id, command_id, ordinal)
                ):
                    return self._manifest_failure(
                        task_id,
                        head_sequence,
                        head_checksum,
                        sequence,
                        f"command {command_id} ordinal, range, endpoint, branch, time, or idempotency mismatch",
                    )
                try:
                    payload = json.loads(
                        self._strict_text(row["payload_json"], "event payload json")
                    )
                    validated.append(
                        self.registry.validate(
                            EventDraft(
                                event_type=self._strict_text(row["event_type"], "event type"),
                                event_schema_version=self._strict_integer(
                                    row["event_schema_version"], "event schema version"
                                ),
                                actor=EventActor(
                                    kind=self._strict_text(
                                        row["actor_kind"], "event actor kind"
                                    ),
                                    actor_id=self._strict_text(
                                        row["actor_id"], "event actor id"
                                    ),
                                ),
                                branch_id=self._strict_text(
                                    row["branch_id"], "event branch id"
                                ),
                                cause=(
                                    EventCause(
                                        self._strict_text(row["cause"], "event cause")
                                    )
                                    if row["cause"] is not None
                                    else None
                                ),
                                caused_by=(
                                    self._strict_text(
                                        row["caused_by"], "event caused by"
                                    )
                                    if row["caused_by"] is not None
                                    else None
                                ),
                                payload=payload,
                            )
                        )
                    )
                    if canonical_json_text(payload) != canonical_json_text(
                        validated[-1].payload.model_dump(mode="json")
                    ):
                        raise ValueError("stored payload differs from registry-normalized payload")
                except Exception as exc:
                    return self._manifest_failure(
                        task_id, head_sequence, head_checksum, sequence, str(exc)
                    )
            if drafts_checksum(task_id, command_id, validated) != fields["drafts_checksum"]:
                return self._manifest_failure(
                    task_id,
                    head_sequence,
                    head_checksum,
                    first_sequence,
                    f"command {command_id} canonical drafts checksum mismatch",
                )

            try:
                before = await self._visible_events(
                    db, task_id, branch_id, at_sequence=first_sequence - 1
                )
            except Exception as exc:
                return self._manifest_failure(
                    task_id, head_sequence, head_checksum, first_sequence, str(exc)
                )
            starting_frame = before[-1].frame_version if before else 0
            if (
                fields["starting_frame_version"] != starting_frame
                or fields["expected_frame_version"] != starting_frame
            ):
                return self._manifest_failure(
                    task_id,
                    head_sequence,
                    head_checksum,
                    first_sequence,
                    f"command {command_id} starting or expected frame mismatch",
                )
            visible_ids = {event.event_id for event in before}
            frame_version = starting_frame
            observed_confirm: int | None = None
            for ordinal, (row, item) in enumerate(zip(command_rows, validated), start=1):
                sequence = self._strict_integer(row["sequence"], "event sequence")
                caused_by = self._strict_text(
                    row["caused_by"], "event caused by", nullable=True
                )
                if caused_by is not None and caused_by not in visible_ids:
                    return self._manifest_failure(
                        task_id,
                        head_sequence,
                        head_checksum,
                        sequence,
                        f"command {command_id} cause is not visible at its append boundary",
                    )
                if item.definition.frame_effect is FrameEffect.CONFIRM:
                    if observed_confirm is not None:
                        return self._manifest_failure(
                            task_id, head_sequence, head_checksum, sequence, "multiple confirms in command"
                        )
                    observed_confirm = ordinal
                    frame_version += 1
                if self._strict_integer(row["frame_version"], "event frame version") != frame_version:
                    return self._manifest_failure(
                        task_id,
                        head_sequence,
                        head_checksum,
                        sequence,
                        f"command {command_id} registry-derived frame transition mismatch",
                    )
            if fields["confirm_ordinal"] != observed_confirm:
                return self._manifest_failure(
                    task_id,
                    head_sequence,
                    head_checksum,
                    first_sequence,
                    f"command {command_id} confirm ordinal mismatch",
                )
            expected_first = last_sequence + 1

        event_sequences = {
            self._strict_integer(row["sequence"], "event sequence") for row in event_rows
        }
        if claimed_sequences != event_sequences:
            missing = sorted(event_sequences - claimed_sequences)
            return self._manifest_failure(
                task_id,
                head_sequence,
                head_checksum,
                missing[0] if missing else expected_first,
                "orphan event or manifest",
            )
        if expected_first - 1 != head_sequence:
            return self._manifest_failure(
                task_id, head_sequence, head_checksum, expected_first, "manifest ranges do not cover ledger head"
            )
        root = root_events[0]
        first_manifest = manifests[0]
        if (
            self._strict_integer(first_manifest["first_sequence"], "manifest first sequence") != 1
            or self._strict_integer(first_manifest["event_count"], "manifest event count") != 1
            or self._strict_text(first_manifest["first_event_id"], "manifest first event id")
            != self._strict_text(root["event_id"], "event id")
            or self._strict_text(first_manifest["last_event_id"], "manifest last event id")
            != self._strict_text(root["event_id"], "event id")
            or self._strict_text(
                first_manifest["target_branch_id"], "manifest target branch id"
            )
            != self._strict_text(root["branch_id"], "event branch id")
            or self._strict_text(root["prior_checksum"], "event prior checksum")
            != GENESIS_CHECKSUM
            or self._strict_integer(root["frame_version"], "event frame version") != 0
        ):
            return self._manifest_failure(
                task_id, head_sequence, head_checksum, 1, "root manifest is not bound to task.created"
            )
        return None

    async def _verify_task_authority(
        self,
        db: aiosqlite.Connection,
        task_id: str,
        event_rows: Sequence[aiosqlite.Row],
    ) -> LedgerVerification | None:
        creation_rows = [
            row
            for row in event_rows
            if self._strict_text(row["event_type"], "event type") == "task.created"
        ]
        task = await (
            await db.execute("SELECT * FROM workbench_tasks WHERE id=?", (task_id,))
        ).fetchone()
        if task is None or len(creation_rows) != 1:
            return self._topology_failure(
                task_id, event_rows, 1, "task row and unique task.created event disagree"
            )
        creation = creation_rows[0]
        try:
            payload = json.loads(
                self._strict_text(creation["payload_json"], "event payload json")
            )
            created_at = self._strict_text(creation["created_at"], "event created at")
            initial_branch_id = self._strict_text(
                payload["initial_branch_id"], "task initial branch id"
            )
            expected = (
                task_id,
                self._strict_text(payload["title"], "task creation title"),
                "intake",
                initial_branch_id,
                0,
                created_at,
                created_at,
            )
            observed = (
                self._strict_text(task["id"], "task id"),
                self._strict_text(task["title"], "task title"),
                self._strict_text(task["state"], "task state"),
                self._strict_text(task["active_branch_id"], "task active branch id"),
                self._strict_integer(task["current_frame_version"], "task current frame version"),
                self._strict_text(task["created_at"], "task created at"),
                self._strict_text(task["updated_at"], "task updated at"),
            )
            if created_at != normalize_timestamp(created_at) or observed != expected:
                raise ValueError("task row is not bound to task.created")
            root = await (
                await db.execute(
                    "SELECT * FROM workbench_branches WHERE task_id=? AND branch_id=?",
                    (task_id, initial_branch_id),
                )
            ).fetchone()
            if root is None:
                raise ValueError("task root branch is missing")
            if (
                root["parent_branch_id"] is not None
                or root["forked_from_sequence"] is not None
                or root["forked_from_frame_version"] is not None
                or root["created_by_event_id"] is not None
                or self._strict_text(root["status"], "branch status") != "active"
                or self._strict_text(root["created_at"], "branch created at") != created_at
                or normalize_timestamp(
                    self._strict_text(root["created_at"], "branch created at")
                )
                != created_at
            ):
                raise ValueError("root branch is not bound to task.created")
        except Exception as exc:
            return self._topology_failure(
                task_id,
                event_rows,
                self._strict_integer(creation["sequence"], "event sequence"),
                str(exc),
            )
        return None

    @staticmethod
    def _manifest_failure(
        task_id: str,
        head_sequence: int,
        head_checksum: str,
        sequence: int | None,
        detail: str,
    ) -> LedgerVerification:
        return LedgerVerification(
            valid=False,
            task_id=task_id,
            checked_events=max((sequence or 1) - 1, 0),
            head_sequence=head_sequence,
            head_checksum=head_checksum,
            first_invalid_sequence=sequence,
            reason=f"command manifest mismatch: {detail}",
        )

    async def _verify_branch_topology(
        self,
        db: aiosqlite.Connection,
        task_id: str,
        event_rows: Sequence[aiosqlite.Row],
    ) -> LedgerVerification | None:
        branches = await (
            await db.execute(
                """
                SELECT *
                FROM workbench_branches WHERE task_id=? ORDER BY branch_id
                """,
                (task_id,),
            )
        ).fetchall()
        by_id = {
            self._strict_text(row["event_id"], "event id"): row for row in event_rows
        }
        fork_events: dict[str, aiosqlite.Row] = {}
        for row in event_rows:
            if self._strict_text(row["event_type"], "event type") != "branch.forked":
                continue
            try:
                payload = json.loads(
                    self._strict_text(row["payload_json"], "event payload json")
                )
                child_id = self._strict_text(payload["new_branch_id"], "fork child branch id")
            except Exception as exc:
                return self._topology_failure(
                    task_id,
                    event_rows,
                    self._strict_integer(row["sequence"], "event sequence"),
                    str(exc),
                )
            if child_id in fork_events:
                return self._topology_failure(
                    task_id,
                    event_rows,
                    self._strict_integer(row["sequence"], "event sequence"),
                    "duplicate fork event for child",
                )
            fork_events[child_id] = row

        matched_forks: set[str] = set()
        roots = 0
        for branch in branches:
            for column in ("task_id", "branch_id", "status", "created_at"):
                self._strict_text(branch[column], f"branch {column}")
            self._strict_text(branch["parent_branch_id"], "branch parent id", nullable=True)
            self._strict_text(
                branch["created_by_event_id"], "branch creation event id", nullable=True
            )
            self._strict_text(branch["task_id"], "branch task id")
            branch_id = self._strict_text(branch["branch_id"], "branch id")
            parent = branch["parent_branch_id"]
            if parent is None:
                roots += 1
                if any(
                    branch[field] is not None
                    for field in (
                        "forked_from_sequence",
                        "forked_from_frame_version",
                        "created_by_event_id",
                    )
                ):
                    return self._topology_failure(
                        task_id, event_rows, 1, f"root branch {branch_id} has fork identity"
                    )
                continue
            parent = self._strict_text(parent, "branch parent id")
            created_id = self._strict_text(
                branch["created_by_event_id"], "branch creation event id"
            )
            event = by_id.get(created_id)
            if event is None or self._strict_text(event["event_type"], "event type") != "branch.forked":
                return self._topology_failure(
                    task_id, event_rows, None, f"branch {branch_id} lacks its fork event"
                )
            sequence = self._strict_integer(event["sequence"], "event sequence")
            try:
                payload = json.loads(
                    self._strict_text(event["payload_json"], "event payload json")
                )
                event_created_at = self._strict_text(
                    event["created_at"], "event created at"
                )
                branch_created_at = self._strict_text(
                    branch["created_at"], "branch created at"
                )
                forked_from_sequence = self._strict_integer(
                    branch["forked_from_sequence"], "branch fork sequence"
                )
                forked_from_frame_version = self._strict_integer(
                    branch["forked_from_frame_version"], "branch fork frame version"
                )
                expected = (
                    branch_id,
                    parent,
                    forked_from_sequence,
                    forked_from_frame_version,
                )
                observed = (
                    self._strict_text(payload["new_branch_id"], "fork child branch id"),
                    self._strict_text(payload["parent_branch_id"], "fork parent branch id"),
                    self._strict_integer(payload["forked_from_sequence"], "fork payload sequence"),
                    self._strict_integer(
                        payload["forked_from_frame_version"], "fork payload frame version"
                    ),
                )
                if (
                    self._strict_text(branch["status"], "branch status") != "active"
                    or branch_created_at != event_created_at
                    or normalize_timestamp(branch_created_at) != event_created_at
                ):
                    raise ValueError("child branch state or creation time is not bound to fork event")
            except Exception as exc:
                return self._topology_failure(task_id, event_rows, sequence, str(exc))
            if observed != expected or self._strict_text(
                event["branch_id"], "event branch id"
            ) != parent:
                return self._topology_failure(
                    task_id,
                    event_rows,
                    sequence,
                    f"branch {branch_id} does not match its immutable fork event",
                )
            if forked_from_sequence < 1 or sequence <= forked_from_sequence:
                return self._topology_failure(
                    task_id, event_rows, sequence, f"branch {branch_id} has an invalid fork boundary"
                )
            matched_forks.add(branch_id)
        if roots != 1:
            return self._topology_failure(
                task_id, event_rows, None, f"expected one root branch, found {roots}"
            )
        unmatched = set(fork_events) - matched_forks
        if unmatched:
            child = sorted(unmatched)[0]
            event = fork_events[child]
            return self._topology_failure(
                task_id,
                event_rows,
                self._strict_integer(event["sequence"], "event sequence"),
                f"fork event has no matching branch: {child}",
            )
        return None

    @staticmethod
    def _topology_failure(
        task_id: str,
        rows: Sequence[aiosqlite.Row],
        sequence: int | None,
        detail: str,
    ) -> LedgerVerification:
        return LedgerVerification(
            valid=False,
            task_id=task_id,
            checked_events=len(rows),
            head_sequence=(
                rows[-1]["sequence"]
                if rows and type(rows[-1]["sequence"]) is int
                else 0
            ),
            head_checksum=(
                rows[-1]["checksum"]
                if rows and type(rows[-1]["checksum"]) is str
                else GENESIS_CHECKSUM
            ),
            first_invalid_sequence=sequence,
            reason=f"branch topology mismatch: {detail}",
        )

    def _replay(
        self,
        projector: Projector,
        events: _StoreVisibleEvents,
        *,
        task_id: str,
        branch_id: str,
        at_sequence: int | None = None,
    ) -> WorkbenchSnapshot:
        if (
            not isinstance(events, _StoreVisibleEvents)
            or events.task_id != task_id
            or events.branch_id != branch_id
            or events.at_sequence != at_sequence
        ):
            raise LedgerCorruption(None, "store replay requires an exact topology visibility witness")
        plan = ReplayPlan(
            task_id=task_id,
            target_branch_id=branch_id,
            at_sequence=at_sequence,
            topology=events.topology,
            visible_events=events.events,
        )
        return projector.replay(
            plan,
            task_id=task_id,
            branch_id=branch_id,
            at_sequence=at_sequence,
        )

    async def _visible_events(
        self,
        db: aiosqlite.Connection,
        task_id: str,
        branch_id: str,
        *,
        at_sequence: int | None,
    ) -> _StoreVisibleEvents:
        await self._require_branch(db, task_id, branch_id)
        global_head = (await self._global_head(db, task_id))[0]
        boundary = global_head if at_sequence is None else at_sequence
        if boundary < 0 or boundary > global_head:
            raise EventValidationError(f"invalid historical boundary: {boundary}")
        lineage: list[BranchTopology] = []
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
                    SELECT branch_id,parent_branch_id,forked_from_sequence,
                           forked_from_frame_version,created_by_event_id
                    FROM workbench_branches WHERE task_id=? AND branch_id=?
                    """,
                    (task_id, current),
                )
            ).fetchone()
            if row is None:
                raise BranchNotFound(f"branch not found: {task_id}/{current}")
            stored_branch_id = self._strict_text(row["branch_id"], "branch id")
            if stored_branch_id != current:
                raise LedgerCorruption(None, "branch lookup identity mismatch")
            self._strict_text(row["parent_branch_id"], "branch parent id", nullable=True)
            self._strict_text(
                row["created_by_event_id"], "branch creation event id", nullable=True
            )
            creation_event = None
            if row["created_by_event_id"] is not None:
                event_row = await (
                    await db.execute(
                        "SELECT * FROM workbench_events WHERE task_id=? AND event_id=?",
                        (
                            task_id,
                            self._strict_text(
                                row["created_by_event_id"], "branch creation event id"
                            ),
                        ),
                    )
                ).fetchone()
                if event_row is None:
                    raise LedgerCorruption(None, f"branch {current} lacks its creation event")
                creation_event = self._event_from_row(event_row)
            lineage.append(
                BranchTopology(
                    branch_id=current,
                    parent_branch_id=(
                        self._strict_text(row["parent_branch_id"], "branch parent id")
                        if row["parent_branch_id"] is not None
                        else None
                    ),
                    visible_through_sequence=cutoff,
                    forked_from_sequence=(
                        self._strict_integer(row["forked_from_sequence"], "branch fork sequence")
                        if row["forked_from_sequence"] is not None
                        else None
                    ),
                    forked_from_frame_version=(
                        self._strict_integer(
                            row["forked_from_frame_version"], "branch fork frame version"
                        )
                        if row["forked_from_frame_version"] is not None
                        else None
                    ),
                    creation_event=creation_event,
                )
            )
            if row["parent_branch_id"] is None:
                break
            cutoff = min(
                cutoff,
                self._strict_integer(row["forked_from_sequence"], "branch fork sequence"),
            )
            current = self._strict_text(row["parent_branch_id"], "branch parent id")
        ordered_lineage = tuple(reversed(lineage))
        rows: list[aiosqlite.Row] = []
        for topology_item in ordered_lineage:
            rows.extend(
                await (
                    await db.execute(
                        """
                        SELECT * FROM workbench_events
                        WHERE task_id=? AND branch_id=? AND sequence<=?
                        ORDER BY sequence
                        """,
                        (
                            task_id,
                            topology_item.branch_id,
                            topology_item.visible_through_sequence,
                        ),
                    )
                ).fetchall()
            )
        rows.sort(key=lambda row: self._strict_integer(row["sequence"], "event sequence"))
        return _StoreVisibleEvents(
            tuple(self._event_from_row(row) for row in rows),
            task_id,
            branch_id,
            at_sequence,
            ordered_lineage,
        )

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

    def _prepare_command(
        self,
        *,
        task_id: str,
        command_id: str,
        validated: Sequence[ValidatedDraft],
        target_branch_id: str,
        first_sequence: int,
        starting_frame_version: int,
        expected_frame_version: int,
        prior_checksum: str,
        created_at: str,
    ) -> tuple[tuple[WorkbenchEvent, ...], dict[str, Any]]:
        items = tuple(validated)
        if not items:
            raise EventValidationError("command manifest cannot be empty")
        event_ids = tuple(self._new_event_id() for _ in items)
        confirm_ordinals = tuple(
            ordinal
            for ordinal, item in enumerate(items, start=1)
            if item.definition.frame_effect is FrameEffect.CONFIRM
        )
        if len(confirm_ordinals) > 1:
            raise EventValidationError("Task 2 permits at most one confirmed frame event per command")
        frame_version = starting_frame_version
        checksum = prior_checksum
        events: list[WorkbenchEvent] = []
        for ordinal, (item, event_id) in enumerate(zip(items, event_ids), start=1):
            if item.definition.frame_effect is FrameEffect.CONFIRM:
                frame_version += 1
            event = self._build_event(
                task_id=task_id,
                command_id=command_id,
                command_sequence=ordinal,
                sequence=first_sequence + ordinal - 1,
                validated=item,
                frame_version=frame_version,
                prior_checksum=checksum,
                event_id=event_id,
                created_at=created_at,
            )
            events.append(event)
            checksum = event.checksum
        fields = {
            "task_id": task_id,
            "command_id": command_id,
            "target_branch_id": target_branch_id,
            "event_count": len(events),
            "first_sequence": first_sequence,
            "last_sequence": first_sequence + len(events) - 1,
            "first_event_id": events[0].event_id,
            "last_event_id": events[-1].event_id,
            "starting_frame_version": starting_frame_version,
            "expected_frame_version": expected_frame_version,
            "confirm_ordinal": confirm_ordinals[0] if confirm_ordinals else None,
            "drafts_checksum": drafts_checksum(task_id, command_id, items),
            "created_at": created_at,
        }
        return tuple(events), {**fields, "manifest_checksum": manifest_checksum(fields)}

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

    async def _insert_manifest(self, db: aiosqlite.Connection, manifest: Mapping[str, Any]) -> None:
        await db.execute(
            """
            INSERT INTO workbench_command_manifests(
                task_id,command_id,target_branch_id,event_count,first_sequence,last_sequence,
                first_event_id,last_event_id,starting_frame_version,expected_frame_version,
                confirm_ordinal,drafts_checksum,manifest_checksum,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                manifest["task_id"],
                manifest["command_id"],
                manifest["target_branch_id"],
                manifest["event_count"],
                manifest["first_sequence"],
                manifest["last_sequence"],
                manifest["first_event_id"],
                manifest["last_event_id"],
                manifest["starting_frame_version"],
                manifest["expected_frame_version"],
                manifest["confirm_ordinal"],
                manifest["drafts_checksum"],
                manifest["manifest_checksum"],
                manifest["created_at"],
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
        return (
            (
                self._strict_integer(row["sequence"], "event sequence"),
                self._strict_text(row["checksum"], "event checksum"),
            )
            if row
            else (0, GENESIS_CHECKSUM)
        )

    async def _require_task(self, db: aiosqlite.Connection, task_id: str) -> aiosqlite.Row:
        row = await (
            await db.execute("SELECT * FROM workbench_tasks WHERE id=?", (task_id,))
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"task not found: {task_id}")
        try:
            for column in (
                "id",
                "title",
                "state",
                "active_branch_id",
                "created_at",
                "updated_at",
            ):
                self._strict_text(row[column], f"task {column}")
            self._strict_integer(row["current_frame_version"], "task current frame version")
        except ValueError as exc:
            raise LedgerCorruption(None, str(exc)) from exc
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
        try:
            for column in ("task_id", "branch_id", "status", "created_at"):
                self._strict_text(row[column], f"branch {column}")
            self._strict_text(row["parent_branch_id"], "branch parent id", nullable=True)
            self._strict_text(row["created_by_event_id"], "branch creation event id", nullable=True)
            if row["forked_from_sequence"] is not None:
                self._strict_integer(row["forked_from_sequence"], "branch fork sequence")
            if row["forked_from_frame_version"] is not None:
                self._strict_integer(row["forked_from_frame_version"], "branch fork frame version")
        except ValueError as exc:
            raise LedgerCorruption(None, str(exc)) from exc
        return row

    def _event_from_row(self, row: aiosqlite.Row) -> WorkbenchEvent:
        return WorkbenchEvent(
            event_schema_version=self._strict_integer(
                row["event_schema_version"], "event schema version"
            ),
            event_id=self._strict_text(row["event_id"], "event id"),
            task_id=self._strict_text(row["task_id"], "event task id"),
            sequence=self._strict_integer(row["sequence"], "event sequence"),
            event_type=self._strict_text(row["event_type"], "event type"),
            actor=EventActor(
                kind=self._strict_text(row["actor_kind"], "event actor kind"),
                actor_id=self._strict_text(row["actor_id"], "event actor id"),
            ),
            branch_id=self._strict_text(row["branch_id"], "event branch id"),
            cause=(
                EventCause(self._strict_text(row["cause"], "event cause"))
                if row["cause"] is not None
                else None
            ),
            caused_by=self._strict_text(row["caused_by"], "event caused by", nullable=True),
            command_id=self._strict_text(row["command_id"], "event command id"),
            command_sequence=self._strict_integer(
                row["command_sequence"], "event command sequence"
            ),
            frame_version=self._strict_integer(row["frame_version"], "event frame version"),
            payload=json.loads(self._strict_text(row["payload_json"], "event payload json")),
            idempotency_key=self._strict_text(row["idempotency_key"], "event idempotency key"),
            prior_checksum=self._strict_text(row["prior_checksum"], "event prior checksum"),
            checksum=self._strict_text(row["checksum"], "event checksum"),
            created_at=self._strict_text(row["created_at"], "event created at"),
        )

    def _row_envelope(self, row: aiosqlite.Row, payload: Any) -> dict[str, Any]:
        return {
            "event_schema_version": self._strict_integer(
                row["event_schema_version"], "event schema version"
            ),
            "event_id": self._strict_text(row["event_id"], "event id"),
            "task_id": self._strict_text(row["task_id"], "event task id"),
            "sequence": self._strict_integer(row["sequence"], "event sequence"),
            "event_type": self._strict_text(row["event_type"], "event type"),
            "actor_kind": self._strict_text(row["actor_kind"], "event actor kind"),
            "actor_id": self._strict_text(row["actor_id"], "event actor id"),
            "branch_id": self._strict_text(row["branch_id"], "event branch id"),
            "cause": self._strict_text(row["cause"], "event cause", nullable=True),
            "caused_by": self._strict_text(
                row["caused_by"], "event caused by", nullable=True
            ),
            "command_id": self._strict_text(row["command_id"], "event command id"),
            "command_sequence": self._strict_integer(
                row["command_sequence"], "event command sequence"
            ),
            "frame_version": self._strict_integer(row["frame_version"], "event frame version"),
            "payload": payload,
            "idempotency_key": self._strict_text(
                row["idempotency_key"], "event idempotency key"
            ),
            "created_at": self._strict_text(row["created_at"], "event created at"),
            "prior_checksum": self._strict_text(
                row["prior_checksum"], "event prior checksum"
            ),
        }

    @staticmethod
    def _strict_integer(value: Any, name: str) -> int:
        if type(value) is not int:
            raise ValueError(f"{name} must use integer storage")
        return value

    @staticmethod
    def _strict_text(value: Any, name: str, *, nullable: bool = False) -> str | None:
        if value is None and nullable:
            return None
        if type(value) is not str:
            raise ValueError(f"{name} must use text storage")
        return value

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
