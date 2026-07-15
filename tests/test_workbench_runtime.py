from __future__ import annotations

import asyncio
import inspect
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from pydantic import BaseModel, ConfigDict

from orchestrator.config import Settings
from orchestrator.state.store import StateStore
from orchestrator.workbench.events import EventDefinition, EventRegistry, FrameEffect
from orchestrator.workbench.models import EventActor, EventDraft, LedgerCorruption
from orchestrator.workbench.projector import Projector
from orchestrator.workbench.store import WorkbenchStore


ROOT = Path(__file__).resolve().parents[1]
ACTOR = EventActor(kind="owner", actor_id="matt")


def runtime_api() -> tuple[Any, Any, Any, Any]:
    from orchestrator.workbench.runtime import (
        CORE_PARTICIPANT_ID,
        DomainAuthorityVerification,
        RuntimeConfigurationError,
        WorkbenchRuntime,
    )

    return (
        CORE_PARTICIPANT_ID,
        DomainAuthorityVerification,
        RuntimeConfigurationError,
        WorkbenchRuntime,
    )


def capability_api() -> tuple[Any, Any]:
    from orchestrator.workbench.runtime import (
        ParticipantDatabasePolicy,
        ParticipantTablePolicy,
    )

    return ParticipantDatabasePolicy, ParticipantTablePolicy


def settings_for(tmp_path: Path) -> Settings:
    home = tmp_path / "runtime"
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=ROOT,
    )


class FixturePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str


def fixture_reducer(state: dict[str, Any], event: Any) -> dict[str, Any]:
    return {**state, "fixture_value": event.payload["value"]}


def fixture_registry(*, owner: str = "fixture_domain") -> EventRegistry:
    registry = EventRegistry.production()
    registry.register(
        EventDefinition(
            event_type="fixture.domain_changed",
            event_schema_version=1,
            payload_model=FixturePayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=fixture_reducer,
            authority_participant=owner,
        )
    )
    return registry


async def prepare(settings: Settings) -> None:
    await StateStore(settings).initialize()
    async with aiosqlite.connect(settings.state_path) as db:
        await db.execute(
            "CREATE TABLE fixture_authority(task_id TEXT NOT NULL, event_id TEXT NOT NULL, value TEXT NOT NULL)"
        )
        await db.commit()


@dataclass
class FixtureParticipant:
    participant_id: str = "fixture_domain"
    event_types: frozenset[tuple[str, int]] = frozenset({("fixture.domain_changed", 1)})
    validate_error: BaseException | None = None
    apply_error: BaseException | None = None
    mutate_before_error: bool = False
    wrong_value: bool = False

    def __post_init__(self) -> None:
        self.validations: list[Any] = []
        self.verifications: list[tuple[Any, ...]] = []
        self.apply_count = 0
        self.verify_count = 0

    async def validate_command(self, db: aiosqlite.Connection, *, context: Any, drafts: tuple[Any, ...]) -> None:
        self.validations.append((context, drafts))
        if self.validate_error is not None:
            raise self.validate_error

    async def apply_command(
        self,
        db: aiosqlite.Connection,
        *,
        before: Any,
        events: tuple[Any, ...],
        after: Any,
    ) -> None:
        self.apply_count += 1
        if self.mutate_before_error or self.apply_error is None:
            for event in events:
                await db.insert(
                    "fixture_authority",
                    {
                        "event_id": event.event_id,
                        "value": "wrong" if self.wrong_value else event.payload["value"],
                    },
                )
        if self.apply_error is not None:
            raise self.apply_error

    async def verify_authority(self, db: aiosqlite.Connection, *, task_id: str, events: tuple[Any, ...]) -> Any:
        _, Verification, _, _ = runtime_api()
        self.verify_count += 1
        self.verifications.append(events)
        expected = [
            (event.event_id, event.payload["value"])
            for event in events
            if event.event_type == "fixture.domain_changed"
        ]
        rows = await db.fetch_all(
            "fixture_authority", columns=("event_id", "value")
        )
        actual = sorted((str(row["event_id"]), str(row["value"])) for row in rows)
        expected.sort()
        if actual != expected:
            return Verification(valid=False, participant_id=self.participant_id, reason=f"expected {expected}, got {actual}")
        return Verification(valid=True, participant_id=self.participant_id)


def domain_draft(value: str = "accepted") -> EventDraft:
    return EventDraft(
        event_type="fixture.domain_changed",
        actor=ACTOR,
        branch_id="main",
        payload={"value": value},
    )


def make_runtime(registry: EventRegistry, *participants: Any) -> Any:
    _, _, _, Runtime = runtime_api()
    DatabasePolicy, TablePolicy = capability_api()
    policies = []
    for participant in participants:
        tables = ()
        if participant.participant_id == "fixture_domain":
            tables = (
                TablePolicy(
                    table_name="fixture_authority",
                    readable_columns=frozenset({"task_id", "event_id", "value"}),
                    insertable_columns=frozenset({"event_id", "value"}),
                    updatable_columns=frozenset({"value"}),
                ),
            )
        policies.append(DatabasePolicy(participant_id=participant.participant_id, tables=tables))
    return Runtime(
        registry=registry,
        participants=tuple(participants),
        database_policies=tuple(policies),
    )


