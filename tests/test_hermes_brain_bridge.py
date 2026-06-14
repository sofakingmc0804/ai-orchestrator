from __future__ import annotations

import json
import base64
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.cli.main import _hermes_route
from orchestrator.hermes.brain_bridge import route_for_hermes_prompt
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )


async def _seed_routeable_worker(store: StateStore) -> None:
    await store.db.execute(
        "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
        "repo_coding",
        '["coding", "tools"]',
        "{}",
        0,
        "local_resource",
    )
    await store.db.execute(
        "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
        "routing_triage",
        '["coding"]',
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
        '{"coding": 8, "speed": 7, "stability": 7}',
        '["repo_coding"]',
        "[]",
    )


@pytest.mark.asyncio
async def test_hermes_brain_bridge_writes_live_route_receipt(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await _seed_routeable_worker(store)

    receipt = await route_for_hermes_prompt(
        settings,
        text="fix this repo bug",
        job_class="repo_coding",
        argv=["-z", "fix this repo bug"],
    )

    receipt_path = Path(receipt["receipt_path"])
    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["state"] == "produced"
    assert receipt["proof_kind"] == "live"
    assert receipt["selected"]["worker_id"] == "qwen2.5-coder:7b@ollama-local"
    assert receipt["selected"]["adapter_name"] == "ollama-http"
    assert receipt["selected"]["hermes_provider"] == "custom"
    assert receipt["selected"]["model_id"] == "qwen2.5-coder:7b"
    assert on_disk["route"]["decision"]["chosen_adapter"] == "ollama-http"


@pytest.mark.asyncio
async def test_hermes_route_cli_accepts_base64_argv_from_powershell(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await _seed_routeable_worker(store)
    argv = ["-z", "fix this repo bug", "--provider", "custom"]
    argv_b64 = base64.b64encode(json.dumps(argv).encode("utf-8")).decode("ascii")

    receipt = await _hermes_route(settings, "fix this repo bug", "repo_coding", None, argv_b64)

    assert receipt["state"] == "produced"
    assert receipt["input"]["argv"] == argv
    assert Path(str(receipt["receipt_path"])).exists()


@pytest.mark.asyncio
async def test_hermes_prompt_without_known_job_class_falls_back_to_routing_triage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await _seed_routeable_worker(store)

    receipt = await route_for_hermes_prompt(settings, text="Return exactly OK.", argv=["-z", "Return exactly OK."])

    assert receipt["state"] == "produced"
    assert receipt["route"]["job_class"] == "routing_triage"
    assert receipt["route"]["classification"]["fallback_from_job_class"] == "quick_question"


def test_routing_package_has_no_hermes_name_special_cases() -> None:
    routing_dir = Path("orchestrator/routing")
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in routing_dir.glob("*.py"))
    assert "hermes" not in text
    assert "nous" not in text
