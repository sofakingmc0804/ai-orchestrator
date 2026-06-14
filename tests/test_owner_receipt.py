from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator.spec_status as spec_status_module
from orchestrator.config import Settings
from orchestrator.models import BillingClass, Capability, ConsequenceTier, HealthState, ServiceInfo
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


@pytest.mark.asyncio
async def test_owner_receipt_spec_counts_drop_when_core_adapter_health_drifts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(spec_status_module, "NAMED_ADAPTERS", {"lm-studio"})
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
            ServiceInfo(
                id="lm-studio",
                name="LM Studio",
                service_group="agent_hosts",
                adapter_name="lm-studio",
                protocol="http",
                health_state=HealthState.HEALTHY,
            )
        ]
    )
    await store.upsert_capabilities(
        [
            Capability(
                id="lm-studio:embeddings",
                adapter_name="lm-studio",
                capability_id="embeddings",
                rating_instruction=3,
                rating_quality=3,
                latency_band="fast",
                consequence_max=ConsequenceTier.MEDIUM,
                billing_class=BillingClass.LOCAL_RESOURCE,
            )
        ]
    )
    proof_dir = tmp_path / "proof"
    proof_dir.mkdir()
    output_path = proof_dir / "result.txt"
    output_path.write_text("ok", encoding="utf-8")
    await store.record_dispatch(
        {
            "id": "dsp_lm_studio",
            "intent_id": "int_lm_studio",
            "adapter_name": "lm-studio",
            "envelope": {},
            "state": "completed",
            "started_at": iso(),
            "completed_at": iso(),
            "output_path": str(output_path),
        },
        receipt={
            "service": "lm-studio",
            "capability": "embeddings",
            "model": "text-embedding-nomic-embed-text-v1.5",
            "cost_class": "local_resource",
            "success": True,
            "output_summary": "ok",
            # Integrity: CT-12 counts a dispatch as proven only with live proof.
            "raw_output": '{"returncode": 0, "text": "ok"}',
            "proof_kind": "live",
        },
    )

    healthy_result = await export_owner_receipt(settings)

    await store.upsert_services(
        [
            ServiceInfo(
                id="lm-studio",
                name="LM Studio",
                service_group="agent_hosts",
                adapter_name="lm-studio",
                protocol="http",
                health_state=HealthState.DEGRADED,
            )
        ]
    )

    drifted_result = await export_owner_receipt(settings)
    text = Path(str(drifted_result["receipt_path"])).read_text(encoding="utf-8")

    assert drifted_result["spec_counts"]["partial"] == healthy_result["spec_counts"]["partial"] + 1
    assert drifted_result["spec_counts"]["passed"] == healthy_result["spec_counts"]["passed"] - 1
    assert f"`{drifted_result['spec_counts']['passed']} passed`" in text
    assert f"`{drifted_result['spec_counts']['partial']} partial`" in text


@pytest.mark.asyncio
async def test_owner_receipt_migrates_legacy_adapter_proof_paths_to_repo_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy_home = tmp_path / "legacy-home" / ".orchestrator"
    repo_home = tmp_path / "repo" / ".runtime" / "orchestrator"
    monkeypatch.setenv("ORCHESTRATOR_LEGACY_HOME", str(legacy_home))
    settings = Settings(
        home=repo_home,
        state_path=repo_home / "state.sqlite",
        notifications_path=repo_home / "notifications.jsonl",
        log_dir=repo_home / "logs",
        repo_root=tmp_path / "repo",
    )
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_services(
        [
            ServiceInfo(
                id="fake-local",
                name="Fake Local",
                service_group="test",
                adapter_name="fake-local",
                protocol="test",
                health_state=HealthState.HEALTHY,
            )
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
            )
        ]
    )
    relative_proof = Path("adapter-proof") / "fake-local" / "ORCHESTRATOR_OUTPUT" / "2026-06-05" / "int_legacy"
    legacy_output = legacy_home / relative_proof / "result.txt"
    legacy_receipt = legacy_home / relative_proof / "receipt.json"
    repo_output = repo_home / relative_proof / "result.txt"
    repo_receipt = repo_home / relative_proof / "receipt.json"
    legacy_output.parent.mkdir(parents=True)
    repo_output.parent.mkdir(parents=True)
    legacy_output.write_text("old proof", encoding="utf-8")
    legacy_receipt.write_text("{}", encoding="utf-8")
    repo_output.write_text("repo proof", encoding="utf-8")
    repo_receipt.write_text("{}", encoding="utf-8")
    await store.record_dispatch(
        {
            "id": "dsp_legacy",
            "intent_id": "int_legacy",
            "adapter_name": "fake-local",
            "envelope": {},
            "state": "completed",
            "started_at": iso(),
            "completed_at": iso(),
            "output_path": str(legacy_output),
        },
        receipt={
            "service": "fake-local",
            "capability": "local_chat",
            "model": "fake",
            "cost_class": "local_resource",
            "success": True,
            "output_summary": "ok",
            "output_path": str(legacy_output),
            "receipt_path": str(legacy_receipt),
        },
    )

    result = await export_owner_receipt(settings)

    text = Path(str(result["receipt_path"])).read_text(encoding="utf-8")
    dispatch = (await store.list_dispatches(limit=1))[0]
    receipt_row = await store.db.fetchrow("SELECT full_receipt FROM receipts WHERE dispatch_id = ?", "dsp_legacy")
    full_receipt = json.loads(str((receipt_row or {}).get("full_receipt") or "{}"))

    assert str(repo_receipt) in text
    assert str(legacy_home) not in text
    assert dispatch["output_path"] == str(repo_output)
    assert full_receipt["output_path"] == str(repo_output)
    assert full_receipt["receipt_path"] == str(repo_receipt)
