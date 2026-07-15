from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, get_origin

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from orchestrator.workbench.models import (
    CorrectionImpact, DecisionQueueSnapshot, DecisionRequest, FrameCursor, FrameProposal,
    FrameState, MaterialityAssessment, NativeQuestionCandidate, NativeIdentityEnvelope,
    ReplyOccurrence, RunReceiptObservation, TaskCacheToken, ProposalSupersession,
    StructuredDecisionInterpretation, DecisionResolution,
    EventDraft,
    EventValidationError,
    UnsupportedEventSchema,
    UnsupportedEventType,
    ServiceRunState, OutputValidity, RunTransitionReason, DecisionSupersessionReason,
    _normalize_enum_wire,
)


GENESIS_CHECKSUM = hashlib.sha256(b"ai-orchestrator/workbench-ledger/genesis/v1").hexdigest()
EVENT_SCHEMA_VERSION = 1
CORE_PARTICIPANT_ID = "core"


class FrameEffect(str, Enum):
    INHERIT = "inherit"
    PROPOSE = "propose"
    CONFIRM = "confirm"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _json_arrays_to_tuples(cls, data):
        if not isinstance(data, Mapping):
            return data
        values = dict(data)
        for name, field in cls.model_fields.items():
            if name in values and get_origin(field.annotation) is tuple and isinstance(values[name], list):
                values[name] = tuple(values[name])
            if name in values:
                values[name] = _normalize_enum_wire(values[name], field.annotation)
        return values

    @field_validator("*", mode="after")
    @classmethod
    def _strict_json_and_checksums(cls, value, info):
        name = info.field_name or ""
        if name.endswith("_checksum") and value is not None:
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("checksum must be lowercase SHA-256")
        if (name == "queue_order" or name.endswith("_revision")) and value is not None:
            if not isinstance(value, int) or value < 0:
                raise ValueError("revisions and queue orders must be nonnegative integers")
        field = cls.model_fields.get(name)
        if field is not None and field.annotation is Any:
            _validate_json_domain(value)
        return value


class TaskCreatedPayload(_Payload):
    title: str = Field(min_length=1)
    initial_branch_id: str = Field(min_length=1)

    @field_validator("title", "initial_branch_id")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task title and initial branch must not be blank")
        return value


class BranchForkedPayload(_Payload):
    new_branch_id: str = Field(min_length=1)
    parent_branch_id: str = Field(min_length=1)
    forked_from_sequence: int = Field(ge=1)
    forked_from_frame_version: int = Field(ge=0)

    @field_validator("new_branch_id", "parent_branch_id")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("branch identities must not be blank")
        return value


# Task 3 uses one closed payload type per event.  The nested domain models are
# intentionally strict; cross-row authority checks remain in intent_core.
class FrameProposalCreatedPayload(_Payload): proposal: FrameProposal
class FrameProposalRejectedPayload(_Payload): proposal_id: str; expected_preview_state_checksum: str; reason: str
class FrameChangeConfirmedPayload(_Payload):
    proposal_id: str; expected_base: FrameCursor; expected_preview_state_checksum: str; expected_impact_preview_checksum: str
    confirmed: FrameState; impact: CorrectionImpact; proposal_supersessions: tuple[ProposalSupersession, ...]; expected_task_cache: TaskCacheToken | None = None; resulting_task_state: str
class BranchActivatedPayload(_Payload):
    source_branch_id: str; target_branch_id: str; expected_global_head_sequence: int; expected_global_head_checksum: str
    expected_source_last_transition_event_id: str; expected_task_cache: TaskCacheToken; target_frame_version: int; target_frame_state_checksum: str; resulting_task_state: str
class QuestionDispositionPayload(_Payload): candidate: NativeQuestionCandidate; assessment: MaterialityAssessment
class QuestionNativeRepeatedPayload(_Payload):
    candidate: NativeQuestionCandidate; classification: Literal["duplicate", "conflict"]; original_occurrence_event_id: str
    native_question_identity_checksum: str; original_question_content_checksum: str; observed_question_content_checksum: str
class DecisionEnqueuedPayload(_Payload):
    candidate: NativeQuestionCandidate; assessment: MaterialityAssessment; decision: DecisionRequest; expected_queue_revision: int; expected_queue_checksum: str
    expected_task_cache: TaskCacheToken | None = None; resulting_queue: DecisionQueueSnapshot; resulting_task_state: str
class DecisionSourceMergedPayload(_Payload):
    decision_id: str; candidate: NativeQuestionCandidate; assessment: MaterialityAssessment; expected_decision_revision: int; expected_queue_revision: int; resulting_decision_revision: int; resulting_queue: DecisionQueueSnapshot
