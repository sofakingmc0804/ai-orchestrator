from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Annotated, Union, Mapping, get_origin, get_args
import types
import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, ValidationError


class WorkbenchError(RuntimeError):
    """Base class for fail-closed workbench errors."""


class EventValidationError(WorkbenchError):
    """An event cannot be represented by the declared schema."""


class TaskNotFound(WorkbenchError):
    """The requested task does not exist."""


class BranchNotFound(WorkbenchError):
    """The requested branch does not exist for the task."""


class IdempotencyConflict(WorkbenchError):
    """An idempotency identity was reused for different immutable content."""


class StaleFrameVersion(WorkbenchError):
    """A confirmed frame mutation was based on an obsolete frame version."""


class InvalidCause(WorkbenchError):
    """A causal reference is missing, future, or belongs to another task."""


class UnsupportedEventType(EventValidationError):
    """No structural schema and reducer are registered for an event type."""


class UnsupportedEventSchema(EventValidationError):
    """The event schema version is not supported."""


class LedgerCorruption(WorkbenchError):
    def __init__(self, sequence: int | None, reason: str) -> None:
        self.sequence = sequence
        self.reason = reason
        label = "ledger head" if sequence is None else f"ledger sequence {sequence}"
        super().__init__(f"{label} is invalid: {reason}")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EventCause(str, Enum):
    OWNER_REQUEST = "owner_request"
    SERVICE_RESULT = "service_result"
    CORRECTION = "correction"
    DEPENDENCY_CHANGE = "dependency_change"
    RECOVERY = "recovery"
    VERIFICATION = "verification"
    EXTERNAL_OBSERVATION = "external_observation"
    SYSTEM_TRANSITION = "system_transition"

class ServiceRunState(str, Enum):
    QUEUED = "queued"; STARTING = "starting"; RUNNING = "running"; WAITING_OWNER = "waiting_owner"
    PAUSED_DEPENDENCY = "paused_dependency"; REPAIRING = "repairing"; VERIFYING = "verifying"
    INTERRUPTED = "interrupted"; COMPLETE = "complete"; FAILED = "failed"; CANCELED = "canceled"
class OutputValidity(str, Enum):
    CURRENT = "current"; STALE = "stale"; REVERIFICATION_REQUIRED = "reverification_required"
class RunTransitionReason(str, Enum):
    DISPATCH_STARTED = "dispatch_started"
    PROVIDER_ATTACHED = "provider_attached"
    OWNER_INPUT_REQUIRED = "owner_input_required"
    OWNER_INPUT_RECEIVED = "owner_input_received"
    DEPENDENCY_CHANGED = "dependency_changed"
    DEPENDENCY_RESTORED = "dependency_restored"
    REPAIR_STARTED = "repair_started"
    RETRY_STARTED = "retry_started"
    RECOVERY_RECONCILED = "recovery_reconciled"
    VERIFICATION_STARTED = "verification_started"
    SERVICE_INTERRUPTED = "service_interrupted"
    SERVICE_COMPLETED = "service_completed"
    SERVICE_FAILED = "service_failed"
    OWNER_CANCELED = "owner_canceled"
    OUTPUT_STALED = "output_staled"
    MISSING_INPUT_MANIFEST = "missing_input_manifest"
    REVERIFICATION_REQUIRED = "reverification_required"
class MaterialityReason(str, Enum):
    NO_MATERIAL_DIFFERENCE = "no_material_difference"; IDENTICAL_DELTA_ALREADY_RECORDED = "identical_delta_already_recorded"
    REVERSIBLE_DEFAULT_WITHIN_AUTHORITY = "reversible_default_within_authority"; UNDELEGATED_MATERIAL_DIFFERENCE = "undelegated_material_difference"
    MISSING_DIMENSION = "missing_dimension"; DUPLICATE_DIMENSION = "duplicate_dimension"; CONTRADICTORY_DIMENSION = "contradictory_dimension"
    DELEGATION_MISSING = "delegation_missing"; DEFAULT_OUTSIDE_AUTHORITY = "default_outside_authority"; EXTERNAL_ACTION_UNAUTHORIZED = "external_action_unauthorized"; IRREVERSIBILITY_CONTRADICTION = "irreversibility_contradiction"
