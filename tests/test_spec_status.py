from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator.spec_status as spec_status_module
from orchestrator.config import Settings
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.models import BillingClass, Capability, ConsequenceTier, HealthState, ServiceInfo
from orchestrator.reports.acceptance_battery import prove_autopilot_default_disabled, prove_platform_bones
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore, iso


async def _seed_proven_adapters(
    store: StateStore,
    adapter_names: list[str],
    health_overrides: dict[str, HealthState] | None = None,
) -> None:
    health_overrides = health_overrides or {}
    services: list[ServiceInfo] = []
    capabilities: list[Capability] = []
    for adapter_name in adapter_names:
        services.append(
            ServiceInfo(
                id=adapter_name,
                name=adapter_name,
                service_group="test",
                adapter_name=adapter_name,
                protocol="test",
                health_state=health_overrides.get(adapter_name, HealthState.HEALTHY),
            )
        )
        capabilities.append(
            Capability(
                id=f"{adapter_name}:local_chat",
                adapter_name=adapter_name,
                capability_id="local_chat",
                rating_instruction=3,
                rating_quality=3,
                latency_band="fast",
                consequence_max=ConsequenceTier.MEDIUM,
                billing_class=BillingClass.LOCAL_RESOURCE,
            )
        )
    await store.upsert_services(services)
    await store.upsert_capabilities(capabilities)
    for index, adapter_name in enumerate(adapter_names, start=1):
        proof_dir = store.settings.home / "proofs" / adapter_name
        proof_dir.mkdir(parents=True, exist_ok=True)
        output_path = proof_dir / "result.txt"
        output_path.write_text("ok", encoding="utf-8")
        await store.record_dispatch(
            {
                "id": f"dsp_{index}",
                "intent_id": f"int_{index}",
                "adapter_name": adapter_name,
                "envelope": {},
                "state": "completed",
                "started_at": iso(),
                "completed_at": iso(),
                "output_path": str(output_path),
            },
            receipt={
                "service": adapter_name,
                "capability": "local_chat",
                "model": adapter_name,
                "cost_class": "local_resource",
                "success": True,
                "output_summary": "ok",
                # Integrity: only live-proven dispatches count toward CT-07/09/12.
                "raw_output": '{"returncode": 0, "text": "ok"}',
                "proof_kind": "live",
            },
        )


@pytest.mark.asyncio
async def test_spec_status_reports_all_capability_targets(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=Path.cwd())
    store = StateStore(settings)
    await store.initialize()
    services, capabilities = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(capabilities)

    status = await evaluate_spec_status(settings, store)

    targets = status["targets"]
    assert len(targets) == 24
    assert status["spec_version"] == "4.0"
    assert {row["id"] for row in targets} == {f"CT-{number:02d}" for number in range(1, 25)}
    assert status["counts"]["passed"] >= 2
    assert all(row["next_action"] for row in targets)


@pytest.mark.asyncio
async def test_spec_status_does_not_count_registration_as_working_adapter(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=Path.cwd())
    store = StateStore(settings)
    await store.initialize()
    services, capabilities = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(capabilities)

    status = await evaluate_spec_status(settings, store)
    adapter_target = next(row for row in status["targets"] if row["id"] == "CT-12")

    assert adapter_target["status"] == "partial"
    assert any("11/11" in evidence for evidence in adapter_target["evidence"])
    assert any("dispatch_proven=0/11" in evidence for evidence in adapter_target["evidence"])


@pytest.mark.asyncio
async def test_spec_status_marks_latency_target_passed_from_receipt(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path / ".orchestrator", state_path=tmp_path / ".orchestrator" / "state.sqlite", notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl", log_dir=tmp_path / ".orchestrator" / "logs", repo_root=Path.cwd())
    store = StateStore(settings)
    await store.initialize()
    benchmark_root = settings.home / "benchmarks"
    benchmark_root.mkdir(parents=True)
    (benchmark_root / "selection-latency-1.json").write_text(
        json.dumps(
            {
                "benchmark": "selection_to_output_latency",
                "elapsed_seconds": 1.5,
                "passed": True,
            }
        ),
        encoding="utf-8",
    )

    status = await evaluate_spec_status(settings, store)
    latency_target = next(row for row in status["targets"] if row["id"] == "CT-22")

    assert latency_target["status"] == "passed"
    assert any("latest_elapsed=1.5" in evidence for evidence in latency_target["evidence"])


@pytest.mark.asyncio
async def test_spec_status_crash_survival_requires_live_supervisor_restart_receipt(tmp_path: Path) -> None:
    settings = Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=Path.cwd(),
    )
    store = StateStore(settings)
    await store.initialize()
    await store.add_repair_item(
        "ollama-http",
        "Dispatch dsp_old was running when the Orchestrator restarted.",
        "Review dispatch.",
    )

    stale_status = await evaluate_spec_status(settings, store)
    stale_target = next(row for row in stale_status["targets"] if row["id"] == "CT-23")
    assert stale_target["status"] == "partial"

    supervisor_dir = settings.home / "supervisor"
    supervisor_dir.mkdir(parents=True)
    receipt = supervisor_dir / "restart.json"
    receipt.write_text(
        json.dumps(
            {
                "event": "supervisor_tick",
                "state": "produced",
                "proof_kind": "live",
                "completed_at": "2026-06-14T00:00:00+00:00",
                "recovered_dispatches_count": 1,
                "restart_recovery_proved": True,
            }
        ),
        encoding="utf-8",
    )

    proved_status = await evaluate_spec_status(settings, store)
    proved_target = next(row for row in proved_status["targets"] if row["id"] == "CT-23")
    assert proved_target["status"] == "passed"
    assert any("restart.json" in evidence for evidence in proved_target["evidence"])


