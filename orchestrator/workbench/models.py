from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
