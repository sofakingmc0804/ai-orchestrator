from __future__ import annotations

import asyncio

from orchestrator.adapters.builtins import build_adapters
from orchestrator.models import Capability, HealthState, ServiceInfo


async def _probe_adapter(adapter: object) -> tuple[ServiceInfo, list[Capability]]:
    try:
        health = await adapter.health_probe()  # type: ignore[attr-defined]
        caps = await adapter.capabilities()  # type: ignore[attr-defined]
        return health, caps
    except Exception as exc:
        name = str(getattr(adapter, "name", "unknown-adapter"))
        label = str(getattr(adapter, "label", name))
        service_group = str(getattr(adapter, "service_group", "unknown"))
        protocol = str(getattr(adapter, "protocol", "unknown"))
        return (
            ServiceInfo(
                id=name,
                name=label,
                service_group=service_group,
                adapter_name=name,
                protocol=protocol,
                health_state=HealthState.DEGRADED,
                detail=f"Health probe raised an exception: {exc}",
                repair_action=(
                    f"Inspect the {label} adapter's health_probe for the error above; "
                    "the probe itself failed, so this state is not a verified service status."
                ),
                version=f"probe failed: {exc}",
            ),
            [],
        )


async def discover_services_and_capabilities() -> tuple[list[ServiceInfo], list[Capability]]:
    services: list[ServiceInfo] = []
    capabilities: list[Capability] = []
    results = await asyncio.gather(*[_probe_adapter(adapter) for adapter in build_adapters().values()])
    for service, caps in results:
        services.append(service)
        capabilities.extend(caps)
    return services, capabilities