class DecisionQueueReorderedPayload(_Payload):
    expected_queue_revision: int; expected_queue_checksum: str; queued_decision_ids: tuple[str, ...]; resulting_queue: DecisionQueueSnapshot
class DecisionFreeFormReceivedPayload(_Payload):
    decision_id: str; occurrence: ReplyOccurrence; expected_decision_revision: int; expected_queue_revision: int; resulting_decision: DecisionRequest; resulting_queue: DecisionQueueSnapshot
class DecisionReplyRepeatedPayload(DecisionFreeFormReceivedPayload): pass
class DecisionInterpretationProposedPayload(_Payload):
    decision_id: str; interpretation: StructuredDecisionInterpretation; expected_decision_revision: int; expected_queue_revision: int; resulting_decision_revision: int; resulting_queue: DecisionQueueSnapshot
class DecisionInterpretationRejectedPayload(_Payload):
    decision_id: str; occurrence: ReplyOccurrence; reason: str | None = None; expected_decision_revision: int; expected_queue_revision: int; resulting_decision: DecisionRequest; resulting_queue: DecisionQueueSnapshot
class DecisionResolvedPayload(_Payload):
    decision_id: str; occurrence: ReplyOccurrence; resolution: DecisionResolution; expected_decision_revision: int; expected_queue_revision: int; expected_task_cache: TaskCacheToken | None = None; resulting_decision: DecisionRequest; resulting_queue: DecisionQueueSnapshot; next_activated_decision_id: str | None = None; resulting_task_state: str
class DecisionSupersededPayload(_Payload):
    decision_id: str; reason: DecisionSupersessionReason; expected_decision_revision: int; expected_queue_revision: int; expected_task_cache: TaskCacheToken | None = None; resulting_decision: DecisionRequest; resulting_queue: DecisionQueueSnapshot; next_activated_decision_id: str | None = None; resulting_task_state: str
class DecisionLateReplyPayload(_Payload):
    decision_id: str; occurrence: ReplyOccurrence; terminal_authority_event_id: str; expected_decision_revision: int; resulting_decision: DecisionRequest
class ServiceRunRegisteredPayload(_Payload):
    run_id: str; service: str; role: str; task_id: str; branch_id: str; frame_version: int; input_manifest: Any; input_node_ids: tuple[str, ...]; initial_state: Literal["queued"]; output_validity: OutputValidity; native_identity_envelope: NativeIdentityEnvelope | None = None; expected_task_cache: TaskCacheToken | None = None; resulting_task_state: str
class ServiceRunReceiptRecordedPayload(_Payload):
    run_id: str; expected_revision: int; expected_state: Literal["running", "verifying"]; receipt: RunReceiptObservation; resulting_revision: int
class ServiceRunStateChangedPayload(_Payload):
    run_id: str; expected_revision: int; expected_state: ServiceRunState; expected_output_validity: OutputValidity; new_state: ServiceRunState; resulting_output_validity: OutputValidity; reason_code: RunTransitionReason; native_identity_attachment: NativeIdentityEnvelope | None = None; expected_task_cache: TaskCacheToken | None = None; resulting_task_state: str
class EvidenceRecordedPayload(_Payload):
    evidence_id: str; task_id: str; branch_id: str; success_node_id: str; success_node_frame_version: int; frame_version: int; observed_head_event_id: str; observed_head_sequence: int; predicate_id: str; predicate_text: str | None = None; predicate_json: Any = None; expected_outcome: Any; authority_kind: str; authority_locator: str; verifier_kind: str; verifier_identity: str; verification_status: str; observed_at: str; valid_until: str | None = None; observed_value_checksum: str; content_checksum: str | None = None; source_event_id: str | None = None; source_event_sequence: int | None = None; producing_run_id: str | None = None; metadata: Any = None


Reducer = Callable[[dict[str, Any], Any], dict[str, Any]]


@dataclass(frozen=True)
class EventDefinition:
    event_type: str
    event_schema_version: int
    payload_model: type[BaseModel]
    frame_effect: FrameEffect
    reducer: Reducer
    authority_participant: str


@dataclass(frozen=True)
class ValidatedDraft:
    draft: EventDraft
    definition: EventDefinition
    payload: BaseModel


class EventRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, int], EventDefinition] = {}
        self._frozen = False

    @classmethod
    def production(cls, *, include_task3: bool = False) -> "EventRegistry":
        registry = cls()
        registry.register(
            EventDefinition(
                event_type="task.created",
                event_schema_version=1,
                payload_model=TaskCreatedPayload,
                frame_effect=FrameEffect.INHERIT,
                reducer=_reduce_task_created,
                authority_participant=CORE_PARTICIPANT_ID,
            )
        )
        registry.register(
            EventDefinition(
                event_type="branch.forked",
                event_schema_version=1,
                payload_model=BranchForkedPayload,
                frame_effect=FrameEffect.INHERIT,
                reducer=_reduce_branch_forked,
                authority_participant=CORE_PARTICIPANT_ID,
            )
        )
        if not include_task3:
            return registry
        catalog = {
            "frame.proposal_created": (FrameProposalCreatedPayload, FrameEffect.PROPOSE), "frame.proposal_rejected": (FrameProposalRejectedPayload, FrameEffect.INHERIT), "frame.change_confirmed": (FrameChangeConfirmedPayload, FrameEffect.CONFIRM), "branch.activated": (BranchActivatedPayload, FrameEffect.INHERIT),
            "question.declaration_required": (QuestionDispositionPayload, FrameEffect.INHERIT), "question.default_displayed": (QuestionDispositionPayload, FrameEffect.INHERIT), "question.ignored": (QuestionDispositionPayload, FrameEffect.INHERIT), "question.native_repeated": (QuestionNativeRepeatedPayload, FrameEffect.INHERIT),
            "decision.enqueued": (DecisionEnqueuedPayload, FrameEffect.INHERIT), "decision.source_merged": (DecisionSourceMergedPayload, FrameEffect.INHERIT), "decision.queue_reordered": (DecisionQueueReorderedPayload, FrameEffect.INHERIT), "decision.free_form_received": (DecisionFreeFormReceivedPayload, FrameEffect.INHERIT), "decision.reply_repeated": (DecisionReplyRepeatedPayload, FrameEffect.INHERIT), "decision.interpretation_proposed": (DecisionInterpretationProposedPayload, FrameEffect.INHERIT), "decision.interpretation_rejected": (DecisionInterpretationRejectedPayload, FrameEffect.INHERIT), "decision.resolved": (DecisionResolvedPayload, FrameEffect.INHERIT), "decision.superseded": (DecisionSupersededPayload, FrameEffect.INHERIT), "decision.late_reply_recorded": (DecisionLateReplyPayload, FrameEffect.INHERIT),
            "service_run.registered": (ServiceRunRegisteredPayload, FrameEffect.INHERIT), "service_run.receipt_recorded": (ServiceRunReceiptRecordedPayload, FrameEffect.INHERIT), "service_run.state_changed": (ServiceRunStateChangedPayload, FrameEffect.INHERIT), "evidence.recorded": (EvidenceRecordedPayload, FrameEffect.INHERIT),
        }
        for event_type, (payload_model, frame_effect) in catalog.items():
            registry.register(EventDefinition(event_type, 1, payload_model, frame_effect, _reduce_passthrough, "intent_core"))
        return registry

    @classmethod
    def task2(cls) -> "EventRegistry":
        return cls.production(include_task3=False)

    @classmethod
    def production_task3(cls) -> "EventRegistry":
        return cls.production(include_task3=True)

    def copy(self) -> "EventRegistry":
        registry = EventRegistry()
        registry._definitions = dict(self._definitions)
        return registry

    def definitions(self) -> dict[tuple[str, int], EventDefinition]:
        return dict(self._definitions)

    def ownership_fingerprint(self) -> tuple[tuple[str, int, str], ...]:
        return tuple(
            sorted(
                (event_type, version, definition.authority_participant)
                for (event_type, version), definition in self._definitions.items()
            )
        )

    def semantic_fingerprint(self) -> tuple[tuple[Any, ...], ...]:
        return tuple(
            sorted(
                (
                    event_type,
                    version,
                    id(definition),
                    id(definition.payload_model),
                    id(definition.reducer),
                    definition.frame_effect.value,
                    definition.authority_participant,
                )
                for (event_type, version), definition in self._definitions.items()
            )
        )

    def register(self, definition: EventDefinition) -> None:
        if self._frozen:
            raise ValueError("event registry is frozen")
        key = (definition.event_type, definition.event_schema_version)
        if not definition.event_type.strip() or definition.event_schema_version < 1:
            raise ValueError("event definition identity is invalid")
        if not definition.authority_participant.strip():
            raise ValueError("event definition authority participant is invalid")
        if key in self._definitions:
            raise ValueError(f"event definition is already registered: {key}")
        self._definitions[key] = definition

    def freeze(self) -> None:
        if self._frozen:
            return
        self._definitions = MappingProxyType(dict(self._definitions))  # type: ignore[assignment]
        self._frozen = True

    def definition(self, event_type: str, event_schema_version: int) -> EventDefinition:
        definition = self._definitions.get((event_type, event_schema_version))
        if definition is not None:
            return definition
        if any(name == event_type for name, _ in self._definitions):
            raise UnsupportedEventSchema(
                f"unsupported schema version {event_schema_version} for event type {event_type}"
            )
        raise UnsupportedEventType(f"unsupported event type: {event_type}")

    def validate(self, draft: EventDraft) -> ValidatedDraft:
        definition = self.definition(draft.event_type, draft.event_schema_version)
        try:
            payload = definition.payload_model.model_validate(draft.payload)
        except ValidationError as exc:
            raise EventValidationError(f"invalid {draft.event_type} payload: {exc}") from exc
        # Round-trip through JSON mode so reducers and stored event payloads see
        # only the declared structural representation.
        canonical_json_bytes(payload.model_dump(mode="json"))
        return ValidatedDraft(draft=draft, definition=definition, payload=payload)


