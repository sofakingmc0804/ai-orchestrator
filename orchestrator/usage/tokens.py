from __future__ import annotations

import json
import math
from typing import Any


def estimate_tokens(text: str) -> int:
    """Cheap, conservative token estimate for providers that do not report usage."""
    value = (text or "").strip()
    if not value:
        return 0
    by_chars = math.ceil(len(value) / 4)
    by_words = math.ceil(len(value.split()) * 1.3)
    return max(1, by_chars, by_words)


def _int_value(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _raw_usage(result: dict[str, Any]) -> tuple[dict[str, Any], str]:
    usage = _dict_value(result.get("usage"))
    if usage:
        return usage, "result.usage"
    raw = _dict_value(result.get("raw"))
    usage = _dict_value(raw.get("usage"))
    if usage:
        return usage, "raw.usage"
    if "prompt_eval_count" in raw or "eval_count" in raw:
        return raw, "ollama.raw"
    return {}, ""


def extract_token_usage(result: dict[str, Any], envelope: dict[str, Any], adapter_name: str) -> dict[str, Any]:
    """Return best available token accounting for one adapter attempt.

    Confidence is `exact` when the provider reported usage, otherwise
    `estimated` from prompt/output text. Estimates are still useful as a
    flowmeter; they are not used as invoice-grade accounting.
    """
    usage, source = _raw_usage(result)
    tokens_in = _int_value(
        usage.get("prompt_tokens"),
        usage.get("input_tokens"),
        usage.get("prompt_eval_count"),
    )
    tokens_out = _int_value(
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
        usage.get("eval_count"),
    )
    total = _int_value(usage.get("total_tokens"))

    confidence = "exact" if source else "estimated"
    intent = _dict_value(envelope.get("intent"))
    prompt = str(intent.get("raw_text") or envelope.get("prompt") or "")
    text = str(result.get("text") or "")
    if tokens_in is None:
        tokens_in = estimate_tokens(prompt)
    if tokens_out is None:
        tokens_out = estimate_tokens(text)
    if total is None:
        total = int(tokens_in or 0) + int(tokens_out or 0)

    return {
        "adapter_name": adapter_name,
        "provider": str(envelope.get("provider") or ""),
        "model": str(result.get("model") or envelope.get("model") or ""),
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "tokens_total": int(total or 0),
        "token_source": source or "text_estimate",
        "confidence": confidence,
        "raw_usage_json": json.dumps(usage, sort_keys=True),
    }


def flowmeter_snapshot(
    usage: dict[str, Any],
    budget_probe: dict[str, Any] | None,
) -> dict[str, Any]:
    limit = _int_value((budget_probe or {}).get("limit")) or 0
    remaining = _int_value((budget_probe or {}).get("remaining")) or 0
    total = int(usage.get("tokens_total") or 0)
    after = max(remaining - total, 0) if limit > 0 else None
    ratio = (after / limit) if after is not None and limit > 0 else None
    return {
        **usage,
        "quota_provider": (budget_probe or {}).get("provider_id") or usage.get("provider"),
        "quota_remaining_before": remaining if limit > 0 else None,
        "quota_remaining_after_estimate": after,
        "quota_limit": limit if limit > 0 else None,
        "quota_ratio_after_estimate": ratio,
        "quota_probe_type": (budget_probe or {}).get("probe_type"),
        "quota_probe_ok": (budget_probe or {}).get("ok"),
    }
