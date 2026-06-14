from __future__ import annotations

import threading
import argparse
import asyncio
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator.models import DispatchResult
from orchestrator.config import Settings
from orchestrator.cli.governance import cmd_route
import orchestrator.ui.simple_server as simple_server
import orchestrator.ui.server as fastapi_server
from orchestrator.ui.simple_server import make_handler
from orchestrator.state.store import StateStore


def test_simple_server_prove_adapter_api_passes_governed_request() -> None:
    calls: list[dict[str, Any]] = []

    class FakeDispatcher:
        async def prove_adapter(self, adapter_name: str, prompt: str, capability_id: str | None = None, allow_subscription: bool = False) -> DispatchResult:
            calls.append(
                {
                    "adapter_name": adapter_name,
                    "prompt": prompt,
                    "capability_id": capability_id,
                    "allow_subscription": allow_subscription,
                }
            )
            return DispatchResult(dispatch_id="dsp_ui", intent_id="int_ui", adapter_name=adapter_name, state="completed", result_text="OK")

    class FakeRuntime:
        dispatcher = FakeDispatcher()

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeRuntime()))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(
            f"http://127.0.0.1:{server.server_port}/api/prove-adapter",
            json={
                "adapter_name": "copilot-gh",
                "capability": "coding_chat",
                "prompt": "proof",
                "allow_subscription": True,
            },
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()

    assert response.status_code == 200
    assert response.json()["state"] == "completed"
    assert calls == [
        {
            "adapter_name": "copilot-gh",
            "prompt": "proof",
            "capability_id": "coding_chat",
            "allow_subscription": True,
        }
    ]


def test_simple_server_retry_repairs_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_retry_open_repairs(settings: object, store: object) -> dict[str, object]:
        calls.append({"settings": settings, "store": store})
        return {"attempted": 1, "remaining_open_repairs": []}

    class FakeRuntime:
        settings = object()
        store = object()

    monkeypatch.setattr(simple_server, "retry_open_repairs", fake_retry_open_repairs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeRuntime()))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(f"http://127.0.0.1:{server.server_port}/api/retry-repairs", timeout=10)
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()

    assert response.status_code == 200
    assert response.json()["attempted"] == 1
    assert len(calls) == 1


def test_simple_server_token_flow_api_returns_metered_rows() -> None:
    class FakeStore:
        async def list_token_usage(self, limit: int = 200) -> list[dict[str, Any]]:
            return [
                {
                    "dispatch_id": "dsp_meter",
                    "provider": "ollama-local",
                    "model": "qwen",
                    "tokens_in": 10,
                    "tokens_out": 5,
                    "tokens_total": 15,
                    "success": 1,
                }
            ][:limit]

        async def token_usage_summary(self, limit: int = 1000) -> dict[str, dict[str, Any]]:
            return {
                "ollama-local|qwen": {
                    "provider": "ollama-local",
                    "model": "qwen",
                    "attempts": 1,
                    "tokens_total": 15,
                    "tokens_in": 10,
                    "tokens_out": 5,
                    "success_rate": 1.0,
                }
            }

    class FakeRuntime:
        store = FakeStore()

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeRuntime()))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.get(f"http://127.0.0.1:{server.server_port}/api/token-flow?limit=1", timeout=10)
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["tokens_total"] == 15
    assert payload["by_provider_model"]["ollama-local|qwen"]["success_rate"] == 1.0
    assert payload["attempts"][0]["dispatch_id"] == "dsp_meter"


def test_simple_server_dashboard_routes_and_api_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStore:
        async def list_dispatches(self, limit: int = 100) -> list[dict[str, Any]]:
            return [{"id": "dsp_ui", "state": "completed", "started_at": "2026-06-11T00:00:00Z"}]

        async def record_quota_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
            return None

        async def list_worker_cards(self) -> list[dict[str, Any]]:
            return [{"worker_id": "qwen@ollama-local", "contract_type": "local_resource"}]

        async def list_receipts(self, limit: int = 100) -> list[dict[str, Any]]:
            return [{"dispatch_id": "dsp_ui", "success": 1, "tokens_in": 2, "tokens_out": 3}]

        async def list_budget_probes(self) -> list[dict[str, Any]]:
            return [{"provider_id": "ollama", "remaining": 999, "limit": 1000, "ok": 1}]

        async def upsert_budget_probes(self, probes: list[dict[str, Any]]) -> dict[str, int]:
            return {"stored": len(probes), "failed": 0, "total": len(probes)}

        async def list_subscription_usage_snapshots(self) -> list[dict[str, Any]]:
            return [
                {
                    "service_id": "openai_chatgpt",
                    "account_id": "matt@example.com",
                    "profile_id": "default",
                    "subscription_name": "OpenAI ChatGPT / Codex",
                    "tokens_used_by_app": 25,
                    "tokens_used_total": 100,
                    "tokens_remaining": 900,
                    "confidence": "cli_exact",
                    "checked_at": "2026-06-11T00:00:00Z",
                }
            ]

        async def upsert_subscription_usage_snapshots(self, snapshots: list[dict[str, Any]]) -> dict[str, int]:
            return {"stored": len(snapshots), "failed": 0, "total": len(snapshots)}

        async def list_api_budget_policies(self) -> list[dict[str, Any]]:
            return []

    class FakeRuntime:
        settings = type(
            "Settings",
            (),
            {"state_path": "state.sqlite", "notifications_path": "notifications.jsonl"},
        )()
        store = FakeStore()

    monkeypatch.setattr(simple_server, "probe_auth_and_quota", lambda: {"probed_at": "2026-06-11T00:00:00Z"})
    monkeypatch.setattr(simple_server, "quota_snapshots", lambda _auth: [])
    monkeypatch.setattr(simple_server, "run_all_probes", lambda: [])

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeRuntime()))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        workers_page = httpx.get(f"{base}/workers", timeout=10)
        status = httpx.get(f"{base}/api/status", timeout=10).json()
        workers = httpx.get(f"{base}/api/workers", timeout=10).json()
        receipts = httpx.get(f"{base}/api/receipts?limit=1", timeout=10).json()
        budget = httpx.get(f"{base}/api/budget", timeout=10).json()
        subscriptions = httpx.get(f"{base}/api/subscriptions", timeout=10).json()
        api_budgets = httpx.get(f"{base}/api/api-budgets", timeout=10).json()
        budget_refresh = httpx.post(f"{base}/api/budget/refresh", timeout=10).json()
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()

    assert workers_page.status_code == 200
    assert "Workers" in workers_page.text
    assert status["dispatches"][0]["id"] == "dsp_ui"
    assert workers["workers"][0]["worker_id"] == "qwen@ollama-local"
    assert receipts["receipts"][0]["dispatch_id"] == "dsp_ui"
    assert budget["budget_model"] == "split"
    assert subscriptions["subscriptions"][0]["service_id"] == "openai_chatgpt"
    assert api_budgets["legacy_probes"][0]["provider_id"] == "ollama"
    assert budget_refresh["total"] == 0


