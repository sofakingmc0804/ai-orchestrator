from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.models import BillingClass, Capability, ConsequenceTier, ServiceInfo
from orchestrator.reports.owner_receipt import export_owner_receipt
from orchestrator.state.store import StateStore, iso


@pytest.mark.asyncio
async def test_owner_receipt_exports_cost_quota_and_proof_path(tmp_path: Path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_services(
        [
            ServiceInfo(id="fake-local", name="Fake Local", service_group="test", adapter_name="fake-local", protocol="test"),
            ServiceInfo(id="copilot-gh", name="Copilot", service_group="cloud_clients", adapter_name="copilot-gh", protocol="cli"),
        ]
    )
    await store.upsert_capabilities(
        [
            Capability(
                id="fake-local:local_chat",
                adapter_name="fake-local",
                capability_id="local_chat",
                rating_instruction=3,
                rating_quality=3,
                latency_band="fast",
                consequence_max=ConsequenceTier.LOW,
                billing_class=BillingClass.LOCAL_RESOURCE,
            ),
            Capability(
                id="copilot-gh:code_repair",
                adapter_name="copilot-gh",
                capability_id="code_repair",
                rating_instruction=3,
                rating_quality=3,
                latency_band="medium",
                consequence_max=ConsequenceTier.MEDIUM,
                billing_class=BillingClass.SUBSCRIPTION_QUOTA,
            ),
        ]
    )
    proof_dir = tmp_path / "proof"
    proof_dir.mkdir()
    output_path = proof_dir / "result.txt"
    output_path.write_text("ok", encoding="utf-8")
    (proof_dir / "receipt.json").write_text("{}", encoding="utf-8")
    await store.record_dispatch(
        {
            "id": "dsp_fake",
            "intent_id": "int_fake",
            "adapter_name": "fake-local",
            "envelope": {},
            "state": "completed",
            "started_at": iso(),
            "completed_at": iso(),
            "output_path": str(output_path),
        },
        receipt={
            "service": "fake-local",
            "capability": "local_chat",
            "model": "fake",
            "cost_class": "local_resource",
            "success": True,
            "output_summary": "ok",
        },
    )
    await store.record_quota_snapshots(
        [
            {
                "provider": "github_copilot",
                "units_consumed": 10,
                "units_limit": 100,
                "reset_at": "2026-06-30T00:00:00Z",
            }
        ]
    )

    result = await export_owner_receipt(settings)

    receipt_path = Path(str(result["receipt_path"]))
    text = receipt_path.read_text(encoding="utf-8")
    assert receipt_path.exists()
    assert "`local_resource`" in text
    assert "`subscription_quota`" in text
    assert "90/100 remaining" in text
    assert str(proof_dir / "receipt.json") in text
    assert result["open_repairs"] == 0
