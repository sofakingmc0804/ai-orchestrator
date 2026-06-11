from __future__ import annotations

import json

from orchestrator.usage.tokens import estimate_tokens, extract_token_usage, flowmeter_snapshot


def test_extracts_ollama_raw_token_counts() -> None:
    usage = extract_token_usage(
        {
            "ok": True,
            "model": "qwen2.5-coder",
            "text": "done",
            "raw": {"prompt_eval_count": 11, "eval_count": 7},
        },
        {"intent": {"raw_text": "fix bug"}, "provider": "ollama-local"},
        "ollama-http",
    )

    assert usage["tokens_in"] == 11
    assert usage["tokens_out"] == 7
    assert usage["tokens_total"] == 18
    assert usage["token_source"] == "ollama.raw"
    assert usage["confidence"] == "exact"
    assert json.loads(usage["raw_usage_json"])["eval_count"] == 7


def test_extracts_openai_compatible_usage_counts() -> None:
    usage = extract_token_usage(
        {
            "ok": True,
            "model": "local-openai-compatible",
            "text": "done",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
        {"intent": {"raw_text": "prove it"}, "provider": "lm-studio"},
        "lm-studio",
    )

    assert usage["tokens_in"] == 5
    assert usage["tokens_out"] == 3
    assert usage["tokens_total"] == 8
    assert usage["token_source"] == "result.usage"
    assert usage["confidence"] == "exact"


def test_estimates_when_provider_does_not_report_usage() -> None:
    usage = extract_token_usage(
        {"ok": False, "text": "failed for now"},
        {"intent": {"raw_text": "classify this compact smoke request"}, "provider": "cli"},
        "codex-cli",
    )

    assert usage["tokens_in"] == estimate_tokens("classify this compact smoke request")
    assert usage["tokens_out"] == estimate_tokens("failed for now")
    assert usage["tokens_total"] == usage["tokens_in"] + usage["tokens_out"]
    assert usage["token_source"] == "text_estimate"
    assert usage["confidence"] == "estimated"


def test_flowmeter_snapshot_estimates_quota_after_attempt() -> None:
    snapshot = flowmeter_snapshot(
        {"provider": "ollama-cloud", "tokens_total": 125},
        {"provider_id": "ollama-cloud", "probe_type": "subscription_usage", "remaining": 1000, "limit": 2000, "ok": True},
    )

    assert snapshot["quota_provider"] == "ollama-cloud"
    assert snapshot["quota_remaining_before"] == 1000
    assert snapshot["quota_remaining_after_estimate"] == 875
    assert snapshot["quota_ratio_after_estimate"] == 0.4375
    assert snapshot["quota_probe_ok"] is True