def test_runtime_construction_enforces_exact_complete_non_core_ownership() -> None:
    core, _, ConfigurationError, Runtime = runtime_api()
    registry = fixture_registry()
    participant = FixtureParticipant()
    runtime = make_runtime(registry, participant)
    assert runtime.registry is registry
    assert runtime.participants == (participant,)
    assert core == "core"

    with pytest.raises(ConfigurationError, match="missing.*fixture.domain_changed"):
        Runtime(registry=registry, participants=())
    with pytest.raises(ConfigurationError, match="duplicate participant_id"):
        Runtime(registry=registry, participants=(participant, FixtureParticipant()))
    with pytest.raises(ConfigurationError, match="owned by more than one"):
        Runtime(
            registry=registry,
            participants=(
                participant,
                FixtureParticipant(participant_id="other"),
            ),
        )
    with pytest.raises(ConfigurationError, match="unregistered event"):
        Runtime(
            registry=registry,
            participants=(
                participant,
                FixtureParticipant(
                    participant_id="other", event_types=frozenset({("fixture.unknown", 1)})
                ),
            ),
        )
    with pytest.raises(ConfigurationError, match="reserved"):
        Runtime(
            registry=registry,
            participants=(
                FixtureParticipant(
                    participant_id=core,
                    event_types=frozenset({("task.created", 1), ("branch.forked", 1)}),
                ),
                participant,
            ),
        )
    with pytest.raises(ConfigurationError, match="declares fixture_domain"):
        Runtime(
            registry=registry,
            participants=(FixtureParticipant(participant_id="wrong"),),
        )
    with pytest.raises(ConfigurationError, match="reserved core event"):
        Runtime(
            registry=registry,
            participants=(
                participant,
                FixtureParticipant(
                    participant_id="other", event_types=frozenset({("task.created", 1)})
                ),
            ),
        )


def test_runtime_rejects_mutated_participant_ownership_and_mixed_owner_command() -> None:
    _, _, ConfigurationError, _ = runtime_api()
    registry = fixture_registry()
    registry.register(
        EventDefinition(
            event_type="fixture.other",
            event_schema_version=1,
            payload_model=FixturePayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=fixture_reducer,
            authority_participant="other",
        )
    )
    first = FixtureParticipant()
    second = FixtureParticipant(participant_id="other", event_types=frozenset({("fixture.other", 1)}))
    runtime = make_runtime(registry, first, second)
    mixed = (
        registry.validate(domain_draft()),
        registry.validate(EventDraft(event_type="fixture.other", actor=ACTOR, payload={"value": "x"})),
    )
    with pytest.raises(ConfigurationError, match="mix multiple"):
        runtime.participant_for(mixed)
    first.participant_id = "changed"
    with pytest.raises(ConfigurationError, match="ownership changed|declares"):
        runtime.validate_current()


def test_runtime_rejects_malformed_participant_shapes() -> None:
    _, _, ConfigurationError, Runtime = runtime_api()
    registry = fixture_registry()
    for participant in (
        FixtureParticipant(participant_id=""),
        FixtureParticipant(event_types=set({("fixture.domain_changed", 1)})),
        FixtureParticipant(event_types=frozenset()),
        FixtureParticipant(event_types=frozenset({("", 1)})),
        FixtureParticipant(event_types=frozenset({("fixture.domain_changed", 0)})),
    ):
        with pytest.raises(ConfigurationError):
            Runtime(registry=registry, participants=(participant,))
    with pytest.raises(ConfigurationError):
        Runtime(registry=registry, participants=[FixtureParticipant()])


def test_runtime_rejects_mutable_database_policy_shapes_and_detects_replacement() -> None:
    _, _, ConfigurationError, Runtime = runtime_api()
    DatabasePolicy, TablePolicy = capability_api()
    registry = fixture_registry()
    participant = FixtureParticipant()
    valid_table = TablePolicy(
        table_name="fixture_authority",
        readable_columns=frozenset({"task_id", "event_id", "value"}),
        insertable_columns=frozenset({"event_id", "value"}),
        updatable_columns=frozenset({"value"}),
    )
    with pytest.raises(ConfigurationError, match="tables.*tuple"):
        Runtime(
            registry=registry,
            participants=(participant,),
            database_policies=(
                DatabasePolicy(participant_id="fixture_domain", tables=[valid_table]),
            ),
        )
    mutable_columns = TablePolicy(
        table_name="fixture_authority",
        readable_columns={"task_id", "event_id", "value"},
        insertable_columns=frozenset({"event_id", "value"}),
        updatable_columns=frozenset({"value"}),
    )
    with pytest.raises(ConfigurationError, match="columns.*frozenset"):
        Runtime(
            registry=registry,
            participants=(participant,),
            database_policies=(
                DatabasePolicy(participant_id="fixture_domain", tables=(mutable_columns,)),
            ),
        )


