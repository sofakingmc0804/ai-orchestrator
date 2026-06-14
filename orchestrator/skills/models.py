from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ConfirmationState = Literal["not_required", "required", "confirmed", "blocked"]
AuthorityStatus = Literal["passed", "required", "blocked", "unknown"]
EnforcementMode = Literal["advisory", "hard_deny"]


class SelectedSkill(BaseModel):
    name: str
    path: str | None = None
    reason: str
    primary_group: str | None = None
    tool_requirements: list[str] = Field(default_factory=list)


class AuthorityCheck(BaseModel):
    name: str
    status: AuthorityStatus
    reason: str
    required_tool: str | None = None
    expires_at: str | None = None
    scope_cwd: str | None = None


class InterpretedAction(BaseModel):
    verb: str
    target: str
    domain: str
    mutates: bool = False
    consequence: str = "low"


class SkillHookPlan(BaseModel):
    id: str = Field(default_factory=lambda: f"shp_{uuid.uuid4().hex[:16]}")
    prompt: str
    cwd: str | None = None
    source_event: str = "detector"
    interaction_type: str
    domain: str
    consequence: str
    confidence: float = Field(ge=0.0, le=1.0)
    selected_skills: list[SelectedSkill] = Field(default_factory=list)
    interpreted_actions: list[InterpretedAction] = Field(default_factory=list)
    authority_checks: list[AuthorityCheck] = Field(default_factory=list)
    confirmation_state: ConfirmationState = "not_required"
    question: str | None = None
    terminal_state_requirement: str = "investigated_and_routed"
    enforcement_mode: EnforcementMode = "advisory"
    requires_receipt: bool = True
    created_at: str = Field(default_factory=utc_iso)

    @property
    def selected_skill_names(self) -> list[str]:
        return [skill.name for skill in self.selected_skills]

    @property
    def has_mutating_action(self) -> bool:
        return any(action.mutates for action in self.interpreted_actions)

    def receipt_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "cwd": self.cwd,
            "source_event": self.source_event,
            "interaction_type": self.interaction_type,
            "domain": self.domain,
            "consequence": self.consequence,
            "confidence": self.confidence,
            "selected_skills": [skill.model_dump(mode="json") for skill in self.selected_skills],
            "interpreted_actions": [action.model_dump(mode="json") for action in self.interpreted_actions],
            "authority_checks": [check.model_dump(mode="json") for check in self.authority_checks],
            "confirmation_state": self.confirmation_state,
            "question": self.question,
            "terminal_state_requirement": self.terminal_state_requirement,
            "enforcement_mode": self.enforcement_mode,
            "requires_receipt": self.requires_receipt,
            "created_at": self.created_at,
        }


class HookDecision(BaseModel):
    hook_event_name: str
    should_block: bool = False
    reason: str | None = None
    additional_context: str | None = None
    permission_decision: str | None = None
    updated_input: dict[str, Any] | None = None


def normalize_cwd(cwd: str | Path | None) -> str | None:
    if cwd is None:
        return None
    try:
        return str(Path(cwd).expanduser().resolve())
    except OSError:
        return str(Path(cwd).expanduser().absolute())
