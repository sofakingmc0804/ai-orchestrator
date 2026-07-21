from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import Settings
from orchestrator.state.store import StateStore
from orchestrator.workspace_runtime import WorkspacePolicyError, build_platform_console_payload
from orchestrator.evaluation.sparring import FROZEN_HELD_OUT_SUITE, evaluate_held_out_suite
import orchestrator.ui.server as fastapi_server


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )


def test_personal_to_example_transfer_never_copies_before_owner_approval(tmp_path: Path) -> None:
    async def run() -> None:
        store = StateStore(settings_for(tmp_path))
        await store.initialize()
        roots = await store.bootstrap_workspace_roots()
        assert {row["id"] for row in roots} >= {"personal", "example", "system", "unclassified_legacy"}

        packet = await store.create_work_packet(
            workspace_id="personal",
            intent="Prepare a private estimate note",
            payload={"body": "PERSONAL-TRANSFER-SECRET", "classification": "example_likely"},
            mode_pack_id="project_management.v1",
            consumer="Matt",
        )
        transfer = await store.propose_transfer(
            work_packet_id=packet["id"],
            target_workspace_id="example",
            summary="Potential Example estimate work",
        )

        assert transfer["state"] == "proposed"
        assert await store.list_work_packets("example") == []

        rejected = await store.decide_transfer(transfer["id"], approve=False, decided_by="owner")
        assert rejected["state"] == "rejected"
        assert await store.list_work_packets("example") == []

        second = await store.propose_transfer(
            work_packet_id=packet["id"],
            target_workspace_id="example",
            summary="Potential Example estimate work",
        )
        approved = await store.decide_transfer(second["id"], approve=True, decided_by="owner")
        assert approved["state"] == "approved"
        assert approved["target_work_packet_id"]
        example_packets = await store.list_work_packets("example")
        assert len(example_packets) == 1
        assert example_packets[0]["payload"]["body"] == "PERSONAL-TRANSFER-SECRET"
        assert example_packets[0]["source_workspace_id"] == "personal"

    asyncio.run(run())


def test_mode_session_freezes_its_pack_and_rejects_unavailable_skill(tmp_path: Path) -> None:
    async def run() -> None:
        store = StateStore(settings_for(tmp_path))
        await store.initialize()
        await store.bootstrap_workspace_roots()
        packs = await store.seed_default_mode_packs()
        assert {pack["id"] for pack in packs} == {
            "software_engineering.v1",
            "technical_writing.v1",
            "research.v1",
            "industrial_controls.v1",
            "project_management.v1",
        }

        session = await store.start_workspace_session(
            workspace_id="personal",
            mode_pack_id="software_engineering.v1",
            skill_capability_checks={"repo_read": True, "test_runner": True},
        )
        assert session["workspace_id"] == "personal"
        assert session["frozen_mode_pack"]["id"] == "software_engineering.v1"

        with pytest.raises(WorkspacePolicyError, match="capability check failed"):
            await store.start_workspace_session(
                workspace_id="example",
                mode_pack_id="software_engineering.v1",
                skill_capability_checks={"repo_read": True, "test_runner": False},
            )

        # Every declared foundation mode can start only when the desktop's
        # callable bridge capabilities have passed their check.
        checks = {"repo_read": True, "test_runner": True, "source_reader": True}
        for pack in packs:
            started = await store.start_workspace_session(
                workspace_id="personal",
                mode_pack_id=pack["id"],
                skill_capability_checks=checks,
            )
            assert started["frozen_mode_pack"]["preloaded_skills"]

    asyncio.run(run())