def test_runtime_rejects_case_variant_core_tables_and_duplicate_table_ownership() -> None:
    _, _, ConfigurationError, Runtime = runtime_api()
    DatabasePolicy, TablePolicy = capability_api()
    registry = fixture_registry()
    participant = FixtureParticipant()
    core_alias = TablePolicy(
        table_name="WORKBENCH_EVENTS",
        readable_columns=frozenset({"task_id", "event_id"}),
        global_read=True,
    )
    with pytest.raises(ConfigurationError, match="reserved"):
        Runtime(
            registry=registry,
            participants=(participant,),
            database_policies=(
                DatabasePolicy(participant_id="fixture_domain", tables=(core_alias,)),
            ),
        )
    registry.register(
        EventDefinition(
            event_type="fixture.observer",
            event_schema_version=1,
            payload_model=FixturePayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=fixture_reducer,
            authority_participant="observer",
        )
    )
    observer = FixtureParticipant(
        participant_id="observer", event_types=frozenset({("fixture.observer", 1)})
    )
    table = TablePolicy(
        table_name="fixture_authority",
        readable_columns=frozenset({"task_id", "event_id", "value"}),
    )
    alias = TablePolicy(
        table_name="FIXTURE_AUTHORITY",
        readable_columns=frozenset({"task_id", "event_id", "value"}),
    )
    with pytest.raises(ConfigurationError, match="owned by both"):
        Runtime(
            registry=registry,
            participants=(participant, observer),
            database_policies=(
                DatabasePolicy(participant_id="fixture_domain", tables=(table,)),
                DatabasePolicy(participant_id="observer", tables=(alias,)),
            ),
        )


def test_runtime_requires_literal_immutable_task_id_scope_column() -> None:
    _, _, ConfigurationError, Runtime = runtime_api()
    DatabasePolicy, TablePolicy = capability_api()
    registry = fixture_registry()
    participant = FixtureParticipant()
    for table in (
        TablePolicy(
            table_name="fixture_authority",
            readable_columns=frozenset({"task_id", "event_id", "value"}),
            updatable_columns=frozenset({"task_id"}),
            task_id_column="value",
        ),
        TablePolicy(
            table_name="fixture_authority",
            readable_columns=frozenset({"task_id", "event_id", "value"}),
            insertable_columns=frozenset({"task_id", "event_id"}),
        ),
        TablePolicy(
            table_name="fixture_authority",
            readable_columns=frozenset({"task_id", "event_id", "value"}),
            updatable_columns=frozenset({"task_id"}),
        ),
    ):
        with pytest.raises(ConfigurationError, match="task_id"):
            Runtime(
                registry=registry,
                participants=(participant,),
                database_policies=(
                    DatabasePolicy(participant_id="fixture_domain", tables=(table,)),
                ),
            )

def test_store_requires_runtime_registry_identity_and_exposes_no_sql_callback(tmp_path: Path) -> None:
    _, _, ConfigurationError, _ = runtime_api()
    registry = fixture_registry()
    runtime = make_runtime(registry, FixtureParticipant())
    store = WorkbenchStore(settings_for(tmp_path), runtime=runtime)
    assert store.registry is registry
    with pytest.raises(ConfigurationError, match="registry identity"):
        WorkbenchStore(settings_for(tmp_path), registry=registry.copy(), runtime=runtime)
    assert "callback" not in inspect.signature(WorkbenchStore.append_batch).parameters


@pytest.mark.asyncio
async def test_validate_command_sees_preallocation_snapshot_and_global_selection_tokens(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "domain")).__next__)
    created = await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    await store.append("task-1", "cmd-domain", domain_draft())

    context, drafts = participant.validations[-1]
    assert context.task_id == "task-1"
    assert context.branch_id == "main"
    assert context.before.head.head_sequence == created.sequence
    assert context.before.state["task"]["title"] == "Intent"
    assert context.global_head_sequence == created.sequence
    assert context.global_head_checksum == created.checksum
    assert context.selected_branch_id == "main"
    assert context.selected_branch_frame_version == 0
    assert drafts[0].payload.value == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["validate", "apply"])
@pytest.mark.parametrize("failure", [RuntimeError("participant failed"), asyncio.CancelledError()])
async def test_participant_failure_or_cancellation_rolls_back_every_command_surface(
    tmp_path: Path, phase: str, failure: BaseException
) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant(
        validate_error=failure if phase == "validate" else None,
        apply_error=failure if phase == "apply" else None,
        mutate_before_error=phase == "apply",
    )
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "domain", "recovery")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)

    async with aiosqlite.connect(settings.state_path) as db:
        before_projection = await (
            await db.execute("SELECT * FROM projection_metadata WHERE task_id='task-1'")
        ).fetchall()
    with pytest.raises(type(failure), match=None if isinstance(failure, asyncio.CancelledError) else "participant failed"):
        await store.append("task-1", "cmd-fail", domain_draft())

    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (1,)
        assert await (await db.execute("SELECT count(*) FROM workbench_command_manifests WHERE task_id='task-1'")).fetchone() == (1,)
        assert await (await db.execute("SELECT count(*) FROM fixture_authority")).fetchone() == (0,)
        after_projection = await (
            await db.execute("SELECT * FROM projection_metadata WHERE task_id='task-1'")
        ).fetchall()
        assert [tuple(row) for row in after_projection] == [tuple(row) for row in before_projection]

    participant.validate_error = None
    participant.apply_error = None
    recovered = await store.append("task-1", "cmd-recovery", domain_draft("recovered"))
    assert recovered.sequence == 2