@pytest.mark.asyncio
async def test_spec_status_autopilot_and_platform_require_live_receipts(tmp_path: Path) -> None:
    settings = Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=Path.cwd(),
    )
    store = StateStore(settings)
    await store.initialize()

    unproved = await evaluate_spec_status(settings, store)
    unproved_autopilot = next(row for row in unproved["targets"] if row["id"] == "CT-19")
    unproved_platform = next(row for row in unproved["targets"] if row["id"] == "CT-20")
    assert unproved_autopilot["status"] == "partial"
    assert unproved_platform["status"] == "partial"

    autopilot_receipt = await prove_autopilot_default_disabled(settings, store)
    platform_receipt = await prove_platform_bones(settings)

    proved = await evaluate_spec_status(settings, store)
    proved_autopilot = next(row for row in proved["targets"] if row["id"] == "CT-19")
    proved_platform = next(row for row in proved["targets"] if row["id"] == "CT-20")
    assert proved_autopilot["status"] == "passed"
    assert proved_platform["status"] == "passed"
    assert any(str(autopilot_receipt["receipt_path"]) in evidence for evidence in proved_autopilot["evidence"])
    assert any(str(platform_receipt["receipt_path"]) in evidence for evidence in proved_platform["evidence"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("drifted_adapter", "health_state"),
    [
        ("lm-studio", HealthState.STOPPED),
    ],
)
async def test_spec_status_marks_core_adapter_health_drift_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    drifted_adapter: str,
    health_state: HealthState,
) -> None:
    monkeypatch.setattr(spec_status_module, "NAMED_ADAPTERS", {"hermes-agent", "lm-studio"})
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=Path.cwd(),
    )
    store = StateStore(settings)
    await store.initialize()
    await _seed_proven_adapters(store, ["hermes-agent", "lm-studio"])

    healthy_status = await evaluate_spec_status(settings, store)
    healthy_target = next(row for row in healthy_status["targets"] if row["id"] == "CT-12")
    assert healthy_target["status"] == "passed"

    await store.upsert_services(
        [
            ServiceInfo(
                id="hermes-agent",
                name="hermes-agent",
                service_group="test",
                adapter_name="hermes-agent",
                protocol="test",
                health_state=health_state if drifted_adapter == "hermes-agent" else HealthState.HEALTHY,
            ),
            ServiceInfo(
                id="lm-studio",
                name="lm-studio",
                service_group="test",
                adapter_name="lm-studio",
                protocol="test",
                health_state=health_state if drifted_adapter == "lm-studio" else HealthState.HEALTHY,
            ),
        ]
    )

    drifted_status = await evaluate_spec_status(settings, store)
    drifted_target = next(row for row in drifted_status["targets"] if row["id"] == "CT-12")

    assert drifted_target["status"] == "partial"
    assert any(f"health_drift={drifted_adapter}:{health_state.value}" in evidence for evidence in drifted_target["evidence"])
    assert "repair-services" in drifted_target["next_action"]


@pytest.mark.asyncio
async def test_spec_status_does_not_require_retired_hermes_downstream_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(spec_status_module, "NAMED_ADAPTERS", {"hermes-agent", "lm-studio"})
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=Path.cwd(),
    )
    store = StateStore(settings)
    await store.initialize()
    await _seed_proven_adapters(store, ["lm-studio"])
    await store.upsert_services(
        [
            ServiceInfo(
                id="hermes-agent",
                name="hermes-agent",
                service_group="test",
                adapter_name="hermes-agent",
                protocol="test",
                health_state=HealthState.DEGRADED,
            )
        ]
    )

    status = await evaluate_spec_status(settings, store)
    adapter_target = next(row for row in status["targets"] if row["id"] == "CT-12")

    assert adapter_target["status"] == "passed"
    assert any("dispatch_proven=1/1" in evidence for evidence in adapter_target["evidence"])
    assert any("retired_downstream=hermes-agent" in evidence for evidence in adapter_target["evidence"])
