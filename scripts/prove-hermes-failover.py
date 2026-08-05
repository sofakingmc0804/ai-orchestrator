"""Run one live, no-cost provider-failover proof and persist its receipt."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orchestrator.dispatch.dispatcher as dispatcher_module
from orchestrator.adapters.builtins import OllamaHttpAdapter
from orchestrator.config import Settings
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.models import BillingClass, Capability, ConsequenceTier, RoutingDecision
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.state.store import StateStore


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_HOME = REPO_ROOT / ".runtime" / "orchestrator"
INVALID_OPENROUTER_MODEL = "openrouter/hermes-failover-invalid-model"


def _env_value(name: str) -> str | None:
    env_path = Path.home() / "AppData" / "Local" / "hermes" / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'") or None
    return None


class ControlledOpenRouterFailure:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.calls = 0

    async def dispatch(self, _envelope: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        payload = {
            "model": INVALID_OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": "Return exactly FAILOVER_PRIMARY"}],
            "stream": False,
            "max_tokens": 8,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        return {
            "ok": False,
            "error": f"Controlled OpenRouter primary failure (HTTP {response.status_code})",
            "raw": {"provider": "openrouter", "model": INVALID_OPENROUTER_MODEL, "status": response.status_code},
        }


def _capability(adapter: str, billing: BillingClass, quality: int) -> Capability:
    return Capability(
        id=f"{adapter}:classify_text",
        adapter_name=adapter,
        capability_id="classify_text",
        rating_instruction=3,
        rating_quality=quality,
        latency_band="medium",
        consequence_max=ConsequenceTier.MEDIUM,
        billing_class=billing,
    )


async def run() -> dict[str, Any]:
    key = _env_value("OPENROUTER_API_KEY")
    if not key:
        return {"ok": False, "terminal_state": "continuation_required", "error": "OpenRouter key is not present in Hermes env."}

    with tempfile.TemporaryDirectory(prefix="hermes-failover-") as temp_dir:
        temp_root = Path(temp_dir)
        settings = Settings(
            home=temp_root / "state",
            state_path=temp_root / "state.sqlite",
            notifications_path=temp_root / "notifications.jsonl",
            log_dir=temp_root / "logs",
            repo_root=REPO_ROOT,
        )
        store = StateStore(settings)
        await store.initialize()
        primary = _capability("openrouter-live", BillingClass.SUBSCRIPTION_USAGE, 5).model_dump(mode="json")
        fallback = _capability("ollama-http", BillingClass.LOCAL_RESOURCE, 4).model_dump(mode="json")
        await store.upsert_capabilities([Capability.model_validate(primary), Capability.model_validate(fallback)])

        original_route = dispatcher_module.route_intent

        def forced_live_route(intent, _capabilities, _quota_state=None, _project_policy=None, _score_contract=None):
            return RoutingDecision(
                intent_id=intent.id,
                chosen_adapter="openrouter-live",
                candidates_considered=[primary, fallback],
                candidates_rejected=[],
                reasoning="Controlled live failover proof: OpenRouter primary then local Ollama fallback.",
            )

        dispatcher_module.route_intent = forced_live_route
        try:
            dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
            dispatcher.max_attempts_per_adapter = 1
            dispatcher.retry_backoff_seconds = [0]
            openrouter = ControlledOpenRouterFailure(key)
            dispatcher.adapters = {"openrouter-live": openrouter, "ollama-http": OllamaHttpAdapter()}
            result = await dispatcher.dispatch_text("Return exactly FAILOVER_OK", job_class_override="simple_coding")
            attempts = await store.list_dispatch_attempts(result.dispatch_id)
            receipt = {
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "proof_kind": "live",
                "terminal_state": "passed" if result.state == "completed" and openrouter.calls == 1 else "failed",
                "test": "OpenRouter controlled invalid-model failure falls through to local Ollama.",
                "state": result.state,
                "selected_adapter": result.adapter_name,
                "result_text": result.result_text,
                "primary_calls": openrouter.calls,
                "failover_ladder": [
                    {"adapter_name": row.get("adapter_name"), "billing_class": row.get("billing_class")}
                    for row in result.receipt.get("failover_ladder", [])
                ],
                "attempts": [
                    {
                        "adapter_name": row.get("adapter_name"),
                        "state": row.get("state"),
                        "attempt_number": row.get("attempt_number"),
                        "error": row.get("error"),
                    }
                    for row in attempts
                ],
                "dispatch_receipt": result.receipt,
            }
        finally:
            dispatcher_module.route_intent = original_route

    path = ORCHESTRATOR_HOME / "receipts" / f"hermes-failover-proof-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt["receipt_path"] = str(path)
    path.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2, default=str))
