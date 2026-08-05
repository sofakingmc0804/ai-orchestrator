from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "hermes" / "live_capacity.py"


def _module():
    assert MODULE_PATH.is_file(), "live capacity reconciler is not registered"
    spec = importlib.util.spec_from_file_location("live_capacity_under_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openrouter_free_worker_is_classified_as_safe_live_quota() -> None:
    module = _module()

    assert module.provider_contract("openrouter", model_id="nvidia/nemotron-3-ultra:free", cost_proved=True) == "subscription_usage"
    worker = module.build_live_worker_card(
        provider_id="openrouter",
        model_id="nvidia/nemotron-3-ultra:free",
        contract_type="subscription_usage",
        quota_state={"status": "ok", "source": "openrouter_key_api"},
    )

    assert worker["worker_id"] == "nvidia/nemotron-3-ultra:free@openrouter"
    assert worker["surface"] == "openrouter"
    assert worker["dynamic_state"]["refresh_status"] == "ok"
    assert "live_model_catalog" in worker["source_evidence"]


def test_claude_subscription_oauth_worker_is_classified_as_subscription_quota() -> None:
    module = _module()
    assert module.provider_contract("claude-max", model_id="sonnet", cost_proved=True) == "subscription_quota"
    worker = module.build_live_worker_card(
        provider_id="claude-max",
        model_id="sonnet",
        contract_type="subscription_quota",
        quota_state={"status": "ok", "source": "claude_code_oauth_status"},
    )
    assert worker["surface"] == "claude-max"
    assert worker["contract_type"] == "subscription_quota"


def test_opencode_zen_without_included_quota_is_visible_but_blocked() -> None:
    module = _module()

    assert module.provider_contract("opencode-zen", model_id="deepseek-v4-flash-free", cost_proved=False) == "third_party_metered"
    worker = module.build_live_worker_card(
        provider_id="opencode-zen",
        model_id="deepseek-v4-flash-free",
        contract_type="third_party_metered",
        quota_state={"status": "unknown", "source": "provider_docs"},
    )

    assert worker["contract_type"] == "third_party_metered"
    assert worker["dynamic_state"]["remaining_budget_state"] == "unknown"


def test_live_worker_merge_replaces_same_model_without_duplicates() -> None:
    module = _module()
    existing = {
        "version": 2,
        "workers": [
            {"worker_id": "old@openrouter", "provider_id": "openrouter", "model_id": "old"},
            {"worker_id": "keep@ollama-local", "provider_id": "ollama-local", "model_id": "keep"},
        ],
    }
    replacement = module.build_live_worker_card(
        provider_id="openrouter",
        model_id="old",
        contract_type="subscription_usage",
        quota_state={"status": "ok"},
    )

    merged = module.merge_live_workers(existing, [replacement])
    rows = {row["worker_id"]: row for row in merged["workers"]}

    assert len(merged["workers"]) == 2
    assert rows["old@openrouter"]["dynamic_state"]["refresh_status"] == "ok"
    assert rows["keep@ollama-local"]["model_id"] == "keep"