@pytest.mark.asyncio
async def test_post_apply_verifier_failure_rolls_back_mutation_and_exact_retry_never_reapplies(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant(wrong_value=True)
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "bad", "good")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    with pytest.raises(LedgerCorruption, match="fixture_domain"):
        await store.append("task-1", "cmd-bad", domain_draft("right"))
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM fixture_authority")).fetchone() == (0,)

    participant.wrong_value = False
    event = await store.append("task-1", "cmd-good", domain_draft("right"))
    applied = participant.apply_count
    assert await store.append("task-1", "cmd-good", domain_draft("right")) == event
    assert participant.apply_count == applied


@pytest.mark.asyncio
async def test_domain_verifier_blocks_retry_append_snapshot_and_rebuild_before_laundering(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "domain", "new")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    event = await store.append("task-1", "cmd-domain", domain_draft())
    async with aiosqlite.connect(settings.state_path) as db:
        await db.execute("UPDATE fixture_authority SET value='laundered' WHERE event_id=?", (event.event_id,))
        await db.commit()

    calls = (
        lambda: store.append("task-1", "cmd-domain", domain_draft()),
        lambda: store.append("task-1", "cmd-new", domain_draft("new")),
        lambda: store.snapshot("task-1"),
        lambda: Projector(registry).rebuild(store, "task-1"),
    )
    for call in calls:
        with pytest.raises(LedgerCorruption, match="fixture_domain"):
            await call()

    async with aiosqlite.connect(settings.state_path) as db:
        row = await (await db.execute("SELECT value FROM fixture_authority WHERE event_id=?", (event.event_id,))).fetchone()
        assert row == ("laundered",)
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (2,)


