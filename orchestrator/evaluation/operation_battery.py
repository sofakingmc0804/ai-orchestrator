from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.validators.operation_validators import VALIDATORS
from orchestrator.governance.job_classifier import JOB_CLASS_PATTERNS


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "benchmarks" / "manifest.json"
DEFAULT_PACK_PATH = REPO_ROOT / "benchmarks" / "fixtures" / "operations" / "first_pack.json"
DETERMINISTIC_REFEREE = "deterministic_ground_truth"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_index(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(task.get("task_id")): task
        for task in pack.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    }


def _operation_domain_ids(manifest: dict[str, Any]) -> set[str]:
    return {
        str(domain.get("domain_id"))
        for domain in manifest.get("operation_domains", [])
        if isinstance(domain, dict) and domain.get("domain_id")
    }


def _task_has_expected_answer(task: dict[str, Any] | None) -> bool:
    expected = (task or {}).get("expected")
    return isinstance(expected, dict) and bool(expected)


def validate_operation_battery(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    pack_path: Path = DEFAULT_PACK_PATH,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    pack = _read_json(pack_path)
    job_map = manifest.get("job_class_operation_map")
    job_map = job_map if isinstance(job_map, dict) else {}
    tasks = _task_index(pack)
    domains = _operation_domain_ids(manifest)

    covered: list[str] = []
    missing_job_classes: list[str] = []
    missing_task_ids: list[str] = []
    unknown_domains: list[str] = []
    unknown_validators: list[str] = []
    tasks_without_expected_answers: list[str] = []
    tasks_without_deterministic_referee: list[str] = []
    coverage: dict[str, Any] = {}

    for job_class in sorted(JOB_CLASS_PATTERNS):
        entry = job_map.get(job_class)
        if not isinstance(entry, dict):
            missing_job_classes.append(job_class)
            continue

        domain_id = str(entry.get("domain_id") or "")
        task_ids = [str(task_id) for task_id in entry.get("task_ids", []) if task_id]
        referee_type = str(entry.get("referee_type") or "")
        entry_errors = False

        if not domain_id or domain_id not in domains:
            unknown_domains.append(f"{job_class}:{domain_id or 'missing'}")
            entry_errors = True
        if not task_ids:
            missing_job_classes.append(job_class)
            entry_errors = True
        if referee_type != DETERMINISTIC_REFEREE:
            tasks_without_deterministic_referee.extend(task_ids or [job_class])
            entry_errors = True

        for task_id in task_ids:
            task = tasks.get(task_id)
            if task is None:
                missing_task_ids.append(task_id)
                entry_errors = True
                continue
            validator = str(task.get("validator") or "")
            if validator not in VALIDATORS:
                unknown_validators.append(f"{task_id}:{validator or 'missing'}")
                entry_errors = True
            if not _task_has_expected_answer(task):
                tasks_without_expected_answers.append(task_id)
                entry_errors = True

        coverage[job_class] = {
            "domain_id": domain_id,
            "task_ids": task_ids,
            "referee_type": referee_type,
        }
        if not entry_errors:
            covered.append(job_class)

    return {
        "covered_job_classes": covered,
        "missing_job_classes": sorted(set(missing_job_classes)),
        "missing_task_ids": sorted(set(missing_task_ids)),
        "unknown_domains": sorted(set(unknown_domains)),
        "unknown_validators": sorted(set(unknown_validators)),
        "tasks_without_expected_answers": sorted(set(tasks_without_expected_answers)),
        "tasks_without_deterministic_referee": sorted(set(tasks_without_deterministic_referee)),
        "coverage": coverage,
        "manifest_path": str(manifest_path),
        "pack_path": str(pack_path),
    }
