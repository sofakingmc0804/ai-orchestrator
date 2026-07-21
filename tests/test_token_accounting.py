from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import Settings
from orchestrator.state.store import StateStore
from orchestrator.ui import server as fastapi_server
from orchestrator.usage.accounting import build_token_accounting_payload


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_token_accounting_aggregates_multi_attempt_directive(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    store = StateStore(settings)
    await store.initialize()

    for row in (
        {
            "id": "tok_failed_attempt",
            "dispatch_id": "dsp_1",
            "attempt_id": "att_1",
            "intent_id": "int_directive",
            "adapter_name": "claude-cli",
            "provider": "anthropic",
            "model": "claude-opus",
            "tokens_in": 40,
            "tokens_out": 10,
            "tokens_total": 50,
            "success": False,
            "token_source": "result.usage",
            "confidence": "exact",
            "raw_usage_json": "{}",
            "created_at": "2026-06-14T00:00:00+00:00",
        },
        {
            "id": "tok_success_attempt",
            "dispatch_id": "dsp_1",
            "attempt_id": "att_2",
            "intent_id": "int_directive",
            "adapter_name": "claude-cli",
            "provider": "anthropic",
            "model": "claude-opus",
            "tokens_in": 60,
            "tokens_out": 20,
            "tokens_total": 80,
            "success": True,
            "token_source": "result.usage",
            "confidence": "exact",
            "raw_usage_json": "{}",
            "created_at": "2026-06-14T00:01:00+00:00",
        },
    ):
        await store.record_token_usage(row)

    payload = await build_token_accounting_payload(store, limit=10)

    assert payload["proof_kind"] == "live"
    assert payload["totals"]["completed_directives"] == 1
    assert payload["totals"]["tokens_total"] == 130
    assert payload["totals"]["tokens_per_completed_directive"] == 130
    directive = payload["directives"][0]
    assert directive["directive_id"] == "int_directive"
    assert directive["attempts"] == 2
    assert directive["successful_attempts"] == 1
    assert directive["completed"] is True
    assert directive["tokens_in"] == 100
    assert directive["tokens_out"] == 30


def test_token_accounting_api_and_dashboard_show_cost_vs_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_services() -> tuple[list[Any], list[Any]]:
        return [], []

    monkeypatch.setattr(fastapi_server, "discover_services_and_capabilities", fake_services)
    monkeypatch.setattr(fastapi_server, "discover_projects", lambda *_args, **_kwargs: [])

    async def fake_supervisor_tick(_settings: Settings, _store: StateStore, **_kwargs: object) -> dict[str, object]:
        return {"state": "produced", "proof_kind": "live"}

    monkeypatch.setattr(fastapi_server, "run_supervisor_tick", fake_supervisor_tick)
    monkeypatch.setattr(fastapi_server, "start_supervisor_thread", lambda _settings: object())

    settings = settings_for(tmp_path)
    app = fastapi_server.create_app(settings)

    with TestClient(app) as client:
        store = StateStore(settings)

        import anyio

        async def seed() -> None:
            await store.db.execute(
                """
                INSERT INTO worker_cards(worker_id, model_id, base_model, surface, provider_id, contract_type,
                  capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json,
                  marginal_cost_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                "claude-opus@anthropic",
                "claude-opus",
                "claude-opus",
                "anthropic",
                "anthropic",
                "subscription_usage",
                '["coding"]',
                '["tools"]',
                '["text"]',
                '{"coding": 9}',
                '["repo_coding"]',
                "[]",
                '{"unit":"subscription_tokens"}',
            )
            await store.record_token_usage(
                {
                    "id": "tok_api_premium",
                    "dispatch_id": "dsp_api",
                    "attempt_id": "att_api",
                    "intent_id": "int_api",
                    "adapter_name": "claude-cli",
                    "provider": "anthropic",
                    "model": "claude-opus",
                    "tokens_in": 75,
                    "tokens_out": 25,
                    "tokens_total": 100,
                    "success": True,
                    "token_source": "result.usage",
                    "confidence": "exact",
                    "raw_usage_json": "{}",
                    "created_at": "2026-06-14T00:00:00+00:00",
                }
            )
            await store.record_operation_quality_score(
                {
                    "id": "oqs_api_premium",
                    "dispatch_id": "dsp_api",
                    "worker_id": "claude-opus@anthropic",
                    "operation_domain": "repo_coding",
                    "validator_name": "relative_tournament",
                    "composite_score": 0.82,
                    "dimensional_scores": {"relative_accuracy": 0.82},
                    "task_id": "BENCH-P5-2",
                    "proof_kind": "live",
                    "validation": {"ranking": 1},
                    "created_at": "2026-06-14T00:02:00+00:00",
                }
            )

        anyio.run(seed)
        response = client.get("/api/token-accounting?limit=10")
        index = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["tokens_per_completed_directive"] == 100
    assert payload["premium_agents"][0]["agent_id"] == "claude-opus@anthropic"
    assert payload["premium_agents"][0]["avg_quality_score"] == 0.82
    assert payload["premium_agents"][0]["tokens_per_completed_directive"] == 100
    assert index.status_code == 200
    assert "Platform Console" in index.text
    assert "Tokens Per Directive" in index.text
    assert "/static/app.js" in index.text
    assert "tokenAccountingList" in index.text
