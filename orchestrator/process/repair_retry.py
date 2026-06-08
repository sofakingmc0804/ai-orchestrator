from __future__ import annotations

from typing import Any

from orchestrator.config import Settings
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.process.recovery import repair_lm_studio_local_server
from orchestrator.state.store import StateStore


async def retry_open_repairs(settings: Settings, store: StateStore) -> dict[str, Any]:
    open_items = await store.list_repair_queue()
    results: list[dict[str, Any]] = []
    sources = {str(item.get("failure_source") or "") for item in open_items}

    if "lm-studio" in sources:
        repair = await repair_lm_studio_local_server(store)
        proof: dict[str, Any] | None = None
        if repair.get("state") == "healthy":
            services, capabilities = await discover_services_and_capabilities()
            await store.upsert_services(services)
            await store.upsert_capabilities(capabilities)
            dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
            proof_result = await dispatcher.prove_adapter(
                "lm-studio",
                "LM Studio repair proof: answer OK.",
                capability_id="local_chat",
                allow_subscription=False,
            )
            proof = proof_result.model_dump(mode="json")
        results.append({"failure_source": "lm-studio", "repair": repair, "proof": proof})

    remaining = await store.list_repair_queue()
    return {
        "attempted": len(results),
        "results": results,
        "remaining_open_repairs": remaining,
    }