def _reduce_task_created(state: dict[str, Any], event: Any) -> dict[str, Any]:
    result = deepcopy(state)
    result["task"] = {
        "task_id": event.task_id,
        "title": event.payload["title"],
        "initial_branch_id": event.payload["initial_branch_id"],
    }
    return result


def _reduce_branch_forked(state: dict[str, Any], event: Any) -> dict[str, Any]:
    result = deepcopy(state)
    branches = dict(result.get("forks", {}))
    payload = event.payload
    branches[payload["new_branch_id"]] = {
        "parent_branch_id": payload["parent_branch_id"],
        "forked_from_sequence": payload["forked_from_sequence"],
        "forked_from_frame_version": payload["forked_from_frame_version"],
    }
    result["forks"] = branches
    return result


def _reduce_passthrough(state: dict[str, Any], event: Any) -> dict[str, Any]:
    result = deepcopy(state)
    result.setdefault("events", []).append(event.event_type)
    return result


def _validate_json_domain(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            _validate_json_domain(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_domain(item)
        return
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the sole canonical JSON representation used by the ledger."""
    _validate_json_domain(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical JSON: {exc}") from exc
    return encoded.encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def normalize_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            instant = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("timestamp must be ISO 8601") from exc
    elif isinstance(value, datetime):
        instant = value
    else:
        raise TypeError("timestamp must be a datetime or ISO 8601 string")
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def event_checksum(envelope: Mapping[str, Any]) -> str:
    """Hash the canonical immutable envelope, which must not contain checksum."""
    if "checksum" in envelope:
        raise ValueError("checksum is not part of its own immutable envelope")
    return hashlib.sha256(canonical_json_bytes(dict(envelope))).hexdigest()


def draft_envelope(ordinal: int, item: ValidatedDraft) -> dict[str, Any]:
    draft = item.draft
    return {
        "ordinal": ordinal,
        "event_type": draft.event_type,
        "event_schema_version": draft.event_schema_version,
        "actor_kind": draft.actor.kind,
        "actor_id": draft.actor.actor_id,
        "branch_id": draft.branch_id,
        "cause": draft.cause.value if draft.cause is not None else None,
        "caused_by": draft.caused_by,
        "payload": item.payload.model_dump(mode="json"),
    }


def drafts_checksum(
    task_id: str,
    command_id: str,
    drafts: tuple[ValidatedDraft, ...] | list[ValidatedDraft],
) -> str:
    envelope = {
        "domain": "workbench.command.drafts.v1",
        "task_id": task_id,
        "command_id": command_id,
        "drafts": [draft_envelope(ordinal, item) for ordinal, item in enumerate(drafts, start=1)],
    }
    return hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()


_MANIFEST_FIELDS = {
    "task_id",
    "command_id",
    "target_branch_id",
    "event_count",
    "first_sequence",
    "last_sequence",
    "first_event_id",
    "last_event_id",
    "starting_frame_version",
    "expected_frame_version",
    "confirm_ordinal",
    "drafts_checksum",
    "created_at",
}


def manifest_checksum(fields: Mapping[str, Any]) -> str:
    values = dict(fields)
    if set(values) != _MANIFEST_FIELDS:
        missing = sorted(_MANIFEST_FIELDS - set(values))
        extra = sorted(set(values) - _MANIFEST_FIELDS)
        raise ValueError(f"manifest checksum fields mismatch; missing={missing}, extra={extra}")
    envelope = {"domain": "workbench.command.manifest.v1", **values}
    return hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
