"""Translation rules for workers that execute through Hermes providers.

The orchestrator may know about more workers than Hermes can execute directly.
This module keeps that distinction explicit: a worker is only marked
``supported`` when its contract is allowed and Hermes has a native provider
name for it.  Paid or unclassified API rows remain visible in receipts but are
never converted into executable Hermes arguments.
"""

from __future__ import annotations

from typing import Any


FORBIDDEN_CONTRACTS = {"metered_extra_cost", "third_party_metered", "unknown_cost"}

# These names are the provider ids accepted by Hermes' runtime provider
# resolver.  Aliases on worker cards are intentionally kept here rather than
# scattered through the shim and routing code.
HERMES_PROVIDER_ALIASES = {
    "codex": "openai-codex",
    "codex-chatgpt": "openai-codex",
    "openai-codex": "openai-codex",
    "nous": "nous",
    "hermes-nous": "nous",
    "nous-portal": "nous",
    "openrouter": "openrouter",
    "opencode": "opencode-zen",
    "opencode-zen": "opencode-zen",
    "ollama-cloud": "ollama-cloud",
    "ollama": "custom",
    "ollama-local": "custom",
    "ollama-http": "custom",
    "ollama-cli": "custom",
    "lmstudio": "custom",
    "lm-studio": "custom",
    "anthropic": "anthropic",
    "anthropic-api": "anthropic",
    "openai-api": "openai-api",
}


def _provider_key(selected: dict[str, Any]) -> str:
    return str(selected.get("provider_id") or selected.get("provider") or selected.get("surface") or "").strip().lower()


def hermes_lane_for_selected(selected: dict[str, Any] | None) -> dict[str, Any]:
    """Return the executable Hermes translation for one selected worker."""

    if not selected:
        return {
            "hermes_provider": None,
            "hermes_model": None,
            "lane_state": "unselected",
            "lane_reason": "No worker was selected.",
        }

    provider_key = _provider_key(selected)
    hermes_provider = HERMES_PROVIDER_ALIASES.get(provider_key)
    contract = str(selected.get("contract_type") or "").strip().lower()
    model = str(selected.get("model_id") or selected.get("recommended_model") or "").strip() or None

    if contract in FORBIDDEN_CONTRACTS:
        return {
            "hermes_provider": hermes_provider,
            "hermes_model": model,
            "lane_state": "blocked_metered",
            "lane_reason": f"Worker contract {contract} is not executable without explicit billing authorization.",
        }
    dispatch_adapter = str(selected.get("dispatch_adapter") or selected.get("adapter_name") or "").strip().lower()
    if provider_key == "codex-chatgpt" or dispatch_adapter in {"codex-desktop", "codex-cli"}:
        return {
            "hermes_provider": hermes_provider,
            "hermes_model": model,
            "lane_state": "dispatch_only",
            "lane_reason": "This worker is dispatched through its direct Codex adapter, not the Hermes executable.",
        }
    if hermes_provider == "openrouter" and not (model and model.lower().endswith(":free")):
        return {
            "hermes_provider": hermes_provider,
            "hermes_model": model,
            "lane_state": "blocked_metered",
            "lane_reason": "OpenRouter is executable only for an explicitly free model in this plan.",
        }
    if not hermes_provider:
        return {
            "hermes_provider": None,
            "hermes_model": model,
            "lane_state": "dispatch_only",
            "lane_reason": f"No native Hermes provider mapping exists for {provider_key or 'unknown provider'}.",
        }

    return {
        "hermes_provider": hermes_provider,
        "hermes_model": model,
        "lane_state": "supported",
        "lane_reason": f"{provider_key} is translated to Hermes provider {hermes_provider} under {contract or 'unclassified'} contract.",
    }
