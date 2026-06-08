from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore


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
    assert any("12/12" in evidence for evidence in adapter_target["evidence"])
    assert any("dispatch_proven=0/12" in evidence for evidence in adapter_target["evidence"])


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