class DecisionSupersessionReason(str, Enum):
    CORRECTION = "correction"; SOURCE_WITHDRAWN = "source_withdrawn"; AUTHORITY_REVOKED = "authority_revoked"; BRANCH_ABANDONED = "branch_abandoned"; OWNER_SUPERSEDED = "owner_superseded"

class DecisionTier(str, Enum):
    ROUTINE = "routine"
    BLOCKING = "blocking"
    CRITICAL = "critical"

class DecisionKind(str, Enum):
    INTENT_CLARIFICATION = "intent_clarification"
    TOOL_APPROVAL = "tool_approval"
    EXTERNAL_ACTION_APPROVAL = "external_action_approval"
    EVIDENCE_CHECKPOINT = "evidence_checkpoint"
    SERVICE_TEAM_OVERRIDE = "service_team_override"
    FRAME_INTERPRETATION_CONFIRMATION = "frame_interpretation_confirmation"

class FrameNodeKind(str, Enum):
    GOAL = "goal"
    SUCCESS = "success"
    CONSTRAINT = "constraint"
    ASSUMPTION = "assumption"
    ALTERNATIVE = "alternative"
    AUTHORITY = "authority"
    EVIDENCE_GAP = "evidence_gap"
    DECISION = "decision"
    ACTION = "action"

OutcomeDimension = Literal["intent", "success", "consequence", "authority", "cost", "owner_visible_ux", "external_action", "irreversibility"]


class EventActor(_FrozenModel):
    kind: Literal["owner", "service", "system", "automation"]
    actor_id: str = Field(min_length=1)

    @field_validator("actor_id")
    @classmethod
    def _nonblank_actor(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("actor_id must not be blank")
        return value


class EventDraft(_FrozenModel):
    event_type: str = Field(min_length=1)
    actor: EventActor
    payload: dict[str, Any]
    branch_id: str = Field(default="main", min_length=1)
    event_schema_version: int = Field(default=1, ge=1)
    cause: EventCause | None = None
    caused_by: str | None = None

    @field_validator("event_type", "branch_id")
    @classmethod
    def _nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event identities must not be blank")
        return value

    @field_validator("payload")
    @classmethod
    def _canonical_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value)
        return value


class WorkbenchEvent(_FrozenModel):
    event_schema_version: int = Field(ge=1)
    event_id: str
    task_id: str
    sequence: int = Field(gt=0)
    event_type: str
    actor: EventActor
    branch_id: str
    cause: EventCause | None
    caused_by: str | None
    command_id: str
    command_sequence: int = Field(gt=0)
    frame_version: int = Field(ge=0)
    payload: dict[str, Any]
    idempotency_key: str
    prior_checksum: str
    checksum: str
    created_at: str

    @field_validator("payload")
    @classmethod
    def _canonical_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value)
        return value


class ProjectionHead(_FrozenModel):
    task_id: str
    projection_name: str
    branch_id: str
    head_sequence: int = Field(ge=0)
    head_event_checksum: str | None
    state_checksum: str


class WorkbenchSnapshot(_FrozenModel):
    task_id: str
    branch_id: str
    at_sequence: int | None = Field(default=None, ge=0)
    frame_version: int = Field(ge=0)
    state: dict[str, Any]
    head: ProjectionHead


class LedgerVerification(_FrozenModel):
    valid: bool
    task_id: str
    checked_events: int = Field(ge=0)
    head_sequence: int = Field(ge=0)
    head_checksum: str
    first_invalid_sequence: int | None = Field(default=None, ge=1)
    reason: str | None = None
    authenticity_boundary: str = (
        "Checksums detect corruption that was not recomputed. External authenticity requires "
        "an independently anchored or signed ledger head."
    )


