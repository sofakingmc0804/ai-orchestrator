from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.discovery.projects import discover_projects
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.models import ConsequenceTier, Intent, Notification
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_project_discovery_persists_working_memory(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    settings = Settings(home=tmp_path / "home", state_path=tmp_path / "home" / "state.sqlite", notifications_path=tmp_path / "home" / "notifications.jsonl", log_dir=tmp_path / "home" / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    projects = discover_projects([tmp_path], max_depth=2)
    await store.upsert_projects(projects)
    rows = await store.list_projects()
    assert len(rows) == 1
    assert rows[0]["name"] == "sample"


@pytest.mark.asyncio
async def test_activity_stream_includes_routing_dispatch_and_notification(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    services, capabilities = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(capabilities)
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    await dispatcher.dispatch_text("send this externally")
    activity = await store.list_activity()
    kinds = {row["kind"] for row in activity}
    assert "notification" in kinds


@pytest.mark.asyncio
async def test_working_memory_tracks_intents_outputs_and_approvals(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    settings = Settings(home=tmp_path / "home", state_path=tmp_path / "home" / "state.sqlite", notifications_path=tmp_path / "home" / "notifications.jsonl", log_dir=tmp_path / "home" / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    project_id = "proj_sample"
    await store.upsert_projects(
        [
            {
                "id": project_id,
                "name": "sample",
                "root_path": str(project),
                "domain": "coding",
                "consequence_tier": "low",
            }
        ]
    )
    intent = Intent(
        id="int_project",
        source="test",
        raw_text="summarize this",
        parsed_payload={"required_capability": "summarize_text"},
        project_id=project_id,
        consequence_tier=ConsequenceTier.LOW,
    )

    await store.create_intent(intent)
    memory = await store.list_working_memory()
    assert memory[0]["project_id"] == project_id
    assert memory[0]["in_flight_intents"][0]["id"] == "int_project"

    await store.add_notification(
        Notification(
            id="ntf_project",
            severity="approval_request",
            project_id=project_id,
            intent_id="int_project",
            title="Approval",
            body="approve",
        )
    )
    memory = await store.list_working_memory()
    assert memory[0]["pending_approvals"][0]["id"] == "ntf_project"

    await store.record_dispatch(
        {
            "id": "dsp_project",
            "intent_id": "int_project",
            "adapter_name": "synthetic-test",
            "envelope": {},
            "state": "completed",
            "completed_at": "2026-06-05T00:00:00+00:00",
            "output_path": str(project / "out.txt"),
        }
    )
    await store.update_intent_state("int_project", "completed", completed=True)
    await store.acknowledge_approval("int_project")
    memory = await store.list_working_memory()
    assert memory[0]["in_flight_intents"] == []
    assert memory[0]["recent_outputs"][0]["dispatch_id"] == "dsp_project"
    assert memory[0]["pending_approvals"] == []


@pytest.mark.asyncio
async def test_project_path_resolution_uses_longest_matching_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    settings = Settings(home=tmp_path / "home", state_path=tmp_path / "home" / "state.sqlite", notifications_path=tmp_path / "home" / "notifications.jsonl", log_dir=tmp_path / "home" / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_projects(
        [
            {"id": "proj_root", "name": "root", "root_path": str(root), "domain": "general", "consequence_tier": "low"},
            {"id": "proj_child", "name": "child", "root_path": str(child), "domain": "general", "consequence_tier": "low"},
        ]
    )

    project_id = await store.find_project_id_for_path(child / "file.txt")

    assert project_id == "proj_child"


@pytest.mark.asyncio
async def test_project_policy_can_require_medium_approval(tmp_path: Path) -> None:
    project = tmp_path / "policy-project"
    project.mkdir()
    policy = project / ".orchestrator-policy.yaml"
    policy.write_text("approval:\n  required_at: medium\nrouting:\n  allowed_adapters:\n    - ollama-http\n", encoding="utf-8")
    settings = Settings(home=tmp_path / "home", state_path=tmp_path / "home" / "state.sqlite", notifications_path=tmp_path / "home" / "notifications.jsonl", log_dir=tmp_path / "home" / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_projects(
        [
            {
                "id": "proj_policy",
                "name": "policy-project",
                "root_path": str(project),
                "domain": "coding",
                "consequence_tier": "medium",
                "policy_file_path": str(policy),
            }
        ]
    )
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))

    result = await dispatcher.dispatch_text("build a local note", project_root=project)

    assert result.state == "awaiting_approval"
    approvals = await store.list_pending_approvals()
    assert approvals[0]["intent_id"] == result.intent_id
