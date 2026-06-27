from __future__ import annotations

import asyncio
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator.config import Settings
from orchestrator.models import BillingClass, Capability, ConsequenceTier, HealthState, ServiceInfo
from orchestrator.state.store import StateStore
import orchestrator.ui.server as fastapi_server
from orchestrator.ui.simple_server import make_handler


SALVAGE_RECEIPT = Path(".runtime/orchestrator/receipts/consolidation-example-codex-agent-salvage-20260617T023043Z.json")


def _write_salvage_receipt(repo_root: Path) -> Path:
    receipt_path = repo_root / SALVAGE_RECEIPT
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "receipt_type": "consolidation_example_codex_agent_salvage",
                "terminal_state": "investigated_and_routed",
                "source_path": "C:\\Users\\Couch\\AI Projects Folder\\Example-Codex-Agent",
                "target_consumer": "C:\\Users\\Couch\\dev\\ai-orchestrator",
                "decision": "salvage_ui_and_connector_primitives_into_ai_orchestrator_do_not_run_as_separate_service",
                "salvage_primitives": [
                    {"primitive": "connector_management_ui", "target": "ai-orchestrator connector/MCP admin surface"},
                    {"primitive": "mcp_config_generation", "target": "ai-orchestrator connector configuration generator"},
                    {"primitive": "google_oauth_reference_only", "target": "reference pattern only; do not import tokens or credentials"},
                ],
                "forbidden_substitutes": [
                    "do not start Example-Codex-Agent as a standalone daemon",
                    "do not copy Google tokens or credentials",
                    "do not create a second dashboard authority",
                ],
            }
        ),
        encoding="utf-8",
    )
    return receipt_path


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / ".runtime" / "orchestrator",
        state_path=tmp_path / ".runtime" / "orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".runtime" / "orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".runtime" / "orchestrator" / "logs",
        repo_root=tmp_path,
    )


def _patch_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_services() -> tuple[list[Any], list[Any]]:
        return [], []

    async def fake_supervisor_tick(_settings: Settings, _store: StateStore, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced", "proof_kind": "live"}

    monkeypatch.setattr(fastapi_server, "discover_services_and_capabilities", fake_services)
    monkeypatch.setattr(fastapi_server, "discover_projects", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fastapi_server, "run_supervisor_tick", fake_supervisor_tick)
    monkeypatch.setattr(fastapi_server, "start_supervisor_thread", lambda _settings: object())


async def _seed_services(settings: Settings) -> None:
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_services(
        [
            ServiceInfo(
                id="local-drive-mcp",
                name="Local Drive MCP",
                service_group="mcp",
                adapter_name="local-drive-mcp",
                protocol="mcp-http",
                health_state=HealthState.HEALTHY,
                version="receipt-backed",
            )
        ]
    )
    await store.upsert_capabilities(
        [
            Capability(
                id="local-drive-mcp:file_search",
                adapter_name="local-drive-mcp",
                capability_id="file_search",
                rating_instruction=4,
                rating_quality=4,
                latency_band="medium",
                consequence_max=ConsequenceTier.MEDIUM,
                billing_class=BillingClass.LOCAL_RESOURCE,
            )
        ]
    )


def test_fastapi_connectors_page_and_inventory_use_authority_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_startup(monkeypatch)
    _write_salvage_receipt(tmp_path)
    settings = _settings(tmp_path)
    app = fastapi_server.create_app(settings)

    with TestClient(app) as client:
        asyncio.run(_seed_services(settings))
        page = client.get("/connectors")
        payload = client.get("/api/connectors").json()

    assert page.status_code == 200
    assert 'id="connectorsPanel"' in page.text
    assert "/api/connectors/config-preview" in page.text
    assert payload["state"] == "produced"
    assert payload["source"] == "state_store.services"
    assert payload["authority"]["decision"] == "salvage_ui_and_connector_primitives_into_ai_orchestrator_do_not_run_as_separate_service"
    assert payload["runtime_boundary"] == "no_second_service_started"
    assert "do not copy Google tokens or credentials" in payload["forbidden_substitutes"]
    assert payload["connectors"][0]["id"] == "local-drive-mcp"
    assert payload["connectors"][0]["capabilities"] == ["file_search"]


def test_config_preview_uses_env_references_and_rejects_secret_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_startup(monkeypatch)
    _write_salvage_receipt(tmp_path)
    settings = _settings(tmp_path)
    app = fastapi_server.create_app(settings)

    with TestClient(app) as client:
        safe = client.post(
            "/api/connectors/config-preview",
            json={
                "name": "local-drive",
                "transport": "stdio",
                "command": "node",
                "args": ["server.js"],
                "env_vars": ["LOCAL_DRIVE_TOKEN"],
                "enabled": True,
            },
        )
        unsafe = client.post(
            "/api/connectors/config-preview",
            json={
                "name": "unsafe",
                "transport": "stdio",
                "command": "node",
                "env": {"LOCAL_DRIVE_TOKEN": "secret-value"},
            },
        )
        unsafe_header = client.post(
            "/api/connectors/config-preview",
            json={
                "name": "unsafe-http",
                "transport": "http",
                "url": "https://example.invalid/mcp",
                "http_headers": {"Authorization": "Bearer secret-value"},
            },
        )

    assert safe.status_code == 200
    config_text = safe.json()["config_text"]
    assert '[mcp_servers."local-drive"]' in config_text
    assert 'env_vars = ["LOCAL_DRIVE_TOKEN"]' in config_text
    assert "secret-value" not in config_text
    assert "auth.json" not in config_text
    assert "cap_sid" not in config_text
    assert "Example-Codex-Agent" not in config_text
    assert unsafe.status_code == 400
    assert unsafe.json()["detail"]["state"] == "rejected"
    assert unsafe.json()["detail"]["reason"] == "credential_values_are_not_accepted"
    assert unsafe_header.status_code == 400
    assert unsafe_header.json()["detail"]["state"] == "rejected"
    assert unsafe_header.json()["detail"]["reason"] == "credential_values_are_not_accepted"


def test_simple_server_exposes_connectors_page_and_api(tmp_path: Path) -> None:
    _write_salvage_receipt(tmp_path)

    class FakeStore:
        async def list_services(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "claude-desktop-mcp",
                    "name": "Claude Desktop MCP",
                    "service_group": "mcp",
                    "adapter_name": "claude-desktop-mcp",
                    "protocol": "mcp",
                    "health_state": "healthy",
                }
            ]

        async def list_capabilities(self) -> list[dict[str, Any]]:
            return [{"adapter_name": "claude-desktop-mcp", "capability_id": "session_chat"}]

    class FakeRuntime:
        settings = _settings(tmp_path)
        store = FakeStore()

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeRuntime()))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        page = httpx.get(f"{base}/connectors", timeout=10)
        payload = httpx.get(f"{base}/api/connectors", timeout=10).json()
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()

    assert page.status_code == 200
    assert "Connector Management" in page.text
    assert payload["connectors"][0]["id"] == "claude-desktop-mcp"
    assert payload["connectors"][0]["capabilities"] == ["session_chat"]
