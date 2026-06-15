from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.reports.acceptance_battery import run_acceptance_battery
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_synthetic_adapter_proof_satisfies_service_registration_target(tmp_path: Path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=Path.cwd(),
    )
    store = StateStore(settings)
    await store.initialize()
    services, caps = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(caps)

    unproved = await evaluate_spec_status(settings, store)
    unproved_ct21 = next(row for row in unproved["targets"] if row["id"] == "CT-21")
    assert unproved_ct21["status"] == "partial"
    assert any("synthetic_dispatch_proven=False" in evidence for evidence in unproved_ct21["evidence"])

    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    result = await dispatcher.prove_adapter(
        "synthetic-test-service",
        "Synthetic test proof.",
        capability_id="synthetic_echo",
    )

    assert result.state == "completed"
    assert result.result_text == "synthetic_echo: Synthetic test proof."
    proved = await evaluate_spec_status(settings, store)
    proved_ct21 = next(row for row in proved["targets"] if row["id"] == "CT-21")
    assert proved_ct21["status"] == "passed"
    assert any("synthetic_dispatch_proven=True" in evidence for evidence in proved_ct21["evidence"])


@pytest.mark.asyncio
async def test_acceptance_battery_writes_signed_owner_receipt(tmp_path: Path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=Path.cwd(),
    )

    result = await run_acceptance_battery(settings)

    receipt_path = Path(str(result["receipt_path"]))
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_path.exists()
    assert payload["event"] == "acceptance_battery"
    assert payload["proof_kind"] == "live"
    assert len(payload["checks"]) == 8
    assert payload["signature_sha256"] == result["signature_sha256"]
    assert Path(str(payload["proofs"]["autopilot"])).exists()
    assert Path(str(payload["proofs"]["platform"])).exists()
    assert Path(str(payload["proofs"]["consequence_enforcement"])).exists()
    assert payload["proofs"]["synthetic_dispatch_id"]
