from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol

from orchestrator.workbench.events import CORE_PARTICIPANT_ID, EventRegistry, ValidatedDraft
from orchestrator.workbench.models import WorkbenchEvent, WorkbenchSnapshot


class RuntimeConfigurationError(ValueError):
    """The event registry and normalized-authority participants do not agree."""


class ParticipantCapabilityError(RuntimeError):
    """A participant attempted an operation outside its declared authority."""


@dataclass(frozen=True)
class ParticipantTablePolicy:
    table_name: str
    readable_columns: frozenset[str]
    insertable_columns: frozenset[str] = frozenset()
    updatable_columns: frozenset[str] = frozenset()
    task_id_column: str = "task_id"
    global_read: bool = False


@dataclass(frozen=True)
class ParticipantDatabasePolicy:
    participant_id: str
    tables: tuple[ParticipantTablePolicy, ...]


ParticipantPhase = Literal["validate", "apply", "verify"]


@dataclass(frozen=True)
class ParticipantInsert:
    table_name: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class ParticipantUpdate:
    table_name: str
    values: Mapping[str, Any]
    equals: Mapping[str, Any]


ParticipantMutation = ParticipantInsert | ParticipantUpdate


class ParticipantDatabase:
    """Immutable preloaded rows plus an in-memory, store-applied mutation plan."""

    __slots__ = (
        "__phase",
        "__task_id",
        "__tables",
        "__task_rows",
        "__global_rows",
        "__mutations",
    )

    def __init__(
        self,
        *,
        phase: ParticipantPhase,
        task_id: str,
        policy: ParticipantDatabasePolicy,
        task_rows: Mapping[str, tuple[Mapping[str, Any], ...]],
        global_rows: Mapping[str, tuple[Mapping[str, Any], ...]],
    ) -> None:
        self.__phase = phase
        self.__task_id = task_id
        self.__tables = MappingProxyType(
            {table.table_name: table for table in policy.tables}
        )
        self.__task_rows = MappingProxyType(dict(task_rows))
        self.__global_rows = MappingProxyType(dict(global_rows))
        self.__mutations: list[ParticipantMutation] = []

    @property
    def phase(self) -> ParticipantPhase:
        return self.__phase

    @property
    def task_id(self) -> str:
        return self.__task_id

    def _table(self, table_name: str) -> ParticipantTablePolicy:
        table = self.__tables.get(table_name)
        if table is None:
            raise ParticipantCapabilityError(f"table is outside participant capability: {table_name}")
        return table

    @staticmethod
    def _require_columns(
        requested: set[str], allowed: frozenset[str], *, operation: str
    ) -> None:
        denied = requested - set(allowed)
        if denied:
            raise ParticipantCapabilityError(
                f"columns are outside {operation} capability: {sorted(denied)}"
            )

    def _predicates(
        self,
        table: ParticipantTablePolicy,
        equals: Mapping[str, Any],
        *,
        global_scope: bool,
    ) -> Mapping[str, Any]:
        values = dict(equals)
        if global_scope:
            if self.__phase != "verify" or not table.global_read:
                raise ParticipantCapabilityError("global read is unavailable in this phase")
        else:
            supplied = values.pop(table.task_id_column, self.__task_id)
            if supplied != self.__task_id:
                raise ParticipantCapabilityError("cross-task predicate is prohibited")
            values[table.task_id_column] = self.__task_id
        self._require_columns(
            set(values), table.readable_columns | {table.task_id_column}, operation="read"
        )
        return MappingProxyType(values)

    async def fetch_all(
        self,
        table_name: str,
        *,
        columns: tuple[str, ...],
        equals: Mapping[str, Any] | None = None,
        order_by: tuple[str, ...] = (),
    ) -> tuple[Mapping[str, Any], ...]:
        return await self._fetch(
            table_name,
            columns=columns,
            equals=equals or {},
            order_by=order_by,
            global_scope=False,
        )

    async def fetch_global(
        self,
        table_name: str,
        *,
        columns: tuple[str, ...],
        equals: Mapping[str, Any] | None = None,
        order_by: tuple[str, ...] = (),
    ) -> tuple[Mapping[str, Any], ...]:
        return await self._fetch(
            table_name,
            columns=columns,
            equals=equals or {},
            order_by=order_by,
            global_scope=True,
        )

    async def _fetch(
        self,
        table_name: str,
        *,
        columns: tuple[str, ...],
        equals: Mapping[str, Any],
        order_by: tuple[str, ...],
        global_scope: bool,
    ) -> tuple[Mapping[str, Any], ...]:
        table = self._table(table_name)
        if not columns or len(set(columns)) != len(columns):
            raise ParticipantCapabilityError("read columns must be nonempty and unique")
        self._require_columns(set(columns), table.readable_columns, operation="read")
        self._require_columns(set(order_by), table.readable_columns, operation="order")
        predicates = self._predicates(table, equals, global_scope=global_scope)
        source = self.__global_rows if global_scope else self.__task_rows
        rows = [
            row
            for row in source.get(table.table_name, ())
            if all(row.get(column) == value for column, value in predicates.items())
        ]
        if order_by:
            rows.sort(key=lambda row: tuple(row[column] for column in order_by))
        return tuple(
            MappingProxyType({column: row[column] for column in columns})
            for row in rows
        )

    async def insert(self, table_name: str, values: Mapping[str, Any]) -> None:
        if self.__phase != "apply":
            raise ParticipantCapabilityError("writes are available only during apply")
        table = self._table(table_name)
        row = dict(values)
        supplied = row.pop(table.task_id_column, self.__task_id)
        if supplied != self.__task_id:
            raise ParticipantCapabilityError("cross-task insert is prohibited")
        row[table.task_id_column] = self.__task_id
        self._require_columns(
            set(row) - {table.task_id_column}, table.insertable_columns, operation="insert"
        )
        self.__mutations.append(
            ParticipantInsert(
                table_name=table.table_name,
                values=MappingProxyType(row),
            )
        )

    async def update(
        self,
        table_name: str,
        values: Mapping[str, Any],
        *,
        equals: Mapping[str, Any],
    ) -> int:
        if self.__phase != "apply":
            raise ParticipantCapabilityError("writes are available only during apply")
        table = self._table(table_name)
        changes = dict(values)
        if not changes or table.task_id_column in changes:
            raise ParticipantCapabilityError("update values are empty or change task identity")
        self._require_columns(set(changes), table.updatable_columns, operation="update")
        predicates = self._predicates(table, equals, global_scope=False)
        count = sum(
            all(row.get(column) == value for column, value in predicates.items())
            for row in self.__task_rows.get(table.table_name, ())
        )
        self.__mutations.append(
            ParticipantUpdate(
                table_name=table.table_name,
                values=MappingProxyType(changes),
                equals=predicates,
            )
        )
        return count

    def export_mutations(self) -> tuple[ParticipantMutation, ...]:
        return tuple(self.__mutations)