@pytest.mark.asyncio
async def test_runtime_detects_registry_mutation_as_omitted_participant_before_allocation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant()
    runtime = make_runtime(registry, participant)
    store = WorkbenchStore(settings, runtime=runtime, id_factory=iter(("create", "never")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    omitted = EventDefinition(
        event_type="fixture.omitted",
        event_schema_version=1,
        payload_model=FixturePayload,
        frame_effect=FrameEffect.INHERIT,
        reducer=fixture_reducer,
        authority_participant="missing_participant",
    )
    with pytest.raises(Exception, match="frozen|registry"):
        registry.register(omitted)
    replacement = EventDefinition(
        event_type="fixture.domain_changed",
        event_schema_version=1,
        payload_model=FixturePayload,
        frame_effect=FrameEffect.INHERIT,
        reducer=lambda state, event: {**state, "fixture_value": "EVIL"},
        authority_participant="fixture_domain",
    )
    with pytest.raises(TypeError):
        registry._definitions[("fixture.domain_changed", 1)] = replacement
    await store.append("task-1", "cmd-good", domain_draft("good"))
    replaced = dict(registry._definitions)
    replaced[("fixture.domain_changed", 1)] = replacement
    registry._definitions = replaced
    with pytest.raises(Exception, match="registry|semantic|changed"):
        await Projector(registry).rebuild(store, "task-1")


@pytest.mark.asyncio
async def test_core_create_and_fork_run_all_verifiers_but_never_apply_participant(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "fork")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    assert participant.verify_count > 0
    assert participant.apply_count == 0
    before_verify = participant.verify_count
    await store.fork_branch("task-1", "cmd-fork", "main", "child", 1, ACTOR)
    assert participant.verify_count > before_verify
    assert participant.apply_count == 0


@pytest.mark.asyncio
async def test_child_validation_context_is_ancestry_local_but_global_head_is_task_global(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant()
    store = WorkbenchStore(
        settings,
        runtime=make_runtime(registry, participant),
        id_factory=iter(("create", "fork", "parent", "child")).__next__,
    )
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    await store.fork_branch("task-1", "cmd-fork", "main", "child", 1, ACTOR)
    parent = await store.append("task-1", "cmd-parent", domain_draft("parent"))
    await store.append(
        "task-1",
        "cmd-child",
        domain_draft("child").model_copy(update={"branch_id": "child"}),
    )
    context, _ = participant.validations[-1]
    assert context.before.branch_id == "child"
    assert context.before.head.head_sequence == 1
    assert context.global_head_sequence == parent.sequence
    assert context.global_head_checksum == parent.checksum
    assert context.selected_branch_id == "main"
    assert [event.sequence for event in participant.verifications[-1]] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_failed_validation_happens_before_event_id_or_command_clock_allocation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant(validate_error=RuntimeError("stale"))
    ids = iter(("create",))
    clock_calls = 0

    def clock() -> Any:
        nonlocal clock_calls
        clock_calls += 1
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=ids.__next__, clock=clock)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    clock_calls = 0
    with pytest.raises(RuntimeError, match="stale"):
        await store.append("task-1", "cmd-stale", domain_draft())
    assert clock_calls == 0


@pytest.mark.asyncio
async def test_apply_observes_inserted_ledger_and_exact_hypothetical_snapshots_before_projection(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class Inspector(FixtureParticipant):
        async def apply_command(self, db: Any, *, before: Any, events: tuple[Any, ...], after: Any) -> None:
            assert not hasattr(db, "execute")
            assert not hasattr(db, "commit")
            assert not hasattr(db, "_conn")
            assert not hasattr(db, "_ParticipantDatabase__db")
            assert before.head.head_sequence == 1
            assert after.head.head_sequence == 2
            await super().apply_command(db, before=before, events=events, after=after)

    participant = Inspector()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "event")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    await store.append("task-1", "cmd-domain", domain_draft())
    assert participant.apply_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["validate", "apply"])
async def test_callback_nested_argument_mutation_is_rejected_and_rolled_back(tmp_path: Path, phase: str) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class Mutator(FixtureParticipant):
        async def validate_command(self, db: Any, *, context: Any, drafts: tuple[Any, ...]) -> None:
            if phase == "validate":
                context.before.state["task"]["title"] = "mutated"
            await super().validate_command(db, context=context, drafts=drafts)

        async def apply_command(self, db: Any, *, before: Any, events: tuple[Any, ...], after: Any) -> None:
            await super().apply_command(db, before=before, events=events, after=after)
            if phase == "apply":
                events[0].payload["value"] = "mutated"

    participant = Mutator()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "event")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    with pytest.raises(LedgerCorruption, match="mutated .* arguments"):
        await store.append("task-1", "cmd-mutate", domain_draft())
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM fixture_authority")).fetchone() == (0,)
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_callback_cannot_commit_or_rollback_store_transaction(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class Committer(FixtureParticipant):
        async def validate_command(self, db: Any, *, context: Any, drafts: tuple[Any, ...]) -> None:
            await db.commit()

    participant = Committer()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "event")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    with pytest.raises(Exception, match="transaction|commit|authoriz"):
        await store.append("task-1", "cmd-commit", domain_draft())
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (1,)


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", ["COMMIT", "ROLLBACK"])
async def test_callback_cannot_execute_transaction_control_sql(tmp_path: Path, sql: str) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class Controller(FixtureParticipant):
        async def apply_command(self, db: Any, *, before: Any, events: tuple[Any, ...], after: Any) -> None:
            await db.execute(sql)

    participant = Controller()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create", "event")).__next__)
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    with pytest.raises((sqlite3.DatabaseError, AttributeError), match="authoriz|attribute|execute"):
        await store.append("task-1", "cmd-control", domain_draft())
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_verifier_is_read_only_and_cannot_commit_laundered_authority(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class LaunderingVerifier(FixtureParticipant):
        async def verify_authority(self, db: Any, *, task_id: str, events: tuple[Any, ...]) -> Any:
            _, Verification, _, _ = runtime_api()
            if events:
                await db.execute(
                    "INSERT INTO fixture_authority(task_id,event_id,value) VALUES (?,?,?)",
                    (task_id, "forged", "forged"),
                )
                await db.commit()
            return Verification(valid=True, participant_id=self.participant_id)

    participant = LaunderingVerifier()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create",)).__next__)
    with pytest.raises((LedgerCorruption, sqlite3.DatabaseError, AttributeError), match="authoriz|read.only|prohibited|attribute|execute"):
        await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM fixture_authority")).fetchone() == (0,)
        assert await (await db.execute("SELECT count(*) FROM workbench_events")).fetchone() == (0,)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wrong_id", "exception", "cancel"])
async def test_verifier_wrong_identity_exception_or_cancellation_fails_closed(tmp_path: Path, mode: str) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class BadVerifier(FixtureParticipant):
        async def verify_authority(self, db: Any, *, task_id: str, events: tuple[Any, ...]) -> Any:
            _, Verification, _, _ = runtime_api()
            if events:
                if mode == "wrong_id":
                    return Verification(valid=True, participant_id="other")
                if mode == "exception":
                    raise RuntimeError("verifier exploded")
                raise asyncio.CancelledError("first-cancel")
            return Verification(valid=True, participant_id=self.participant_id)

    participant = BadVerifier()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant), id_factory=iter(("create",)).__next__)
    expected = asyncio.CancelledError if mode == "cancel" else LedgerCorruption
    with pytest.raises(expected) as raised:
        await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    if mode == "cancel":
        assert raised.value.args == ("first-cancel",)
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (0,)


