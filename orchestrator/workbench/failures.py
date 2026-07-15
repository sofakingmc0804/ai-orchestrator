from __future__ import annotations

from typing import Annotated, Literal, Union, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    MaterialityReason, OutcomeDimension, Task3FailureCode, TaskCacheToken, TaskCacheValue,
)


class _FrozenFailureModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def _wire_enums(cls, data):
        if isinstance(data, dict) and "code" in data and isinstance(data["code"], str):
            data = dict(data)
            try:
                data["code"] = Task3FailureCode(data["code"])
            except ValueError:
                pass
        if isinstance(data, dict):
            data = dict(data)
            for name, field in cls.model_fields.items():
                if name in data and get_origin(field.annotation) is tuple and isinstance(data[name], list):
                    data[name] = tuple(data[name])
        return data

    @field_validator("*")
    @classmethod
    def _checksum_shape(cls, value, info):
        if (info.field_name or "").endswith("checksum") and value is not None:
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("checksum must be lowercase SHA-256")
        return value


class FailureSubject(_FrozenFailureModel):
    kind: Literal[
        "migration", "event", "candidate", "proposal", "operation", "node", "decision",
        "queue", "native_identity", "service_run", "receipt", "evidence", "task_cache",
        "authority_row",
    ]
    id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _migration_identity(self):
        if self.kind == "migration" and self.id != "0004":
            raise ValueError("migration subject must identify migration 0004")
        if self.kind == "event" and self.id == "0004":
            raise ValueError("event subject cannot use migration identity")
        return self


class Task3AuthorityRowCount(_FrozenFailureModel):
    table_name: Literal[
        "decision_requests", "evidence_refs", "frame_edges", "frame_nodes", "frame_proposals",
        "service_run_inputs", "service_runs",
    ]
    row_count: int = Field(gt=0)


class _CodeDetails(_FrozenFailureModel):
    code: Task3FailureCode


class PreexistingTask3AuthorityDetails(_CodeDetails):
    code: Literal["preexisting_task3_authority_without_events"]
    migration_version: Literal[4]
    row_counts: tuple[Task3AuthorityRowCount, ...] = Field(min_length=1)

    @field_validator("row_counts")
    @classmethod
    def _rows_sorted(cls, value):
        names = tuple(x.table_name for x in value)
        if names != tuple(sorted(set(names))):
            raise ValueError("row_counts must be sorted by unique table_name")
        return value


class StaleFrameCursorDetails(_CodeDetails):
    code: Literal["stale_frame_cursor"]
    predicate_id: str
    expected_head_event_id: str | None = None
    expected_head_sequence: int
    expected_frame_version: int
    actual_head_event_id: str | None = None
    actual_head_sequence: int
    actual_frame_version: int


class UnauthenticFrameBaseDetails(_CodeDetails):
    code: Literal["unauthentic_frame_base"]
    predicate_id: str
    base_frame_version: int
    claimed_state_checksum: str
    recomputed_state_checksum: str | None = None
    missing_event_ids: tuple[str, ...] = ()

    @field_validator("missing_event_ids")
    @classmethod
    def _missing_sorted(cls, value):
        if value != tuple(sorted(set(value))):
            raise ValueError("missing_event_ids must be sorted and unique")
        return value


class ProposalNotPendingDetails(_CodeDetails):
    code: Literal["proposal_not_pending"]
    proposal_id: str; predicate_id: str; expected_status: Literal["pending"] = "pending"
    actual_status: str | None = None; expected_last_event_id: str | None = None; actual_last_event_id: str | None = None


class ProposalPreviewMismatchDetails(_CodeDetails):
    code: Literal["proposal_preview_mismatch"]
    proposal_id: str; expected_base_state_checksum: str; actual_base_state_checksum: str
    expected_impact_preview_checksum: str; actual_impact_preview_checksum: str


class InvalidFrameChangeDetails(_CodeDetails):
    code: Literal["invalid_frame_change"]
    predicate_id: str; operation_id: str | None = None; field_path: str
    expected_checksum: str | None = None; actual_checksum: str | None = None


class DependencyCycleDetails(_CodeDetails):
    code: Literal["dependency_cycle"]
    cycle_node_ids: tuple[str, ...] = Field(min_length=2)

    @field_validator("cycle_node_ids")
    @classmethod
    def _closed_cycle(cls, value):
        if value[0] != value[-1]:
            raise ValueError("cycle_node_ids must repeat first node at end")
        return value


class CrossBranchReferenceDetails(_CodeDetails):
    code: Literal["cross_branch_reference"]
    reference_kind: str; reference_id: str; expected_task_id: str; expected_branch_id: str
    actual_task_id: str | None = None; actual_branch_id: str | None = None


