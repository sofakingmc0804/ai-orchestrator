from __future__ import annotations

from typing import Any, Protocol

from orchestrator.models import BillingClass, Capability, ServiceInfo


class Adapter(Protocol):
    name: str

    async def health_probe(self) -> ServiceInfo: ...

    async def capabilities(self) -> list[Capability]: ...

    async def cost_estimate(self, capability_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]: ...


def safe_cost(billing_class: BillingClass) -> dict[str, Any]:
    return {"billing_class": billing_class.value, "estimated_units": 0, "metered_extra_cost": False}