@pytest.mark.asyncio
async def test_repeated_cancellation_during_rollback_preserves_first_cancellation_and_drains_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant(
        apply_error=asyncio.CancelledError("first-cancellation"),
        mutate_before_error=True,
    )
    store = WorkbenchStore(
        settings,
        runtime=make_runtime(registry, participant),
        id_factory=iter(("create", "cancel", "recovery")).__next__,
    )
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    rollback_entered = asyncio.Event()
    rollback_release = asyncio.Event()
    rollback_finished: list[bool] = []
    original_rollback = aiosqlite.Connection.rollback

    async def paused_rollback(db: aiosqlite.Connection) -> None:
        rollback_entered.set()
        await rollback_release.wait()
        await original_rollback(db)
        rollback_finished.append(not db.in_transaction)

    monkeypatch.setattr(aiosqlite.Connection, "rollback", paused_rollback)
    command = asyncio.create_task(store.append("task-1", "cmd-cancel", domain_draft()))
    await rollback_entered.wait()
    command.cancel("second-cancellation")
    rollback_release.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await command
    assert raised.value.args == ("first-cancellation",)
    assert rollback_finished == [True]

    monkeypatch.setattr(aiosqlite.Connection, "rollback", original_rollback)
    participant.apply_error = None
    recovered = await store.append("task-1", "cmd-recovery", domain_draft("recovered"))
    assert recovered.sequence == 2
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM fixture_authority")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_repeated_cancellation_during_authorizer_cleanup_preserves_first_cancellation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, FixtureParticipant()))
    db = await aiosqlite.connect(settings.state_path, isolation_level=None)
    try:
        await db.execute("BEGIN IMMEDIATE")
        body_entered = asyncio.Event()
        body_release = asyncio.Event()
        clear_entered = asyncio.Event()
        clear_release = asyncio.Event()
        original_clear = store._clear_participant_authorizer

        async def paused_clear(connection: Any) -> None:
            clear_entered.set()
            await clear_release.wait()
            await original_clear(connection)

        store._clear_participant_authorizer = paused_clear  # type: ignore[method-assign]

        async def guarded() -> None:
            async with store._participant_database_guard(
                db,
                policy=store.runtime.database_policy_for("fixture_domain"),
                phase="verify",
            ):
                body_entered.set()
                await body_release.wait()

        task = asyncio.create_task(guarded())
        try:
            await asyncio.wait_for(body_entered.wait(), timeout=2)
            task.cancel("first-cancellation")
            await asyncio.wait_for(clear_entered.wait(), timeout=2)
            task.cancel("second-cancellation")
            clear_release.set()
            with pytest.raises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(task, timeout=2)
        finally:
            body_release.set()
            clear_release.set()
            if not task.done():
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            store._clear_participant_authorizer = original_clear  # type: ignore[method-assign]
        assert raised.value.args == ("first-cancellation",)
    finally:
        if db.in_transaction:
            await db.rollback()
        await db.close()


@pytest.mark.asyncio
async def test_authorizer_cleanup_failure_preserves_body_cancellation_and_poisoned_connection_is_discarded(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant))
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    original_clear = store._clear_participant_authorizer
    clear_error = RuntimeError("authorizer clear failed")
    clear_calls = 0

    async def failing_clear(connection: Any) -> None:
        nonlocal clear_calls
        clear_calls += 1
        raise clear_error

    store._clear_participant_authorizer = failing_clear  # type: ignore[method-assign]
    db = await aiosqlite.connect(settings.state_path, isolation_level=None)
    try:
        body_entered = asyncio.Event()

        async def guarded() -> None:
            async with store._participant_database_guard(
                db,
                policy=store.runtime.database_policy_for("fixture_domain"),
                phase="verify",
            ):
                body_entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(guarded())
        await asyncio.wait_for(body_entered.wait(), timeout=2)
        task.cancel("body-cancellation")
        with pytest.raises(asyncio.CancelledError) as raised:
            await asyncio.wait_for(task, timeout=2)
        assert raised.value.args == ("body-cancellation",)
        assert raised.value.__cause__ is clear_error
        assert clear_calls == 1
    finally:
        store._clear_participant_authorizer = original_clear  # type: ignore[method-assign]
        if db.in_transaction:
            await db.rollback()
        await db.close()

    # The failed guarded connection is never returned to the store's pool; a
    # fresh store operation must still work after the cleanup error.
    recovered = await store.append("task-1", "cmd-recovery", domain_draft("recovered"))
    assert recovered.sequence == 2


@pytest.mark.asyncio
async def test_verifier_cannot_mutate_event_copy_to_launder_wrong_domain_row(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class MutatingVerifier(FixtureParticipant):
        mutate = False

        async def verify_authority(self, db: Any, *, task_id: str, events: tuple[Any, ...]) -> Any:
            if self.mutate:
                for event in events:
                    if event.event_type == "fixture.domain_changed":
                        event.payload["value"] = "forged"
            return await super().verify_authority(db, task_id=task_id, events=events)

    participant = MutatingVerifier()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant))
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    event = await store.append("task-1", "cmd-domain", domain_draft("accepted"))
    async with aiosqlite.connect(settings.state_path) as db:
        await db.execute("UPDATE fixture_authority SET value='forged' WHERE event_id=?", (event.event_id,))
        await db.commit()
    participant.mutate = True
    with pytest.raises(LedgerCorruption, match="mutated.*verifier|domain participant"):
        await store.snapshot("task-1")