def _normalize_enum_wire(value: Any, annotation: Any) -> Any:
    """Convert exact JSON enum values back to Enum members for strict models.

    Pydantic strict mode intentionally rejects strings for Enum fields. JSON
    serialization emits those members as strings, so the model boundary must
    perform this one lossless conversion while leaving all other scalar
    coercion disabled.
    """
    origin = get_origin(annotation)
    if origin in (tuple, list, set, frozenset):
        args = get_args(annotation)
        item_annotation = args[0] if args else Any
        if isinstance(value, (tuple, list)):
            converted = tuple(_normalize_enum_wire(item, item_annotation) for item in value)
            return converted if origin is tuple else list(converted)
        return value
    if origin in (Union, types.UnionType):
        for option in get_args(annotation):
            if option is type(None):
                continue
            converted = _normalize_enum_wire(value, option)
            if converted is not value:
                return converted
        return value
    try:
        is_enum = isinstance(annotation, type) and issubclass(annotation, Enum)
    except TypeError:
        is_enum = False
    if is_enum:
        if isinstance(value, annotation):
            return value
        if type(value) is str:
            try:
                return annotation(value)
            except ValueError:
                return value
    return value


# Task 3 public domain.  These are deliberately kept in the model module so
# adapters cannot smuggle untyped dictionaries across the command boundary.
class _Task3Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def _json_wire_and_any_guard(cls, data):
        if not isinstance(data, Mapping):
            return data
        values = dict(data)
        for name, field in cls.model_fields.items():
            if name not in values:
                continue
            value = values[name]
            if get_origin(field.annotation) is tuple and isinstance(value, list):
                value = tuple(value)
            values[name] = _normalize_enum_wire(value, field.annotation)
            if field.annotation is Any:
                _validate_json_value(value)
        return values

    def model_copy(self, *, update=None, deep=False):  # type: ignore[override]
        if update:
            unknown = set(update) - set(type(self).model_fields)
            if unknown: raise ValidationError.from_exception_data(type(self).__name__, [{"type": "extra_forbidden", "loc": (next(iter(unknown)),), "input": update[next(iter(unknown))]}])
            return type(self).model_validate({**self.model_dump(mode="python"), **update})
        return super().model_copy(update=update, deep=deep)

    @field_validator("*", mode="after")
    @classmethod
    def _identity_and_checksum_fields(cls, value, info):
        name = info.field_name or ""
        if name.endswith("_checksum"):
            if value is None:
                return value
            return _checksum(value)
        if value is not None and (name.endswith("_at") or name == "valid_until"):
            if not isinstance(value, str):
                raise TypeError("timestamps must be ISO 8601 strings")
            text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
            try:
                instant = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError("timestamp must be ISO 8601") from exc
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("timestamp must include a UTC offset")
        return value


class NativeReference(_Task3Model):
    provider: str; account_id: str; profile_id: str; session_id: str; thread_id: str
    turn_id: str | None = None; request_id: str | None = None; tool_use_id: str | None = None; question_group_id: str | None = None

    @field_validator("provider", "account_id", "profile_id", "session_id", "thread_id")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v.strip(): raise ValueError("native reference fields must not be blank")
        return v

    @field_validator("turn_id", "request_id", "tool_use_id", "question_group_id")
    @classmethod
    def _optional_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("native reference identity fields must not be blank")
        return value

    @model_validator(mode="after")
    def _optional_chain(self):
        if self.turn_id is None and (self.request_id is not None or self.tool_use_id is not None or self.question_group_id is not None):
            raise ValueError("request/tool/question-group references require turn_id")
        if self.tool_use_id is not None and self.request_id is None:
            raise ValueError("tool_use_id requires request_id")
        if self.question_group_id is not None and self.request_id is None:
            raise ValueError("question_group_id requires request_id")
        return self


