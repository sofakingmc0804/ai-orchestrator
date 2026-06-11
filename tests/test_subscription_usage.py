from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.discovery.subscription_usage import parse_usage_output, snapshot_to_dict, SubscriptionSnapshot
from orchestrator.state.store import StateStore


def test_parse_subscription_usage_json() -> None:
    payload = {
        "account": {"email": "matt@example.com"},
        "profile": "work",
        "plan": "Pro",
        "usage": {"used": 1200, "remaining": 8800, "limit": 10000},
        "reset_at": "2026-07-01T00:00:00Z",
    }

    parsed = parse_usage_output(json.dumps(payload))

    assert parsed["account_id"] == "matt@example.com"
    assert parsed["profile_id"] == "work"
    assert parsed["plan_name"] == "Pro"
    assert parsed["tokens_used_total"] == 1200
    assert parsed["tokens_remaining"] == 8800
    assert parsed["tokens_limit"] == 10000
    assert parsed["reset_at"] == "2026-07-01T00:00:00Z"


def test_parse_subscription_usage_text() -> None:
    parsed = parse_usage_output(
        """
        Account: matt@example.com
        Plan: Team
        Tokens used: 2,500
        Tokens remaining: 7,500
        Token limit: 10,000
        Reset: 2026-07-01
        """
    )

    assert parsed["account_id"] == "matt@example.com"
    assert parsed["plan_name"] == "Team"
    assert parsed["tokens_used_total"] == 2500
    assert parsed["tokens_remaining"] == 7500
    assert parsed["tokens_limit"] == 10000


@pytest.mark.asyncio
async def test_subscription_snapshots_store_app_and_elsewhere_tokens(tmp_path: Path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    store = StateStore(settings)
    await store.initialize()
    await store.record_token_usage(
        {
            "id": "tok_openai",
            "provider": "openai",
            "adapter_name": "codex",
            "tokens_in": 10,
            "tokens_out": 15,
            "tokens_total": 25,
            "success": True,
            "token_source": "test",
            "confidence": "exact",
        }
    )
    usage = await store.token_usage_by_subscription_source()
    snapshot = SubscriptionSnapshot(
        service_id="openai_chatgpt",
        account_id="matt@example.com",
        profile_id="default",
        subscription_name="OpenAI ChatGPT / Codex",
        plan_name="Pro",
        source_type="cli",
        source_command="codex usage --json",
        tokens_limit=1000,
        tokens_used_total=100,
        tokens_remaining=900,
        tokens_used_by_app=usage["openai_chatgpt"],
        tokens_used_elsewhere=100 - usage["openai_chatgpt"],
        reset_at=None,
        checked_at="2026-06-11T00:00:00Z",
        ok=True,
        confidence="cli_exact",
        status="ok",
        error=None,
        raw={"test": True},
        usage_windows=[{"label": "primary", "remaining_percent": 90, "used_percent": 10}],
    )

    result = await store.upsert_subscription_usage_snapshots([snapshot_to_dict(snapshot)])
    rows = await store.list_subscription_usage_snapshots()

    assert result == {"stored": 1, "failed": 0, "total": 1}
    assert rows[0]["tokens_used_by_app"] == 25
    assert rows[0]["tokens_used_elsewhere"] == 75
    assert json.loads(rows[0]["usage_windows_json"])[0]["label"] == "primary"
