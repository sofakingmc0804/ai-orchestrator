from __future__ import annotations

import pytest

from orchestrator.swarm.coordinator import run_low_risk_local_swarm
from orchestrator.config import Settings
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_governed_local_swarm_runs_three_roles_with_review_and_consumer_receipt(tmp_path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    store = StateStore(settings)
    await store.initialize()
    await store.bootstrap_workspace_roots()
    await store.seed_default_mode_packs()
    packet = await store.create_work_packet(
        workspace_id="personal",
        intent="Create a local handoff note",
        payload={"summary": "safe local packet"},
        mode_pack_id="project_management.v1",
        consumer="Matt",
    )

    result = await run_low_risk_local_swarm(store, workspace_id="personal", work_packet_id=packet["id"], consumer="Matt")

    assert result["swarm"]["state"] == "completed"
    assert [row["role"] for row in result["role_results"]] == ["planner", "builder", "reviewer"]
    assert result["reviewer_result"]["outcome"] == "accepted"
    assert result["consumer_output"]["consumer"] == "Matt"
    assert result["consumer_output"]["external_side_effects"] is False