class StaleDecisionRevisionDetails(_CodeDetails):
    code: Literal["stale_decision_revision"]
    decision_id: str; predicate_id: str; expected_revision: int; actual_revision: int | None = None
    expected_last_event_id: str | None = None; actual_last_event_id: str | None = None


class StaleQueueRevisionDetails(_CodeDetails):
    code: Literal["stale_queue_revision"]
    predicate_id: str; expected_queue_revision: int; actual_queue_revision: int
    expected_head_decision_id: str | None = None; actual_head_decision_id: str | None = None
    expected_queue_checksum: str; actual_queue_checksum: str


class InvalidDecisionTransitionDetails(_CodeDetails):
    code: Literal["invalid_decision_transition"]
    decision_id: str; event_type: str; from_state: str; requested_state: str; predicate_id: str


class InvalidOptionReferenceDetails(_CodeDetails):
    code: Literal["invalid_option_reference"]
    decision_id: str; reference_kind: Literal["option", "position", "recommendation", "default"]; reference_id: str


class UndeclaredOutcomeDetails(_CodeDetails):
    code: Literal["undeclared_outcome"]
    materiality_reasons: tuple[MaterialityReason, ...]
    changed_dimensions: tuple[OutcomeDimension, ...]

    @model_validator(mode="after")
    def _canonical(self):
        for name in ("materiality_reasons", "changed_dimensions"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values), key=lambda value: value.value if hasattr(value, "value") else value)):
                raise ValueError(f"{name} must be sorted and unique")
        return self


class NativeIdentityConflictDetails(_CodeDetails):
    code: Literal["native_identity_conflict"]
    native_identity_kind: Literal["question", "reply"]; native_identity_checksum: str
    requested_classification: str; candidate_occurrence_id: str; candidate_decision_id: str | None = None
    existing_decision_id: str | None = None; existing_occurrence_id: str | None = None; existing_authority_event_id: str | None = None


class DomainAuthorityCorruptionDetails(_CodeDetails):
    code: Literal["domain_authority_corruption"]
    table_name: str; row_identity: str; predicate_id: str; authority_event_id: str | None = None
    expected_checksum: str | None = None; actual_checksum: str | None = None


class InvalidRunTransitionDetails(_CodeDetails):
    code: Literal["invalid_run_transition"]
    run_id: str; reason: str; from_state: str | None = None; requested_state: str
    expected_revision: int; actual_revision: int | None = None; predicate_id: str


class ReceiptConflictDetails(_CodeDetails):
    code: Literal["receipt_conflict"]
    receipt_id: str | None = None; run_id: str; predicate_id: str; expected_run_revision: int
    actual_run_revision: int | None = None; expected_frame_version: int; actual_frame_version: int | None = None
    expected_input_manifest_checksum: str; actual_input_manifest_checksum: str | None = None
    expected_last_event_id: str; actual_last_event_id: str | None = None


class EvidenceVisibilityErrorDetails(_CodeDetails):
    code: Literal["evidence_visibility_error"]
    evidence_id: str; predicate_id: str; expected_task_id: str; expected_branch_id: str
    actual_task_id: str | None = None; actual_branch_id: str | None = None
    expected_authority_event_id: str | None = None; actual_authority_event_id: str | None = None
    expected_checksum: str | None = None; actual_checksum: str | None = None


class TaskCacheConflictDetails(_CodeDetails):
    code: Literal["task_cache_conflict"]
    task_id: str; predicate_id: str; mismatched_fields: tuple[str, ...]
    expected_cache: TaskCacheToken; actual_cache: TaskCacheValue | None = None

    @field_validator("mismatched_fields")
    @classmethod
    def _mismatch_sorted(cls, value):
        if value != tuple(sorted(set(value))):
            raise ValueError("mismatched_fields must be sorted and unique")
        return value


FailureDetails = Annotated[
    Union[
        PreexistingTask3AuthorityDetails, StaleFrameCursorDetails, UnauthenticFrameBaseDetails,
        ProposalNotPendingDetails, ProposalPreviewMismatchDetails, InvalidFrameChangeDetails,
        DependencyCycleDetails, CrossBranchReferenceDetails, StaleDecisionRevisionDetails,
        StaleQueueRevisionDetails, InvalidDecisionTransitionDetails, InvalidOptionReferenceDetails,
        UndeclaredOutcomeDetails, NativeIdentityConflictDetails, DomainAuthorityCorruptionDetails,
        InvalidRunTransitionDetails, ReceiptConflictDetails, EvidenceVisibilityErrorDetails,
        TaskCacheConflictDetails,
    ],
    Field(discriminator="code"),
]