@pytest.mark.asyncio
async def test_verifier_event_arguments_are_isolated_between_participants(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    registry.register(
        EventDefinition(
            event_type="fixture.observer",
            event_schema_version=1,
            payload_model=FixturePayload,
            frame_effect=FrameEffect.INHERIT,
            reducer=fixture_reducer,
            authority_participant="observer",
        )
    )

    class Mutator(FixtureParticipant):
        async def verify_authority(self, db: Any, *, task_id: str, events: tuple[Any, ...]) -> Any:
            _, Verification, _, _ = runtime_api()
            if events:
                events[0].payload["title"] = "laundered"
            return Verification(valid=True, participant_id=self.participant_id)

    class Observer(FixtureParticipant):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.seen_titles: list[str] = []

        async def verify_authority(self, db: Any, *, task_id: str, events: tuple[Any, ...]) -> Any:
            _, Verification, _, _ = runtime_api()
            if events:
                self.seen_titles.append(events[0].payload["title"])
            return Verification(valid=True, participant_id=self.participant_id)

    observer = Observer(
        participant_id="observer", event_types=frozenset({("fixture.observer", 1)})
    )
    store = WorkbenchStore(
        settings, runtime=make_runtime(registry, Mutator(), observer)
    )
    with pytest.raises(LedgerCorruption, match="mutated.*verifier"):
        await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    assert observer.seen_titles in ([], ["Intent"])


@pytest.mark.asyncio
async def test_participant_cannot_escape_facade_and_commit_partial_authority(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class EscapeParticipant(FixtureParticipant):
        async def validate_command(self, db: Any, *, context: Any, drafts: tuple[Any, ...]) -> None:
            raw = object.__getattribute__(db, "_ParticipantDatabase__db")
            await raw._execute(raw._conn.set_authorizer, None)
            await raw.execute(
                "INSERT INTO fixture_authority(task_id,event_id,value) VALUES (?,?,?)",
                (context.task_id, "escaped", "escaped"),
            )
            await raw.commit()
            raise RuntimeError("after committed escape")

    participant = EscapeParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant))
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    with pytest.raises(Exception, match="capability|attribute|raw|escape"):
        await store.append("task-1", "cmd-escape", domain_draft())
    async with aiosqlite.connect(settings.state_path) as db:
        assert await (await db.execute("SELECT count(*) FROM fixture_authority")).fetchone() == (0,)
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_apply_cannot_mutate_another_tasks_domain_rows(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class CrossTaskParticipant(FixtureParticipant):
        attack = False

        async def apply_command(self, db: Any, *, before: Any, events: tuple[Any, ...], after: Any) -> None:
            if self.attack:
                await db.update(
                    "fixture_authority",
                    {"value": "corrupt"},
                    equals={"task_id": "task-2"},
                )
            await super().apply_command(db, before=before, events=events, after=after)

    participant = CrossTaskParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant))
    await store.create_task("task-2", "Other", "cmd-create-2", ACTOR)
    await store.append("task-2", "cmd-domain-2", domain_draft("task-two"))
    await store.create_task("task-1", "Intent", "cmd-create-1", ACTOR)
    participant.attack = True
    with pytest.raises(Exception, match="task.scope|cross.task|capability|prohibited"):
        await store.append("task-1", "cmd-attack", domain_draft("task-one"))
    async with aiosqlite.connect(settings.state_path) as db:
        row = await (await db.execute("SELECT value FROM fixture_authority WHERE task_id='task-2'")).fetchone()
        assert row == ("task-two",)


@pytest.mark.asyncio
async def test_store_rejects_deceptive_string_in_forged_cross_task_mutation_plan(tmp_path: Path) -> None:
    from orchestrator.workbench.runtime import ParticipantUpdate

    class DeceptiveTaskId(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        __hash__ = str.__hash__

    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class ForgingParticipant(FixtureParticipant):
        attack = False

        async def apply_command(self, db: Any, *, before: Any, events: tuple[Any, ...], after: Any) -> None:
            await super().apply_command(db, before=before, events=events, after=after)
            if self.attack:
                mutations = object.__getattribute__(db, "_ParticipantDatabase__mutations")
                mutations.append(
                    ParticipantUpdate(
                        table_name="fixture_authority",
                        values={"value": "corrupt"},
                        equals={
                            "task_id": DeceptiveTaskId("task-2"),
                            "event_id": "seed",
                        },
                    )
                )

    participant = ForgingParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant))
    await store.create_task("task-2", "Other", "cmd-create-2", ACTOR)
    seeded = await store.append("task-2", "cmd-domain-2", domain_draft("task-two"))
    async with aiosqlite.connect(settings.state_path) as db:
        await db.execute(
            "UPDATE fixture_authority SET event_id='seed' WHERE event_id=?", (seeded.event_id,)
        )
        await db.commit()
    await store.create_task("task-1", "Intent", "cmd-create-1", ACTOR)
    participant.attack = True
    with pytest.raises(Exception, match="task|scope|string|mutation"):
        await store.append("task-1", "cmd-forged", domain_draft("task-one"))
    async with aiosqlite.connect(settings.state_path) as db:
        row = await (
            await db.execute(
                "SELECT value FROM fixture_authority WHERE task_id='task-2' AND event_id='seed'"
            )
        ).fetchone()
        assert row == ("task-two",)


