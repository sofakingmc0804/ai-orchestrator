from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from orchestrator.workbench.models import (
    EventDraft,
    EventValidationError,
    UnsupportedEventSchema,
    UnsupportedEventType,
)


GENESIS_CHECKSUM = hashlib.sha256(b"ai-orchestrator/workbench-ledger/genesis/v1").hexdigest()
EVENT_SCHEMA_VERSION = 1


class FrameEffect(str, Enum):
    INHERIT = "inherit"
    PROPOSE = "propose"
    CONFIRM = "confirm"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


Reducer = Callable[[dict[str, Any], Any], dict[str, Any]]


@dataclass(frozen=True)
class EventDefinition:
    event_type: str
    event_schema_version: int
    payload_model: type[BaseModel]
    frame_effect: FrameEffect
    reducer: Reducer


@dataclass(frozen=True)
class ValidatedDraft:
    draft: EventDraft
    definition: EventDefinition
    payload: BaseModel


class EventRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, int], EventDefinition] = {}

    @classmethod
    def production(cls) -> "EventRegistry":
        registry = cls()
        registry.register(
            EventDefinition(
                event_type="task.created",
                event_schema_version=1,
                payload_model=TaskCreatedPayload,
                frame_effect=FrameEffect.INHERIT,
                reducer=_reduce_task_created,
            )
        )
        registry.register(
            EventDefinition(
                event_type="branch.forked",
                event_schema_version=1,
                payload_model=BranchForkedPayload,
                frame_effect=FrameEffect.INHERIT,
                reducer=_reduce_branch_forked,
            )
        )
        return registry

    def copy(self) -> "EventRegistry":
        registry = EventRegistry()
        registry._definitions = dict(self._definitions)
        return registry

    def register(self, definition: EventDefinition) -> None:
        key = (definition.event_type, definition.event_schema_version)
        if not definition.event_type.strip() or definition.event_schema_version < 1:
            raise ValueError("event definition identity is invalid")
        if key in self._definitions:
            raise ValueError(f"event definition is already registered: {key}")
        self._definitions[key] = definition

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
