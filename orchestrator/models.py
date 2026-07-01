from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BillingClass(StrEnum):
    LOCAL_RESOURCE = "local_resource"
    SUBSCRIPTION_UNLIMITED = "subscription_unlimited"
    SUBSCRIPTION_QUOTA = "subscription_quota"
    SUBSCRIPTION_USAGE = "subscription_usage"
    METERED_EXTRA_COST = "metered_extra_cost"
    UNKNOWN_COST = "unknown_cost"


class ConsequenceTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class ServiceInfo(BaseModel):
    id: str
    name: str
    service_group: str
    adapter_name: str
    protocol: str
    install_path: str | None = None
    version: str | None = None
    health_state: HealthState = HealthState.UNKNOWN
    # Human-readable reason for the current health_state (why it is degraded/stopped).
    # Always populate this when health_state is not HEALTHY so the dashboard never
    # shows a bare status with no explanation.
    detail: str | None = None
    # Concrete, owner-facing next step to restore the service. Populate alongside
    # `detail` for any non-healthy state.
    repair_action: str | None = None
    # True for services that are intentionally not running (retired, archived, or
    # synthetic test fixtures). Optional services are reported but must NOT drag the
    # aggregate system health to "degraded".
    optional: bool = False
    pid: int | None = None
    memory_mb: float | None = None
    uptime_seconds: float | None = None
    last_probe_at: datetime = Field(default_factory=utc_now)


class Capability(BaseModel):
    id: str
    adapter_name: str
    capability_id: str
    rating_instruction: int = Field(ge=0, le=5)
    rating_quality: int = Field(ge=0, le=5)
    latency_band: str
    consequence_max: ConsequenceTier
    billing_class: BillingClass
    provider: str = ""
    enabled: bool = True
    benchmark_score: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_model: str | None = None


class Selection(BaseModel):
    id: str
    kind: str
    payload: dict[str, Any]
    grouped_with: str | None = None
    added_at: datetime = Field(default_factory=utc_now)


class Intent(BaseModel):
    id: str
    source: str
    raw_text: str
    parsed_payload: dict[str, Any]
    project_id: str | None = None
    selections: list[Selection] = Field(default_factory=list)
    consequence_tier: ConsequenceTier = ConsequenceTier.LOW
    state: str = "pending"
    created_at: datetime = Field(default_factory=utc_now)


class RoutingDecision(BaseModel):
    intent_id: str
    chosen_adapter: str | None
    candidates_considered: list[dict[str, Any]]
    candidates_rejected: list[dict[str, Any]]
    reasoning: str
    decided_at: datetime = Field(default_factory=utc_now)


class DispatchResult(BaseModel):
    dispatch_id: str
    intent_id: str
    adapter_name: str
    state: str
    output_path: Path | None = None
    result_text: str | None = None
    error: str | None = None
    receipt: dict[str, Any] = Field(default_factory=dict)


class Notification(BaseModel):
    id: str
    ts: datetime = Field(default_factory=utc_now)
    severity: str
    project_id: str | None = None
    intent_id: str | None = None
    title: str
    body: str
    actions: list[dict[str, str]] = Field(default_factory=list)
    channels_requested: list[str] = Field(default_factory=lambda: ["in_app"])
