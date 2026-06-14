from __future__ import annotations

import pytest

from orchestrator.config import Settings
from orchestrator.evaluation.blind_panel import build_blind_judge_panel, score_blind_consensus
from orchestrator.evaluation.operation_battery import validate_operation_battery
from orchestrator.evaluation.tournament import run_deterministic_tournament
from orchestrator.governance.job_classifier import JOB_CLASS_PATTERNS
from orchestrator.scheduler.tasks import trigger_scheduler_task
from orchestrator.state.store import StateStore


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


@pytest.mark.asyncio
async def test_deterministic_tournament_persists_relative_live_scores(tmp_path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    store = StateStore(settings)
    await store.initialize()
    task = {
        "task_id": "BENCH-TEST-TOURNAMENT",
        "domain_id": "code_repair",
        "validator": "required_fields_and_terms",
        "expected": {
            "must_contain": {
                "diagnosis": ["None"],
                "tests": ["pytest"],
            }
        },
    }
    workers = [
        {"worker_id": "strong@local", "provider_id": "local", "model_family": "strong"},
        {"worker_id": "weak@local", "provider_id": "local", "model_family": "weak"},
        {"worker_id": "middle@local", "provider_id": "local", "model_family": "middle"},
    ]
    outputs = {
        "strong@local": '{"diagnosis":"None crashes total","tests":"pytest tests/test_total.py"}',
        "middle@local": '{"diagnosis":"None crashes total","tests":"not run"}',
        "weak@local": '{"diagnosis":"empty","tests":"not run"}',
    }
    seen_task_ids: list[str] = []

    async def output_runner(worker: dict, operation_task: dict) -> str:
        seen_task_ids.append(operation_task["task_id"])
        return outputs[worker["worker_id"]]

    receipt = await run_deterministic_tournament(
        store,
        task,
        workers,
        output_runner,
        operation_domain="repo_coding",
        run_id="run_test",
    )

    assert seen_task_ids == ["BENCH-TEST-TOURNAMENT"] * 3
    assert receipt["proof_kind"] == "live"
    assert receipt["ranking"][0]["worker_id"] == "strong@local"
    assert receipt["ranking"][0]["relative_score"] == 1.0
    assert receipt["ranking"][-1]["worker_id"] == "weak@local"
    assert receipt["ranking"][-1]["relative_score"] == 0.0

    rows = await store.list_operation_quality_scores()
    by_worker = {row["worker_id"]: row for row in rows}
    assert by_worker["strong@local"]["composite_score"] == 1.0
    assert by_worker["weak@local"]["composite_score"] == 0.0
    live_scores = await store.load_live_operation_quality_scores()
    assert live_scores["strong@local"]["repo_coding"]["composite_score"] == 1.0


@pytest.mark.asyncio
async def test_scheduler_operation_tournament_task_persists_scores(tmp_path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    store = StateStore(settings)
    await store.initialize()
    task_id = await store.upsert_scheduler_task(
        name="Operation tournament",
        task_type="operation_tournament",
        target_ref="BENCH-SCHED-TOURNAMENT",
        payload={
            "operation_domain": "repo_coding",
            "operation_task": {
                "task_id": "BENCH-SCHED-TOURNAMENT",
                "domain_id": "code_repair",
                "validator": "required_fields_and_terms",
                "expected": {"must_contain": {"diagnosis": ["None"], "tests": ["pytest"]}},
            },
            "workers": [
                {"worker_id": "strong@local", "provider_id": "local", "model_family": "strong"},
                {"worker_id": "weak@local", "provider_id": "local", "model_family": "weak"},
            ],
            "worker_outputs": {
                "strong@local": '{"diagnosis":"None crash","tests":"pytest"}',
                "weak@local": '{"diagnosis":"unknown","tests":"none"}',
            },
        },
        enabled=True,
    )

    result = await trigger_scheduler_task(settings, task_id, store)

    assert result["state"] == "completed"
    assert result["tournament"]["proof_kind"] == "live"
    assert result["tournament"]["ranking"][0]["worker_id"] == "strong@local"
    live_scores = await store.load_live_operation_quality_scores()
    assert live_scores["strong@local"]["repo_coding"]["composite_score"] == 1.0
