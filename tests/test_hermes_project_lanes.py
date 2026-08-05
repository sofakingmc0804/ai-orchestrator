from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "hermes" / "project_lanes.py"


def _module():
    assert MODULE_PATH.is_file(), "project lane assignment is not registered"
    spec = importlib.util.spec_from_file_location("project_lanes_under_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_lane_assignment_is_stable_and_receipt_ready() -> None:
    module = _module()
    candidates = ["ollama-cloud", "openrouter", "nous"]

    first = module.assign_project_lane("project-alpha", candidates)
    second = module.assign_project_lane("project-alpha", candidates)

    assert first == second
    assert first["assigned_provider"] in candidates
    assert first["project_id"] == "project-alpha"
    assert isinstance(first["assignment_key"], str) and first["assignment_key"]


def test_project_lane_selection_prefers_assigned_provider_but_falls_back_safely() -> None:
    module = _module()
    ladder = [
        {"provider_id": "ollama-local", "worker_id": "local@ollama-local"},
        {"provider_id": "openrouter", "worker_id": "free@openrouter"},
        {"provider_id": "nous", "worker_id": "nous@nous"},
    ]

    selected, receipt = module.select_project_worker("project-alpha", ladder)

    assert selected in ladder
    assert receipt["project_id"] == "project-alpha"
    assert receipt["assigned_provider"] in {"ollama-local", "openrouter", "nous"}
    assert receipt["selection_reason"] in {"assigned_provider", "fallback_to_ranked_ladder"}


def test_project_lane_selection_does_not_assign_blocked_metered_worker() -> None:
    module = _module()
    ladder = [
        {
            "provider_id": "opencode-zen",
            "worker_id": "paid@opencode-zen",
            "contract_type": "third_party_metered",
        },
        {
            "provider_id": "openrouter",
            "worker_id": "free@openrouter",
            "model_id": "inclusionai/ling-3.0-flash:free",
            "contract_type": "subscription_usage",
        },
    ]

    selected, receipt = module.select_project_worker("project-paid-guard", ladder)

    assert selected is ladder[1]
    assert receipt["assigned_provider"] == "openrouter"
    assert receipt["selection_reason"] == "assigned_provider"
