from __future__ import annotations

import pytest

from orchestrator.evaluation.blind_panel import build_blind_judge_panel, score_blind_consensus
from orchestrator.evaluation.operation_battery import validate_operation_battery
from orchestrator.governance.job_classifier import JOB_CLASS_PATTERNS


def test_operation_battery_covers_every_job_class_with_ground_truth() -> None:
    report = validate_operation_battery()

    assert report["covered_job_classes"] == sorted(JOB_CLASS_PATTERNS)
    assert report["missing_job_classes"] == []
    assert report["missing_task_ids"] == []
    assert report["unknown_domains"] == []
    assert report["unknown_validators"] == []
    assert report["tasks_without_expected_answers"] == []
    assert report["tasks_without_deterministic_referee"] == []


def test_blind_panel_anonymizes_outputs_and_excludes_self_family() -> None:
    candidates = [
        {"worker_id": "alpha@provider-a", "provider_id": "provider-a", "model_family": "alpha", "output": "A"},
        {"worker_id": "beta@provider-b", "provider_id": "provider-b", "model_family": "beta", "output": "B"},
    ]
    judges = [
        {"judge_id": "judge-a", "provider_id": "provider-a", "model_family": "alpha", "contract_type": "subscription_unlimited"},
        {"judge_id": "judge-b", "provider_id": "provider-b", "model_family": "beta", "contract_type": "subscription_unlimited"},
        {"judge_id": "judge-c", "provider_id": "provider-c", "model_family": "gamma", "contract_type": "local_resource"},
        {"judge_id": "judge-d", "provider_id": "provider-d", "model_family": "delta", "contract_type": "subscription_usage"},
    ]

    panel = build_blind_judge_panel(candidates, judges, seed="fixed")

    assert len(panel["blind_outputs"]) == 2
    for output in panel["blind_outputs"]:
        assert set(output) == {"anonymous_id", "output"}
        assert output["anonymous_id"].startswith("anon_")
        assert "provider" not in output["output"].lower()

    for item in panel["audit_assignments"]:
        assert len(item["judge_ids"]) >= 3
        assert item["candidate_provider_id"] not in item["judge_provider_ids"]
        assert item["candidate_model_family"] not in item["judge_model_families"]


def test_blind_panel_requires_three_diverse_flat_rate_judges() -> None:
    candidates = [{"worker_id": "alpha@provider-a", "provider_id": "provider-a", "model_family": "alpha", "output": "A"}]
    judges = [
        {"judge_id": "judge-a", "provider_id": "provider-a", "model_family": "alpha", "contract_type": "subscription_unlimited"},
        {"judge_id": "judge-b", "provider_id": "provider-b", "model_family": "beta", "contract_type": "metered_extra_cost"},
        {"judge_id": "judge-c", "provider_id": "provider-c", "model_family": "gamma", "contract_type": "local_resource"},
    ]

    with pytest.raises(ValueError, match="at least 3 eligible judges"):
        build_blind_judge_panel(candidates, judges, seed="fixed")


def test_blind_panel_consensus_uses_median_and_variance_gate() -> None:
    candidates = [
        {"worker_id": "alpha@provider-a", "provider_id": "provider-a", "model_family": "alpha", "output": "A"},
        {"worker_id": "beta@provider-b", "provider_id": "provider-b", "model_family": "beta", "output": "B"},
    ]
    judges = [
        {"judge_id": "judge-a", "provider_id": "provider-a", "model_family": "alpha", "contract_type": "subscription_unlimited"},
        {"judge_id": "judge-b", "provider_id": "provider-b", "model_family": "beta", "contract_type": "subscription_unlimited"},
        {"judge_id": "judge-c", "provider_id": "provider-c", "model_family": "gamma", "contract_type": "local_resource"},
        {"judge_id": "judge-d", "provider_id": "provider-d", "model_family": "delta", "contract_type": "subscription_usage"},
    ]
    panel = build_blind_judge_panel(candidates, judges, seed="fixed")
    by_worker = {item["candidate_worker_id"]: item["anonymous_id"] for item in panel["audit_assignments"]}

    result = score_blind_consensus(
        panel,
        {
            by_worker["alpha@provider-a"]: [
                {"judge_id": "judge-b", "score": 0.9},
                {"judge_id": "judge-c", "score": 0.8},
                {"judge_id": "judge-d", "score": 1.0},
            ],
            by_worker["beta@provider-b"]: [
                {"judge_id": "judge-a", "score": 0.1},
                {"judge_id": "judge-c", "score": 0.9},
                {"judge_id": "judge-d", "score": 0.5},
            ],
        },
        variance_threshold=0.05,
    )

    alpha = result["by_worker"]["alpha@provider-a"]
    beta = result["by_worker"]["beta@provider-b"]
    assert alpha["consensus_score"] == 0.9
    assert alpha["status"] == "accepted"
    assert alpha["relative_score"] == 1.0
    assert beta["consensus_score"] == 0.5
    assert beta["status"] == "needs_rejudge"
    assert beta["relative_score"] is None