class Task3Failure(_FrozenFailureModel):
    schema_version: Literal[1]
    code: Task3FailureCode
    task_id: str | None = None
    branch_id: str | None = None
    subject: FailureSubject
    details: FailureDetails
    message: str = Field(min_length=1)

    @model_validator(mode="after")
    def _codes_match(self):
        if self.details.code != self.code:
            raise ValueError("failure detail code must equal top-level code")
        allowed = {
            Task3FailureCode.PREEXISTING: {"migration"},
            Task3FailureCode.STALE_FRAME_CURSOR: {"event"},
            Task3FailureCode.UNAUTHENTIC_FRAME_BASE: {"event"},
            Task3FailureCode.PROPOSAL_NOT_PENDING: {"proposal"},
            Task3FailureCode.PROPOSAL_PREVIEW_MISMATCH: {"proposal"},
            Task3FailureCode.INVALID_FRAME_CHANGE: {"operation", "proposal"},
            Task3FailureCode.DEPENDENCY_CYCLE: {"proposal"},
            Task3FailureCode.CROSS_BRANCH_REFERENCE: {"authority_row", "event", "node", "decision", "proposal", "service_run", "evidence"},
            Task3FailureCode.STALE_DECISION_REVISION: {"decision"},
            Task3FailureCode.STALE_QUEUE_REVISION: {"queue"},
            Task3FailureCode.INVALID_DECISION_TRANSITION: {"decision"},
            Task3FailureCode.INVALID_OPTION_REFERENCE: {"decision"},
            Task3FailureCode.UNDECLARED_OUTCOME: {"candidate", "proposal", "decision"},
            Task3FailureCode.NATIVE_IDENTITY_CONFLICT: {"native_identity"},
            Task3FailureCode.DOMAIN_AUTHORITY_CORRUPTION: {"authority_row"},
            Task3FailureCode.INVALID_RUN_TRANSITION: {"service_run"},
            Task3FailureCode.RECEIPT_CONFLICT: {"receipt", "service_run"},
            Task3FailureCode.EVIDENCE_VISIBILITY_ERROR: {"evidence"},
            Task3FailureCode.TASK_CACHE_CONFLICT: {"task_cache"},
        }
        if self.subject.kind not in allowed[self.code]:
            raise ValueError("failure subject kind does not match failure code")
        if self.code == Task3FailureCode.PREEXISTING:
            if self.task_id is not None or self.branch_id is not None:
                raise ValueError("migration-wide failure cannot claim task or branch")
            if self.subject != FailureSubject(kind="migration", id="0004"):
                raise ValueError("migration-four failure must identify migration 0004")
        return self

    @property
    def subject_id(self) -> str:
        return self.subject.id

    @property
    def materiality_reasons(self):
        values = getattr(self.details, "materiality_reasons", ())
        return tuple(value.value if hasattr(value, "value") else value for value in values)

    @property
    def changed_dimensions(self):
        values = getattr(self.details, "changed_dimensions", ())
        return tuple(value.value if hasattr(value, "value") else value for value in values)


def preexisting_task3_authority_failure(row_counts: tuple[Task3AuthorityRowCount, ...]) -> Task3Failure:
    return Task3Failure(
        schema_version=1, code=Task3FailureCode.PREEXISTING,
        subject=FailureSubject(kind="migration", id="0004"),
        details=PreexistingTask3AuthorityDetails(code="preexisting_task3_authority_without_events", migration_version=4, row_counts=row_counts),
        message="migration 4 refuses to authenticate preexisting Task-3 authority without events",
    )


class UndeclaredOutcomeError(ValueError):
    def __init__(self, *, task_id: str, branch_id: str, subject_id: str, materiality_reasons: tuple[str, ...], changed_dimensions: tuple[str, ...]):
        detail = UndeclaredOutcomeDetails(
            code="undeclared_outcome",
            materiality_reasons=tuple(MaterialityReason(value) for value in materiality_reasons),
            changed_dimensions=changed_dimensions,
        )
        self.task_id, self.branch_id, self.subject_id = task_id, branch_id, subject_id
        self.materiality_reasons = materiality_reasons; self.changed_dimensions = changed_dimensions
        self.code = "undeclared_outcome"
        self.failure = Task3Failure(
            schema_version=1, code="undeclared_outcome", task_id=task_id, branch_id=branch_id,
            subject=FailureSubject(kind="candidate", id=subject_id), details=detail,
            message=f"undeclared outcome for {subject_id}",
        )
        super().__init__(f"undeclared outcome for {subject_id}")