@dataclass(frozen=True)
class DomainAuthorityVerification:
    valid: bool
    participant_id: str
    reason: str | None = None


@dataclass(frozen=True)
class CommandContext:
    task_id: str
    branch_id: str
    before: WorkbenchSnapshot
    global_head_sequence: int
    global_head_checksum: str
    selected_branch_id: str
    selected_branch_frame_version: int


class CommandParticipant(Protocol):
    participant_id: str
    event_types: frozenset[tuple[str, int]]

    async def validate_command(
        self,
        db: ParticipantDatabase,
        *,
        context: CommandContext,
        drafts: tuple[ValidatedDraft, ...],
    ) -> None: ...

    async def apply_command(
        self,
        db: ParticipantDatabase,
        *,
        before: WorkbenchSnapshot,
        events: tuple[WorkbenchEvent, ...],
        after: WorkbenchSnapshot,
    ) -> None: ...

    async def verify_authority(
        self,
        db: ParticipantDatabase,
        *,
        task_id: str,
        events: tuple[WorkbenchEvent, ...],
    ) -> DomainAuthorityVerification: ...


@dataclass(frozen=True)
class WorkbenchRuntime:
    registry: EventRegistry
    participants: tuple[CommandParticipant, ...]
    database_policies: tuple[ParticipantDatabasePolicy, ...] = ()

    def __post_init__(self) -> None:
        if type(self.participants) is not tuple:
            raise RuntimeConfigurationError("runtime participants must be a tuple")
        if type(self.database_policies) is not tuple:
            raise RuntimeConfigurationError("runtime database policies must be a tuple")
        ownership = self._validate_ownership()
        policies = self._validate_database_policies()
        self.registry.freeze()
        object.__setattr__(self, "_ownership", ownership)
        object.__setattr__(self, "_database_policies", policies)
        object.__setattr__(self, "_database_policy_fingerprint", self._policy_fingerprint())
        object.__setattr__(self, "_registry_fingerprint", self.registry.ownership_fingerprint())
        object.__setattr__(
            self, "_registry_semantic_fingerprint", self.registry.semantic_fingerprint()
        )

    def _validate_ownership(self) -> dict[tuple[str, int], CommandParticipant]:
        definitions = self.registry.definitions()
        by_id: dict[str, CommandParticipant] = {}
        claimed: dict[tuple[str, int], CommandParticipant] = {}
        for participant in self.participants:
            participant_id = participant.participant_id
            if not isinstance(participant_id, str) or not participant_id.strip():
                raise RuntimeConfigurationError("participant_id must be a nonblank string")
            if participant_id == CORE_PARTICIPANT_ID:
                raise RuntimeConfigurationError("participant_id core is reserved by the store")
            if participant_id in by_id:
                raise RuntimeConfigurationError(f"duplicate participant_id: {participant_id}")
            by_id[participant_id] = participant
            if type(participant.event_types) is not frozenset or not participant.event_types:
                raise RuntimeConfigurationError(
                    f"participant {participant_id} event_types must be a nonempty frozenset"
                )
            for key in participant.event_types:
                if (
                    type(key) is not tuple
                    or len(key) != 2
                    or not isinstance(key[0], str)
                    or not key[0].strip()
                    or type(key[1]) is not int
                    or key[1] < 1
                ):
                    raise RuntimeConfigurationError(
                        f"participant {participant_id} has invalid event key {key!r}"
                    )
                definition = definitions.get(key)
                if definition is None:
                    raise RuntimeConfigurationError(f"participant {participant_id} claims unregistered event {key}")
                if definition.authority_participant == CORE_PARTICIPANT_ID:
                    raise RuntimeConfigurationError(f"participant {participant_id} claims reserved core event {key}")
                if key in claimed:
                    raise RuntimeConfigurationError(f"event {key} is owned by more than one participant")
                if definition.authority_participant != participant_id:
                    raise RuntimeConfigurationError(
                        f"event {key} declares {definition.authority_participant}, not {participant_id}"
                    )
                claimed[key] = participant
        for key, definition in definitions.items():
            owner = definition.authority_participant
            if owner == CORE_PARTICIPANT_ID:
                continue
            participant = claimed.get(key)
            if participant is None:
                raise RuntimeConfigurationError(f"missing participant for {key}: {owner}")
            if participant.participant_id != owner:
                raise RuntimeConfigurationError(f"declared owner mismatch for {key}: {owner}")
        return claimed

    def _validate_database_policies(self) -> dict[str, ParticipantDatabasePolicy]:
        participant_ids = {participant.participant_id for participant in self.participants}
        policies: dict[str, ParticipantDatabasePolicy] = {}
        owned_tables: dict[str, str] = {}
        reserved = {
            "workbench_events",
            "workbench_command_manifests",
            "workbench_tasks",
            "workbench_branches",
            "projection_metadata",
            "schema_migrations",
            "sqlite_schema",
        }
        reserved_keys = {name.casefold() for name in reserved}
        for policy in self.database_policies:
            if type(policy.tables) is not tuple:
                raise RuntimeConfigurationError("database policy tables must be a tuple")
            if policy.participant_id not in participant_ids:
                raise RuntimeConfigurationError(
                    f"database policy has no participant: {policy.participant_id}"
                )
            if policy.participant_id in policies:
                raise RuntimeConfigurationError(
                    f"duplicate database policy: {policy.participant_id}"
                )
            table_names: set[str] = set()
            for table in policy.tables:
                if any(
                    type(columns) is not frozenset
                    for columns in (
                        table.readable_columns,
                        table.insertable_columns,
                        table.updatable_columns,
                    )
                ):
                    raise RuntimeConfigurationError(
                        f"participant table {table.table_name} columns must be frozensets"
                    )
                if type(table.global_read) is not bool:
                    raise RuntimeConfigurationError("table global_read must be boolean")
                if not isinstance(table.task_id_column, str) or not table.task_id_column.strip():
                    raise RuntimeConfigurationError("table task_id_column must be nonblank text")
                table_key = table.table_name.casefold()
                if (
                    not table.table_name.strip()
                    or table_key.startswith("sqlite_")
                    or table_key in reserved_keys
                ):
                    raise RuntimeConfigurationError(
                        f"reserved or invalid participant table: {table.table_name}"
                    )
                if table_key in table_names:
                    raise RuntimeConfigurationError(
                        f"duplicate table in participant policy: {table.table_name}"
                    )
                table_names.add(table_key)
                prior = owned_tables.get(table_key)
                if prior is not None:
                    raise RuntimeConfigurationError(
                        f"participant table {table.table_name} is owned by both {prior} and {policy.participant_id}"
                    )
                owned_tables[table_key] = policy.participant_id
                if table.task_id_column != "task_id":
                    raise RuntimeConfigurationError(
                        f"participant table {table.table_name} must use literal task_id"
                    )
                if "task_id" not in table.readable_columns:
                    raise RuntimeConfigurationError(
                        f"participant table {table.table_name} must make task_id readable"
                    )
                if (
                    "task_id" in table.insertable_columns
                    or "task_id" in table.updatable_columns
                ):
                    raise RuntimeConfigurationError(
                        f"participant table {table.table_name} task_id is store-owned and immutable"
                    )
                all_columns = (
                    table.readable_columns
                    | table.insertable_columns
                    | table.updatable_columns
                    | {table.task_id_column}
                )
                if any(not isinstance(column, str) or not column.strip() for column in all_columns):
                    raise RuntimeConfigurationError(
                        f"participant table {table.table_name} has invalid columns"
                    )
            policies[policy.participant_id] = policy
        missing = participant_ids - set(policies)
        if missing:
            raise RuntimeConfigurationError(
                f"missing database policy for participants: {sorted(missing)}"
            )
        return policies

    def _policy_fingerprint(self) -> tuple[Any, ...]:
        return tuple(
            (
                policy.participant_id,
                tuple(
                    (
                        table.table_name,
                        tuple(sorted(table.readable_columns)),
                        tuple(sorted(table.insertable_columns)),
                        tuple(sorted(table.updatable_columns)),
                        table.task_id_column,
                        table.global_read,
                    )
                    for table in policy.tables
                ),
            )
            for policy in self.database_policies
        )

    def validate_current(self) -> None:
        if self.registry.ownership_fingerprint() != self._registry_fingerprint:
            raise RuntimeConfigurationError("runtime registry ownership changed after construction")
        if self.registry.semantic_fingerprint() != self._registry_semantic_fingerprint:
            raise RuntimeConfigurationError("runtime registry semantic definition changed")
        if self._validate_ownership() != self._ownership:
            raise RuntimeConfigurationError("runtime participant ownership changed after construction")
        if self._validate_database_policies() != self._database_policies:
            raise RuntimeConfigurationError("runtime database policy changed after construction")
        if self._policy_fingerprint() != self._database_policy_fingerprint:
            raise RuntimeConfigurationError("runtime database policy fingerprint changed")

    def database_policy_for(self, participant_id: str) -> ParticipantDatabasePolicy:
        self.validate_current()
        try:
            return self._database_policies[participant_id]
        except KeyError as exc:
            raise RuntimeConfigurationError(
                f"missing database policy for participant {participant_id}"
            ) from exc

    def participant_for(self, drafts: tuple[ValidatedDraft, ...]) -> CommandParticipant | None:
        self.validate_current()
        owners = {
            item.definition.authority_participant
            for item in drafts
        }
        if len(owners) != 1:
            raise RuntimeConfigurationError("one command cannot mix multiple authority participants")
        owner = next(iter(owners))
        if owner == CORE_PARTICIPANT_ID:
            return None
        participant = self._ownership.get(
            (drafts[0].draft.event_type, drafts[0].draft.event_schema_version)
        )
        if participant is None or participant.participant_id != owner:
            raise RuntimeConfigurationError(f"missing runtime participant for command owner {owner}")
        return participant
