from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.state.store import StateStore, iso


async def run_selection_latency_benchmark(settings: Settings, prompt: str | None = None) -> dict[str, Any]:
    store = StateStore(settings)
    await store.initialize()
    services, capabilities = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(capabilities)
    benchmark_root = settings.home / "benchmarks"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    sample = benchmark_root / "selection-latency-input.txt"
    sample.write_text("orchestrator latency benchmark input", encoding="utf-8")
    selection = await store.add_selection("file", {"path": str(sample), "exists": True, "source": "benchmark"})
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    started = time.perf_counter()
    result = await dispatcher.dispatch_text(prompt or "Classify this local benchmark text and reply with one short label.")
    elapsed = time.perf_counter() - started
    receipt = {
        "benchmark": "selection_to_output_latency",
        "selection_id": selection.id,
        "intent_id": result.intent_id,
        "dispatch_id": result.dispatch_id,
        "adapter_name": result.adapter_name,
        "state": result.state,
        "elapsed_seconds": round(elapsed, 3),
        "threshold_seconds": 60,
        "passed": result.state == "completed" and elapsed < 60,
        "output_path": str(result.output_path) if result.output_path else None,
        "recorded_at": iso(),
    }
    receipt_path = benchmark_root / f"selection-latency-{int(time.time())}.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    await store.audit("benchmark", "selection_to_output_latency", result.intent_id, receipt)
    return receipt