class Provenance(_Task3Model):
    source_kind: Literal["owner", "service", "system", "channel", "transcript_reconciliation"]
    source_id: str
    source_event_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    native_refs: tuple[NativeReference, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @field_validator("source_id")
    @classmethod
    def _source_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("provenance source_id must not be blank")
        return value

    @model_validator(mode="after")
    def _canonical(self):
        for name in ("source_event_ids", "source_run_ids", "evidence_ids"):
            vals = getattr(self, name)
            if vals != tuple(sorted(set(vals))): raise ValueError(f"{name} must be sorted and unique")
        keys = lambda x: tuple("" if (value := x.model_dump(mode="python").get(k)) is None else value for k in ("provider","account_id","profile_id","session_id","thread_id","turn_id","request_id","tool_use_id","question_group_id"))
        if tuple(keys(x) for x in self.native_refs) != tuple(sorted({keys(x) for x in self.native_refs})): raise ValueError("native_refs must be sorted and unique")
        return self


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not __import__('math').isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        return
    if isinstance(value, list):
        for item in value: _validate_json_value(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise TypeError("canonical JSON object keys must be strings")
        for item in value.values(): _validate_json_value(item)
        return
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")

_HEX = str
def _checksum(v: str) -> str:
    if len(v) != 64 or any(c not in "0123456789abcdef" for c in v): raise ValueError("checksum must be lowercase SHA-256")
    return v


class FrameCursor(_Task3Model):
    task_id: str; branch_id: str; frame_version: int = Field(ge=0); head_sequence: int = Field(ge=0)
    head_event_checksum: str | None = None; frame_state_checksum: str
    _head = field_validator("head_event_checksum", "frame_state_checksum")(_checksum)


class NodeUpsert(_Task3Model):
    operation_id: str; node_key: str; expected_current_node_id: str | None = None; new_node_id: str
    kind: str; text: str; value: Any; depends_on_node_keys: tuple[str, ...] = (); provenance: Provenance

    @model_validator(mode="after")
    def _deps(self):
        if self.depends_on_node_keys != tuple(sorted(set(self.depends_on_node_keys))): raise ValueError("dependencies must be sorted unique")
        return self


class NodeInvalidate(_Task3Model):
    operation_id: str; node_key: str; expected_current_node_id: str; new_node_id: str
    reason_code: "FrameInvalidationReason"; provenance: Provenance


class EdgeUpsert(_Task3Model):
    operation_id: str; relation: Literal["depends_on", "alternative_to", "supports", "contradicts"]; from_node_key: str; to_node_key: str
class EdgeRemove(EdgeUpsert): pass


class FrameChangeSet(_Task3Model):
    node_operations: tuple[NodeUpsert | NodeInvalidate, ...] = ()
    edge_operations: tuple[EdgeUpsert | EdgeRemove, ...] = ()

    @model_validator(mode="after")
    def _operations(self):
        ids = tuple(x.operation_id for x in (*self.node_operations, *self.edge_operations))
        if ids != tuple(sorted(set(ids))): raise ValueError("operation IDs must be globally sorted and unique")
        return self


class FrameNode(_Task3Model):
    node_id: str; node_key: str; task_id: str; branch_id: str; frame_version: int = Field(ge=0)
    supersedes_node_id: str | None = None; kind: str; text: str; value: Any
    status: Literal["confirmed", "invalidated"]; depends_on: tuple[str, ...] = (); provenance_event_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _canon(self):
        if self.depends_on != tuple(sorted(set(self.depends_on))): raise ValueError("depends_on must be sorted unique")
        if self.provenance_event_ids != tuple(sorted(set(self.provenance_event_ids))): raise ValueError("provenance events must be sorted unique")
        return self


class FrameEdge(_Task3Model):
    edge_id: str; task_id: str; branch_id: str; frame_version: int = Field(ge=0); from_node_id: str; to_node_id: str
    relation: Literal["depends_on", "alternative_to", "supports", "contradicts"]


class FrameState(_Task3Model):
    task_id: str; branch_id: str; frame_version: int = Field(ge=0)
    nodes: tuple[FrameNode, ...] = (); edges: tuple[FrameEdge, ...] = (); state_checksum: str
    _state = field_validator("state_checksum")(_checksum)

    @model_validator(mode="after")
    def _order(self):
        if tuple((n.node_key, n.node_id) for n in self.nodes) != tuple(sorted((n.node_key, n.node_id) for n in self.nodes)): raise ValueError("nodes must be canonical")
        if tuple((e.relation, e.from_node_id, e.to_node_id, e.edge_id) for e in self.edges) != tuple(sorted((e.relation, e.from_node_id, e.to_node_id, e.edge_id) for e in self.edges)): raise ValueError("edges must be canonical")
        return self


class FrameSnapshot(_Task3Model):
    state: FrameState; head_sequence: int = Field(ge=0); head_event_checksum: str | None = None
    _head = field_validator("head_event_checksum")(_checksum)


class RunImpact(_Task3Model):
    run_id: str; expected_revision: int = Field(ge=1); expected_state: ServiceRunState; expected_output_validity: OutputValidity; expected_last_event_id: str; expected_receipt_event_id: str | None = None
    resulting_state: ServiceRunState; resulting_output_validity: OutputValidity; reason_code: RunTransitionReason; action: Literal["pause_requested", "repairing", "require_reverification"]
class EvidenceImpact(_Task3Model):
    evidence_id: str; expected_invalidation_state: Literal["current"] = "current"; expected_source_event_id: str | None = None; expected_observed_value_checksum: str; expected_content_checksum: str | None = None; invalidation_reason: str; action: Literal["invalidate"] = "invalidate"
class ProposalSupersession(_Task3Model):
    proposal_id: str; expected_status: Literal["pending"] = "pending"; expected_created_by_event_id: str; expected_preview_state_checksum: str
class TaskCacheToken(_Task3Model):
  expected_selected_branch_id: str; expected_frame_version: int; expected_state: str; expected_updated_at: str; expected_last_transition_event_id: str | None
class TaskCacheValue(TaskCacheToken):
    pass
class CorrectionImpact(_Task3Model):
    superseded_node_ids: tuple[str, ...] = (); invalidated_node_ids: tuple[str, ...] = (); run_impacts: tuple[RunImpact, ...] = (); evidence_impacts: tuple[EvidenceImpact, ...] = (); irreversible_external_action_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _canonical_refs(self):
        for name in ("superseded_node_ids", "invalidated_node_ids", "irreversible_external_action_refs"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        for name, key in (("run_impacts", lambda item: item.run_id), ("evidence_impacts", lambda item: item.evidence_id)):
            values = getattr(self, name)
            ids = tuple(key(item) for item in values)
            if ids != tuple(sorted(set(ids))):
                raise ValueError(f"{name} must be sorted and unique by primary ID")
        return self
class FrameProposal(_Task3Model):
    proposal_id: str; base: FrameCursor; proposed_version: int; changes: FrameChangeSet; preview: FrameState; preview_state_checksum: str; impact: CorrectionImpact; impact_preview_checksum: str; status: Literal["pending", "accepted", "rejected", "superseded"]; provenance: Provenance
class OutcomeDelta(_Task3Model):
    dimension: Literal["intent", "success", "consequence", "authority", "cost", "owner_visible_ux", "external_action", "irreversibility"]; baseline: Any; candidate: Any; delegation_node_id: str | None = None
class OutcomePosition(_Task3Model):
    position_id: str; deltas: tuple[OutcomeDelta, ...]; reversible: bool; external_action_refs: tuple[str, ...] = (); external_action_authority_node_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _dimensions(self):
        dims = tuple(x.dimension for x in self.deltas); wanted = ("intent","success","consequence","authority","cost","owner_visible_ux","external_action","irreversibility")
        if dims != wanted: raise ValueError("deltas must contain fixed outcome dimensions in order")
        for name in ("external_action_refs", "external_action_authority_node_ids"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        return self


class MaterialityAssessment(_Task3Model):
    disposition: Literal["ask", "display_default", "ignore", "declaration_required"]; semantic_identity: str; changed_dimensions: tuple[OutcomeDimension, ...] = (); reason_codes: tuple[MaterialityReason, ...] = (); selected_default_option_id: str | None = None

    @model_validator(mode="after")
    def _canonical_reasons(self):
        for name in ("changed_dimensions", "reason_codes"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values), key=lambda value: value.value if isinstance(value, Enum) else value)):
                raise ValueError(f"{name} must be sorted and unique")
        return self

class DecisionOption(_Task3Model):
    option_id: str; label: str; description: str; position_id: str
class QuestionOccurrence(_Task3Model):
    occurrence_kind: Literal["question"] = "question"; occurrence_id: str; service: str; run_id: str | None = None; native_reference: NativeReference; native_question_id: str; native_question_identity_checksum: str; question_content_checksum: str; question_text: str; option_labels: tuple[str, ...]; received_at: str; provenance: Provenance; classification: Literal["accepted", "duplicate", "conflict"]
class NativeReplyIdentity(_Task3Model):
    channel: Literal["workbench", "gmail", "whatsapp", "adapter"]; provider: str; account_id: str; thread_id: str; message_id: str; native_reply_id: str
class OptionSelectionReply(_Task3Model):
    option_id: str; identity: NativeReplyIdentity
class FreeFormReply(_Task3Model):
    text: str; identity: NativeReplyIdentity
class InterpretationConfirmationReply(_Task3Model):
    interpretation_id: str; accepted: bool; identity: NativeReplyIdentity
class ReplyOccurrence(_Task3Model):
    occurrence_kind: Literal["reply"] = "reply"; occurrence_id: str; reply_kind: Literal["option_selection", "free_form", "interpretation_confirmation"]; reply: OptionSelectionReply | FreeFormReply | InterpretationConfirmationReply; native_reply_checksum: str; received_at: str; provenance: Provenance; classification: Literal["accepted", "duplicate", "conflict", "late_new"]; original_occurrence_event_id: str | None = None

    @model_validator(mode="after")
    def _reply_kind_matches(self):
        expected = {OptionSelectionReply: "option_selection", FreeFormReply: "free_form", InterpretationConfirmationReply: "interpretation_confirmation"}
        if expected[type(self.reply)] != self.reply_kind:
            raise ValueError("reply_kind must match reply member")
        return self

DecisionOccurrence = Annotated[Union[QuestionOccurrence, ReplyOccurrence], Field(discriminator="occurrence_kind")]

class StructuredDecisionInterpretation(_Task3Model):
    interpretation_id: str; decision_id: str; source_occurrence_id: str; exact_reply_text: str
    selected_option_id: str | None = None; custom_position: OutcomePosition | None = None
    interpretation_checksum: str; provenance: Provenance

    @model_validator(mode="after")
    def _exactly_one_position(self):
        if (self.selected_option_id is None) == (self.custom_position is None):
            raise ValueError("exactly one selected_option_id or custom_position is required")
        _checksum(self.interpretation_checksum)
        return self

class DecisionResolution(_Task3Model):
    resolution_id: str; decision_id: str; selected_option_id: str | None = None
    accepted_interpretation_id: str | None = None; position: OutcomePosition
    resolving_occurrence_id: str

    @model_validator(mode="after")
    def _resolution_shape(self):
        if (self.selected_option_id is None) == (self.accepted_interpretation_id is None):
            raise ValueError("resolution must identify exactly one option or accepted interpretation")
        if self.selected_option_id is not None and not self.selected_option_id.strip():
            raise ValueError("selected_option_id must not be blank")
        if self.accepted_interpretation_id is not None and not self.accepted_interpretation_id.strip():
            raise ValueError("accepted_interpretation_id must not be blank")
        return self

class DecisionResolutionResult(_Task3Model):
    decision: "DecisionRequest"; queue: "DecisionQueueSnapshot"
    resolution: DecisionResolution | None = None; interpretation: StructuredDecisionInterpretation | None = None
    accepted_event_id: str | None = None

    @model_validator(mode="after")
    def _result_shape(self):
        if (self.resolution is None) == (self.interpretation is None):
            raise ValueError("result must contain exactly one resolution or interpretation")
        if self.accepted_event_id is not None and not self.accepted_event_id.strip():
            raise ValueError("accepted_event_id must not be blank")
        return self
class NativeQuestionCandidate(_Task3Model):
    candidate_id: str; kind: DecisionKind; question: str; options: tuple[DecisionOption, ...]; free_form_allowed: bool; recommendation_option_id: str | None = None; recommendation_reason: str | None = None; changed_outcome: str; consequence_if_unresolved: str; affected_node_keys: tuple[str, ...]; tier: DecisionTier; positions: tuple[OutcomePosition, ...]; default_option_id: str | None = None; source: QuestionOccurrence

    @model_validator(mode="after")
    def _canonical_affected_nodes(self):
        if self.affected_node_keys != tuple(sorted(set(self.affected_node_keys))):
            raise ValueError("affected_node_keys must be sorted and unique")
        return self
class DecisionRequest(_Task3Model):
    decision_id: str; task_id: str; branch_id: str; revision: int = Field(ge=1); state: Literal["queued", "active", "resolved", "superseded"]; tier: DecisionTier; kind: DecisionKind; queue_order: int = Field(ge=0); semantic_identity: str; question: str; options: tuple[DecisionOption, ...]; free_form_allowed: bool; recommendation_option_id: str | None = None; recommendation_reason: str | None = None; changed_outcome: str; positions: tuple[OutcomePosition, ...]; materiality: MaterialityAssessment; consequence_if_unresolved: str; affected_node_keys: tuple[str, ...]; provenance: Provenance; source_occurrences: tuple[DecisionOccurrence, ...]; pending_interpretation: StructuredDecisionInterpretation | None = None; resolution: DecisionResolution | None = None

    @model_validator(mode="after")
    def _canonical_occurrences(self):
        keys = tuple((item.received_at, item.occurrence_id) for item in self.source_occurrences)
        if len({item.occurrence_id for item in self.source_occurrences}) != len(self.source_occurrences):
            raise ValueError("source_occurrences must have unique occurrence IDs")
        if keys != tuple(sorted(keys)):
            raise ValueError("source_occurrences must be sorted by received_at and occurrence_id")
        if not self.source_occurrences or not isinstance(self.source_occurrences[0], QuestionOccurrence) or self.source_occurrences[0].classification != "accepted":
            raise ValueError("source_occurrences must begin with an accepted question")
        return self
class DecisionQueueSnapshot(_Task3Model):
    task_id: str; branch_id: str; revision: int = Field(ge=0); checksum: str; active: DecisionRequest | None = None; queued: tuple[DecisionRequest, ...] = ()

    @model_validator(mode="after")
    def _queue_authority(self):
        if self.revision == 0:
            if self.checksum != "" or self.active is not None or self.queued:
                raise ValueError("revision 0 queue must be the empty token")
            return self
        _checksum(self.checksum)
        if self.active is None:
            if self.queued:
                raise ValueError("queued decisions require an active head")
            return self
        if self.active.task_id != self.task_id or self.active.branch_id != self.branch_id:
            raise ValueError("active decision task/branch does not match queue")
        if self.active.state != "active" or self.active.queue_order != 0:
            raise ValueError("active decision must be first queue item")
        expected = 1
        for item in self.queued:
            if item.task_id != self.task_id or item.branch_id != self.branch_id or item.state != "queued" or item.queue_order != expected:
                raise ValueError("queued decisions must match queue task/branch and contiguous order")
            expected += 1
        return self
class NativeIdentityEnvelope(_Task3Model):
    adapter_provider: str; adapter_contract_revision: str; account_id: str; profile_id: str; model_id: str; capability_inventory_revision: str; transport_generation: str; native_session_id: str; native_thread_id: str; native_turn_id: str | None = None; native_request_id: str | None = None; native_tool_use_id: str | None = None; native_question_group_id: str | None = None; launch_origin: Literal["governed"] = "governed"; native_handle: Any = None

    @field_validator("adapter_provider", "adapter_contract_revision", "account_id", "profile_id", "model_id", "capability_inventory_revision", "transport_generation", "native_session_id", "native_thread_id")
    @classmethod
    def _required_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("native identity fields must not be blank")
        return value

    @field_validator("native_turn_id", "native_request_id", "native_tool_use_id", "native_question_group_id")
    @classmethod
    def _optional_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("native identity reference fields must not be blank")
        return value

    @model_validator(mode="after")
    def _optional_chain(self):
        if self.native_turn_id is None and any(x is not None for x in (self.native_request_id, self.native_tool_use_id, self.native_question_group_id)):
            raise ValueError("native request/tool/question references require native_turn_id")
        if self.native_request_id is None and any(x is not None for x in (self.native_tool_use_id, self.native_question_group_id)):
            raise ValueError("native tool/question references require native_request_id")
        return self
class RunReceiptObservation(_Task3Model):
    receipt_id: str; run_id: str; run_revision: int; frame_version: int; input_manifest_checksum: str; receipt_kind: Literal["provider_result", "local_result", "verification_result"]; authority_locator: str; native_result_id: str | None = None; receipt_identity_checksum: str; observed_value_checksum: str; provenance: Provenance

OutcomeDimension = Literal["intent", "success", "consequence", "authority", "cost", "owner_visible_ux", "external_action", "irreversibility"]
class FrameInvalidationReason(str, Enum):
    OWNER_CORRECTION="owner_correction"; DEPENDENCY_INVALIDATED="dependency_invalidated"; AUTHORITY_REVOKED="authority_revoked"; EVIDENCE_CONFLICT="evidence_conflict"; ASSUMPTION_RETRACTED="assumption_retracted"
class Task3FailureCode(str, Enum):
    PREEXISTING="preexisting_task3_authority_without_events"; STALE_FRAME_CURSOR="stale_frame_cursor"; UNAUTHENTIC_FRAME_BASE="unauthentic_frame_base"; PROPOSAL_NOT_PENDING="proposal_not_pending"; PROPOSAL_PREVIEW_MISMATCH="proposal_preview_mismatch"; INVALID_FRAME_CHANGE="invalid_frame_change"; DEPENDENCY_CYCLE="dependency_cycle"; CROSS_BRANCH_REFERENCE="cross_branch_reference"; STALE_DECISION_REVISION="stale_decision_revision"; STALE_QUEUE_REVISION="stale_queue_revision"; INVALID_DECISION_TRANSITION="invalid_decision_transition"; INVALID_OPTION_REFERENCE="invalid_option_reference"; UNDECLARED_OUTCOME="undeclared_outcome"; NATIVE_IDENTITY_CONFLICT="native_identity_conflict"; DOMAIN_AUTHORITY_CORRUPTION="domain_authority_corruption"; INVALID_RUN_TRANSITION="invalid_run_transition"; RECEIPT_CONFLICT="receipt_conflict"; EVIDENCE_VISIBILITY_ERROR="evidence_visibility_error"; TASK_CACHE_CONFLICT="task_cache_conflict"

def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def native_question_identity_checksum(*, service: str, run_id: str | None, native_reference: NativeReference, native_question_id: str) -> str:
    envelope = {"domain":"workbench.decision.native_question.v1", "service":service, "run_id":run_id, "native_reference":native_reference.model_dump(mode="json"), "native_question_id":native_question_id}
    return hashlib.sha256(_canonical(envelope)).hexdigest()

DecisionRequest.model_rebuild()
DecisionQueueSnapshot.model_rebuild()
DecisionResolutionResult.model_rebuild()
NodeInvalidate.model_rebuild()
FrameChangeSet.model_rebuild()
FrameProposal.model_rebuild()