def test_dashboard_contains_token_flow_surface() -> None:
    html = (Path(__file__).resolve().parents[1] / "orchestrator" / "ui" / "static" / "index.html").read_text(encoding="utf-8")

    assert "tokenFlowTotal" in html
    assert "tokenFlowList" in html
    assert "fetch('/api/token-flow?limit=10')" in html


def test_primary_fastapi_route_api_and_app_js_are_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_services() -> tuple[list[Any], list[Any]]:
        return [], []

    monkeypatch.setattr(fastapi_server, "discover_services_and_capabilities", fake_services)
    monkeypatch.setattr(fastapi_server, "discover_projects", lambda *_args, **_kwargs: [])

    async def fake_supervisor_tick(_settings: Settings, _store: StateStore, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced", "proof_kind": "live"}

    monkeypatch.setattr(fastapi_server, "run_supervisor_tick", fake_supervisor_tick)
    monkeypatch.setattr(fastapi_server, "start_supervisor_thread", lambda _settings: object())

    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    app = fastapi_server.create_app(settings)
    assert app.router.on_startup == []

    with TestClient(app) as client:
        store = StateStore(settings)
        client.get("/api/status")
        import anyio

        async def seed() -> None:
            await store.db.execute(
                "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
                "repo_coding",
                '["coding", "tools"]',
                "{}",
                0,
                "local_resource",
            )
            await store.db.execute(
                """
                INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
                  capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                "qwen@ollama-local",
                "qwen",
                "qwen",
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

        anyio.run(seed)
        index = client.get("/")
        route = client.post("/api/route", json={"text": "fix this repo bug", "job_class": "repo_coding"})

    assert index.status_code == 200
    assert '<script src="/static/app.js" defer></script>' in index.text
    assert 'id="routeText"' in index.text
    assert route.status_code == 200
    payload = route.json()
    assert payload["job_class"] == "repo_coding"
    assert payload["decision"]["chosen_adapter"] == "ollama-http"
    assert payload["decision"]["candidates_considered"][0]["worker_id"] == "qwen@ollama-local"


def test_main_does_not_silently_fallback_to_simple_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.main as main_module

    monkeypatch.setattr(main_module.Settings, "load", lambda: object())

    def broken_create_app(_settings: object) -> object:
        raise TypeError("on_startup is not supported")

    monkeypatch.setattr(main_module, "create_app", broken_create_app)
    assert not hasattr(main_module, "run_simple_server")

    with pytest.raises(TypeError, match="on_startup"):
        main_module.main()


def test_cli_and_api_route_return_identical_brain_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_services() -> tuple[list[Any], list[Any]]:
        return [], []

    monkeypatch.setattr(fastapi_server, "discover_services_and_capabilities", fake_services)
    monkeypatch.setattr(fastapi_server, "discover_projects", lambda *_args, **_kwargs: [])

    async def fake_supervisor_tick(_settings: Settings, _store: StateStore, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced", "proof_kind": "live"}

    monkeypatch.setattr(fastapi_server, "run_supervisor_tick", fake_supervisor_tick)
    monkeypatch.setattr(fastapi_server, "start_supervisor_thread", lambda _settings: object())
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    app = fastapi_server.create_app(settings)

    with TestClient(app) as client:
        store = StateStore(settings)

        async def seed() -> None:
            await store.db.execute(
                "INSERT INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor) VALUES(?,?,?,?,?)",
                "repo_coding",
                '["coding", "tools"]',
                "{}",
                0,
                "local_resource",
            )
            await store.db.execute(
                """
                INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
                  capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                "qwen@ollama-local",
                "qwen",
                "qwen",
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

        asyncio.run(seed())
        api_payload = client.post("/api/route", json={"text": "fix this repo bug", "job_class": "repo_coding"}).json()

        asyncio.run(
            cmd_route(
                argparse.Namespace(
                    settings=settings,
                    job_class="repo_coding",
                    text="fix this repo bug",
                    dry_run=True,
                    execute=False,
                    approve=False,
                )
            )
        )
        cli_payload = json.loads(capsys.readouterr().out)

    assert cli_payload == api_payload
    assert cli_payload["decision"]["chosen_adapter"] == "ollama-http"
    assert cli_payload["ranked_ladder"][0]["worker_id"] == "qwen@ollama-local"
