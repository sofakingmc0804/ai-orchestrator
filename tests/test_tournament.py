from __future__ import annotations

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