@pytest.mark.asyncio
@pytest.mark.parametrize("statement", ["SAVEPOINT participant_escape", "PRAGMA user_version=77"])
async def test_apply_denies_savepoints_and_mutating_pragmas(tmp_path: Path, statement: str) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class EscapeParticipant(FixtureParticipant):
        async def apply_command(self, db: Any, *, before: Any, events: tuple[Any, ...], after: Any) -> None:
            await db.execute(statement)

    store = WorkbenchStore(
        settings, runtime=make_runtime(registry, EscapeParticipant())
    )
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    with pytest.raises(Exception, match="authoriz|prohibited|capability|attribute|execute"):
        await store.append("task-1", "cmd-escape", domain_draft())
    async with aiosqlite.connect(settings.state_path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        assert version == (0,)
        assert await (await db.execute("SELECT count(*) FROM workbench_events WHERE task_id='task-1'")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_cancellation_after_authorizer_install_clears_guard_and_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    participant = FixtureParticipant()
    store = WorkbenchStore(settings, runtime=make_runtime(registry, participant))
    await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    installed = asyncio.Event()
    release = asyncio.Event()
    original_execute = aiosqlite.Connection._execute
    intercepted = False

    async def intercept(self: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal intercepted
        if (
            not intercepted
            and getattr(fn, "__name__", "") == "set_authorizer"
            and args
            and args[0] is not None
        ):
            intercepted = True
            result = await original_execute(self, fn, *args, **kwargs)
            installed.set()
            await release.wait()
            return result
        return await original_execute(self, fn, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "_execute", intercept)
    command = asyncio.create_task(store.append("task-1", "cmd-cancel", domain_draft()))
    await asyncio.wait_for(installed.wait(), timeout=2)
    command.cancel("install-cancellation")
    release.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await asyncio.wait_for(command, timeout=3)
    assert raised.value.args == ("install-cancellation",)
    monkeypatch.setattr(aiosqlite.Connection, "_execute", original_execute)
    recovered = await asyncio.wait_for(
        store.append("task-1", "cmd-recovery", domain_draft("recovered")), timeout=3
    )
    assert recovered.sequence == 2


@pytest.mark.asyncio
async def test_capability_rows_are_immutable_and_global_read_is_verify_only(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()
    _, _, _, Runtime = runtime_api()
    DatabasePolicy, TablePolicy = capability_api()

    class GlobalParticipant(FixtureParticipant):
        try_global_in_validate = False

        def __post_init__(self) -> None:
            super().__post_init__()
            self.global_rows: tuple[Any, ...] = ()

        async def validate_command(self, db: Any, *, context: Any, drafts: tuple[Any, ...]) -> None:
            if self.try_global_in_validate:
                await db.fetch_global(
                    "fixture_authority", columns=("task_id", "event_id", "value")
                )

        async def verify_authority(self, db: Any, *, task_id: str, events: tuple[Any, ...]) -> Any:
            self.global_rows = await db.fetch_global(
                "fixture_authority",
                columns=("task_id", "event_id", "value"),
                order_by=("task_id", "event_id"),
            )
            return await super().verify_authority(db, task_id=task_id, events=events)

    participant = GlobalParticipant()
    runtime = Runtime(
        registry=registry,
        participants=(participant,),
        database_policies=(
            DatabasePolicy(
                participant_id="fixture_domain",
                tables=(
                    TablePolicy(
                        table_name="fixture_authority",
                        readable_columns=frozenset({"task_id", "event_id", "value"}),
                        insertable_columns=frozenset({"event_id", "value"}),
                        updatable_columns=frozenset({"value"}),
                        global_read=True,
                    ),
                ),
            ),
        ),
    )
    store = WorkbenchStore(settings, runtime=runtime)
    await store.create_task("task-2", "Other", "cmd-create-2", ACTOR)
    await store.append("task-2", "cmd-domain-2", domain_draft("two"))
    await store.create_task("task-1", "Intent", "cmd-create-1", ACTOR)
    await store.append("task-1", "cmd-domain-1", domain_draft("one"))
    assert {row["task_id"] for row in participant.global_rows} == {"task-1", "task-2"}
    with pytest.raises(TypeError):
        participant.global_rows[0]["value"] = "mutated"
    participant.try_global_in_validate = True
    with pytest.raises(Exception, match="global read.*phase"):
        await store.append("task-1", "cmd-global-validate", domain_draft("blocked"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "match"),
    [
        ("undeclared_table", "outside participant capability"),
        ("undeclared_column", "outside insert capability"),
        ("identifier_injection", "outside participant capability"),
        ("verify_write", "writes are available only during apply"),
    ],
)
async def test_capability_rejects_undeclared_identifiers_and_wrong_phase_writes(
    tmp_path: Path, operation: str, match: str
) -> None:
    settings = settings_for(tmp_path)
    await prepare(settings)
    registry = fixture_registry()

    class RestrictedParticipant(FixtureParticipant):
        async def apply_command(self, db: Any, *, before: Any, events: tuple[Any, ...], after: Any) -> None:
            if operation == "undeclared_table":
                await db.insert("workbench_events", {"event_id": "x"})
            if operation == "undeclared_column":
                await db.insert("fixture_authority", {"event_id": "x", "unknown": "x"})
            if operation == "identifier_injection":
                await db.insert("fixture_authority; DROP TABLE workbench_events", {"event_id": "x"})
            await super().apply_command(db, before=before, events=events, after=after)

        async def verify_authority(self, db: Any, *, task_id: str, events: tuple[Any, ...]) -> Any:
            if operation == "verify_write" and events:
                await db.insert("fixture_authority", {"event_id": "x", "value": "x"})
            return await super().verify_authority(db, task_id=task_id, events=events)

    store = WorkbenchStore(settings, runtime=make_runtime(registry, RestrictedParticipant()))
    if operation == "verify_write":
        with pytest.raises(LedgerCorruption, match=match):
            await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
    else:
        await store.create_task("task-1", "Intent", "cmd-create", ACTOR)
        with pytest.raises(Exception, match=match):
            await store.append("task-1", "cmd-restricted", domain_draft())
    async with aiosqlite.connect(settings.state_path) as db:
        tables = await (
            await db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name='workbench_events'")
        ).fetchall()
        assert tables == [("workbench_events",)]