def test_system_console_is_aggregate_only_and_outcome_replay_is_required(tmp_path: Path) -> None:
    async def run() -> None:
        store = StateStore(settings_for(tmp_path))
        await store.initialize()
        await store.bootstrap_workspace_roots()
        await store.seed_default_mode_packs()
        packet = await store.create_work_packet(
            workspace_id="personal",
            intent="PERSONAL-CONSOLE-SECRET",
            payload={"body": "PERSONAL-CONSOLE-SECRET"},
            mode_pack_id="technical_writing.v1",
            consumer="Matt",
        )
        await store.record_resource_event(
            workspace_id="personal",
            work_packet_id=packet["id"],
            provider="ollama",
            model="qwen2.5-coder:7b",
            route="local",
            tokens_in=12,
            tokens_out=8,
            estimated_cost_usd=0.0,
            actual_cost_usd=0.0,
            quota_source="local_model",
            context_pressure=0.1,
        )
        episode = await store.record_evaluation_episode(
            workspace_id="personal",
            work_packet_id=packet["id"],
            mode_pack_id="technical_writing.v1",
            selected_model="qwen2.5-coder:7b",
            tools=["repo_read"],
            evidence={"review": "accepted"},
            reviewer_outcome="accepted",
            delivery_receipt="rcpt_local",
            cost_usd=0.0,
            failure_class=None,
            score=0.91,
            held_out=True,
        )
        candidate = await store.create_policy_candidate(
            workspace_id="personal",
            policy={"routing": "prefer_local_for_writing"},
            baseline_score=0.72,
            episode_ids=[episode["id"]],
            sandbox_only=True,
        )
        sparring = evaluate_held_out_suite(
            baseline_scores={case.id: 0.72 for case in FROZEN_HELD_OUT_SUITE},
            candidate_scores={case.id: 0.91 for case in FROZEN_HELD_OUT_SUITE},
        )
        replay = await store.replay_policy_candidate(
            candidate["id"],
            candidate_score=0.91,
            held_out_episode_ids=[episode["id"]],
            replay_evidence=sparring,
        )
        assert replay["state"] == "promoted_sandbox"
        assert replay["replay"]["decision"] == "promote"
        assert replay["replay"]["suite_id"] == "foundation-held-out-v1"

        console = await build_platform_console_payload(store)
        assert console["scope"] == "system"
        serialized = str(console)
        assert "PERSONAL-CONSOLE-SECRET" not in serialized
        assert console["metering"][0]["provider"] == "ollama"
        assert console["model_cards"][0]["specialization_evidence"]["episode_count"] == 1

    asyncio.run(run())


def test_three_role_swarm_requires_budget_reviewer_and_named_consumer(tmp_path: Path) -> None:
    async def run() -> None:
        store = StateStore(settings_for(tmp_path))
        await store.initialize()
        await store.bootstrap_workspace_roots()
        await store.seed_default_mode_packs()
        packet = await store.create_work_packet(
            workspace_id="personal",
            intent="Build local proof bundle",
            payload={"body": "safe local task"},
            mode_pack_id="software_engineering.v1",
            consumer="Matt",
        )
        with pytest.raises(WorkspacePolicyError, match="three roles"):
            await store.start_swarm_run(
                workspace_id="personal",
                work_packet_id=packet["id"],
                roles=[{"name": "builder"}, {"name": "reviewer"}],
                budget={"max_tokens": 1000},
                reviewer="reviewer",
                output_consumer="Matt",
                stop_rule="budget_exhausted",
            )

        swarm = await store.start_swarm_run(
            workspace_id="personal",
            work_packet_id=packet["id"],
            roles=[{"name": "planner"}, {"name": "builder"}, {"name": "reviewer"}],
            budget={"max_tokens": 1000},
            reviewer="reviewer",
            output_consumer="Matt",
            stop_rule="budget_exhausted",
        )
        assert swarm["state"] == "running"
        assert swarm["side_effect_policy"] == "approval_required"
        completed = await store.complete_swarm_run(
            swarm["id"],
            reviewer_outcome="accepted",
            receipt={"consumer": "Matt", "result": "local proof bundle"},
        )
        assert completed["state"] == "completed"
        assert completed["reviewer_outcome"] == "accepted"

    asyncio.run(run())


