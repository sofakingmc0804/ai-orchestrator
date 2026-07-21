from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.hermes.brain_bridge import complete_workspace_hermes_turn, prepare_workspace_hermes_turn
from orchestrator.state.store import StateStore
from orchestrator.workspace_runtime import build_platform_console_payload


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )


async def seed_routeable_worker(store: StateStore) -> None:
    await store.db.execute(
        "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
        "repo_coding",
        '["coding", "tools"]',
        "{}",
        1,
        "local_resource",
    )
    await store.db.execute(
        """
        INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
          capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        "qwen2.5-coder:7b@ollama-local",
        "qwen2.5-coder:7b",
        "qwen2.5-coder",
        "ollama-local",
        "ollama-local",
        "local_resource",
        '["coding"]',
        '["tools"]',
        '["text"]',
        '{"coding": 8, "speed": 7}',
        '["repo_coding"]',
        "[]",
    )


@pytest.mark.asyncio
async def test_workspace_hermes_turn_freezes_mode_routes_and_records_live_meter(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.bootstrap_workspace_roots()
    await store.seed_default_mode_packs()
    await seed_routeable_worker(store)

    prepared = await prepare_workspace_hermes_turn(
        settings,
        workspace_id="personal",
        mode_pack_id="software_engineering.v1",
        skill_capability_checks={"repo_read": True, "test_runner": True},
        text="repair the local parser",
        job_class="repo_coding",
        argv=["run", "repair the local parser"],
        consumer="Matt",
    )

    assert prepared["workspace_id"] == "personal"
    assert prepared["workspace_session"]["frozen_mode_pack"]["id"] == "software_engineering.v1"
    assert prepared["work_packet"]["workspace_id"] == "personal"
    assert prepared["route"]["decision"]["chosen_adapter"] == "ollama-http"

    completed = await complete_workspace_hermes_turn(
        settings,
        workspace_id="personal",
        work_packet_id=prepared["work_packet"]["id"],
        provider="ollama",
        model="qwen2.5-coder:7b",
        route="ollama-http",
        tokens_in=41,
        tokens_out=9,
        estimated_cost_usd=0.0,
        actual_cost_usd=0.0,
        quota_source="local_model",
        context_pressure=0.2,
    )

    assert completed["resource_event"]["tokens_total"] == 50
    console = await build_platform_console_payload(StateStore(settings))
    assert console["metering"][0]["tokens_total"] == 50
    assert console["metering"][0]["quota_sources"] == ["local_model"]