def test_platform_console_api_and_transfer_card_keep_personal_content_out_of_system_view(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_services() -> tuple[list[object], list[object]]:
        return [], []

    async def fake_supervisor_tick(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced"}

    monkeypatch.setattr(fastapi_server, "discover_services_and_capabilities", fake_services)
    monkeypatch.setattr(fastapi_server, "discover_projects", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fastapi_server, "run_supervisor_tick", fake_supervisor_tick)
    monkeypatch.setattr(fastapi_server, "start_supervisor_thread", lambda _settings: object())
    app = fastapi_server.create_app(settings_for(tmp_path))

    with TestClient(app) as client:
        roots = client.post("/api/workspaces/bootstrap")
        assert roots.status_code == 200
        packet = client.post(
            "/api/work-packets",
            json={
                "workspace_id": "personal",
                "intent": "PERSONAL-API-SECRET",
                "payload": {"body": "PERSONAL-API-SECRET"},
                "mode_pack_id": "project_management.v1",
                "consumer": "Matt",
            },
        )
        assert packet.status_code == 200
        transfer = client.post(
            "/api/transfer-proposals",
            json={
                "work_packet_id": packet.json()["id"],
                "target_workspace_id": "example",
                "summary": "Likely Example work",
            },
        )
        assert transfer.status_code == 200
        assert transfer.json()["state"] == "proposed"

        # The proposal belongs to its Personal source.  Selecting Example must not
        # expose even the proposal summary before the owner authorizes a copy.
        example_context = client.get("/api/hermes/workspace-context?workspace_id=example")
        assert example_context.status_code == 200
        assert "Likely Example work" not in example_context.text
        assert example_context.json()["connector_access"]["connectors"] == []
        assert example_context.json()["connector_access"]["state"] == "owner_assignment_required"

        decision = client.post(
            f"/api/transfer-proposals/{transfer.json()['id']}/decision",
            json={"approve": True, "decided_by": "owner"},
        )
        assert decision.status_code == 200
        assert decision.json()["state"] == "approved"

        console = client.get("/api/platform-console")
        root = client.get("/")
        missing_scope = client.post("/api/route", json={"text": "legacy route", "job_class": "repo_coding"})
        system_scope = client.post(
            "/api/route",
            json={"workspace_id": "system", "text": "system must not receive work", "job_class": "repo_coding"},
        )
        missing_memory_scope = client.get("/api/working-memory")
        personal_memory = client.get("/api/working-memory?workspace_id=personal")

    assert console.status_code == 200
    assert "PERSONAL-API-SECRET" not in console.text
    assert console.json()["scope"] == "system"
    assert root.status_code == 200
    assert "Platform Console" in root.text
    assert "/api/platform-console" in root.text
    assert 'href="hermes://workbench/"' in root.text
    assert "Return to Hermes" in root.text
    assert missing_scope.status_code == 422
    assert system_scope.status_code == 409
    assert missing_memory_scope.status_code == 422
    assert personal_memory.status_code == 200
    assert personal_memory.json() == []


def test_workspace_meter_is_scoped_and_model_cards_keep_static_and_dynamic_evidence(tmp_path: Path) -> None:
    async def run() -> None:
        store = StateStore(settings_for(tmp_path))
        await store.initialize()
        await store.bootstrap_workspace_roots()
        await store.seed_default_mode_packs()
        await store.db.execute(
            """
            INSERT INTO worker_cards(worker_id, model_id, provider_id, capabilities_json, context_window, stats_json)
            VALUES(?,?,?,?,?,?)
            """,
            "qwen@ollama",
            "qwen2.5-coder:7b",
            "ollama",
            '["coding", "tools"]',
            32768,
            '{"coding": 8}',
        )
        packet = await store.create_work_packet(
            workspace_id="personal",
            intent="private",
            payload={"body": "METER-PRIVATE-SECRET"},
            mode_pack_id="software_engineering.v1",
            consumer="Matt",
        )
        await store.record_resource_event(
            workspace_id="personal",
            work_packet_id=packet["id"],
            provider="ollama",
            model="qwen2.5-coder:7b",
            route="ollama-http",
            tokens_in=4,
            tokens_out=6,
            estimated_cost_usd=0.0,
            actual_cost_usd=0.0,
            quota_source="local_model",
            context_pressure=0.2,
        )
        meter = await store.workspace_meter("personal")
        assert meter["tokens_total"] == 10
        assert meter["quota_sources"] == ["local_model"]
        assert "METER-PRIVATE-SECRET" not in str(meter)

        console = await build_platform_console_payload(store)
        card = console["model_cards"][0]
        assert card["static_capabilities"]["context_window"] == 32768
        assert card["static_capabilities"]["capabilities"] == ["coding", "tools"]
        assert card["specialization_claim"]["supported"] is False

    asyncio.run(run())


def test_resource_event_correction_replaces_misattributed_provider_metadata(tmp_path: Path) -> None:
    async def run() -> None:
        store = StateStore(settings_for(tmp_path))
        await store.initialize()
        await store.bootstrap_workspace_roots()
        await store.seed_default_mode_packs()
        packet = await store.create_work_packet(
            workspace_id="personal",
            intent="meter correction",
            payload={},
            mode_pack_id="software_engineering.v1",
            consumer="Matt",
        )
        event = await store.record_resource_event(
            workspace_id="personal",
            work_packet_id=packet["id"],
            provider="incorrect-local-provider",
            model="incorrect-local-model",
            route="hermes-cli",
            tokens_in=11,
            tokens_out=2,
            estimated_cost_usd=0.0,
            actual_cost_usd=None,
            quota_source="unknown",
            context_pressure=None,
        )
        corrected = await store.correct_resource_event(
            event["id"],
            provider="copilot",
            model="claude-sonnet-4.6",
            quota_source="subscription_usage_report",
            measurement_source="hermes_usage_report",
            estimated_cost_usd=None,
            actual_cost_usd=None,
            quota_state={"cost_status": "unknown"},
        )
        assert corrected["provider"] == "copilot"
        assert corrected["model"] == "claude-sonnet-4.6"
        assert corrected["measurement_source"] == "hermes_usage_report"
        meter = await store.workspace_meter("personal")
        assert meter["provider_models"][0]["provider"] == "copilot"

    asyncio.run(run())


def test_nonruntime_evaluation_fixtures_are_superseded_before_model_cards_read_them(tmp_path: Path) -> None:
    async def run() -> None:
        store = StateStore(settings_for(tmp_path))
        await store.initialize()
        await store.bootstrap_workspace_roots()
        await store.seed_default_mode_packs()
        packet = await store.create_work_packet(
            workspace_id="personal",
            intent="fixture retirement",
            payload={},
            mode_pack_id="software_engineering.v1",
            consumer="Matt",
        )
        episode = await store.record_evaluation_episode(
            workspace_id="personal",
            work_packet_id=packet["id"],
            mode_pack_id="software_engineering.v1",
            selected_model="qwen",
            tools=["repo_read"],
            evidence={"run_id": "proof-run", "review": "held-out local replay"},
            reviewer_outcome="accepted",
            delivery_receipt="fixture",
            cost_usd=0.0,
            failure_class=None,
            score=0.82,
            held_out=True,
        )
        retired = await store.supersede_nonruntime_evaluation_fixtures("proof-run")
        assert retired == {"episodes": 1, "policy_candidates": 0}
        assert await store.list_evaluation_episodes() == []
        visible = await store.list_evaluation_episodes(include_superseded=True)
        assert visible[0]["id"] == episode["id"]
        assert visible[0]["reviewer_outcome"] == "superseded"

    asyncio.run(run())


def test_hermes_workspace_turn_routes_are_explicit_and_metered_over_local_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_services() -> tuple[list[object], list[object]]:
        return [], []

    async def fake_supervisor_tick(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced"}

    calls: list[dict[str, object]] = []

    async def fake_prepare(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append({"kind": "prepare", **kwargs})
        return {"state": "produced", "workspace_id": kwargs["workspace_id"], "work_packet": {"id": "wp_test"}}

    async def fake_complete(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append({"kind": "complete", **kwargs})
        return {"state": "metered", "workspace_id": kwargs["workspace_id"], "resource_event": {"tokens_total": 12}}

    monkeypatch.setattr(fastapi_server, "discover_services_and_capabilities", fake_services)
    monkeypatch.setattr(fastapi_server, "discover_projects", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fastapi_server, "run_supervisor_tick", fake_supervisor_tick)
    monkeypatch.setattr(fastapi_server, "start_supervisor_thread", lambda _settings: object())
    monkeypatch.setattr(fastapi_server, "prepare_workspace_hermes_turn", fake_prepare)
    monkeypatch.setattr(fastapi_server, "complete_workspace_hermes_turn", fake_complete)
    app = fastapi_server.create_app(settings_for(tmp_path))

    with TestClient(app) as client:
        prepared = client.post(
            "/api/hermes/workspace-turns/prepare",
            json={
                "workspace_id": "personal",
                "mode_pack_id": "software_engineering.v1",
                "skill_capability_checks": {"repo_read": True, "test_runner": True},
                "text": "repair parser",
                "job_class": "repo_coding",
                "consumer": "Matt",
            },
        )
        metered = client.post(
            "/api/hermes/workspace-turns/wp_test/complete",
            json={
                "workspace_id": "personal",
                "provider": "ollama",
                "model": "qwen2.5-coder:7b",
                "route": "ollama-http",
                "tokens_in": 9,
                "tokens_out": 3,
                "quota_source": "local_model",
            },
        )

    assert prepared.status_code == 200
    assert metered.status_code == 200
    assert calls[0]["workspace_id"] == "personal"
    assert calls[1]["work_packet_id"] == "wp_test"
    assert metered.json()["resource_event"]["tokens_total"] == 12
