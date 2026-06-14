from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from benchmarks.validators.operation_validators import validate_task
from orchestrator.state.store import StateStore


OutputRunner = Callable[[dict[str, Any], dict[str, Any]], str | Awaitable[str]]


def _relative_score(raw_score: float, low: float, high: float) -> float:
    if high == low:
        return 1.0
    return round((raw_score - low) / (high - low), 4)


async def _run_worker_output(
    output_runner: OutputRunner,
    worker: dict[str, Any],
    task: dict[str, Any],
) -> str:
    result = output_runner(worker, task)
    if inspect.isawaitable(result):
        return str(await result)
    return str(result)


async def run_deterministic_tournament(
    store: StateStore,
    task: dict[str, Any],
    workers: list[dict[str, Any]],
    output_runner: OutputRunner,
    *,
    operation_domain: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "operation")
    domain = str(operation_domain or task.get("domain_id") or "unknown")
    run_id = run_id or f"tournament_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    evaluated: list[dict[str, Any]] = []

    for worker in workers:
        raw_output = await _run_worker_output(output_runner, worker, task)
        validation = validate_task(task, raw_output)
        scores = validation.get("scores") if isinstance(validation.get("scores"), dict) else {}
        raw_score = float(scores.get("composite") or 0.0)
        evaluated.append(
            {
                "worker_id": str(worker.get("worker_id") or ""),
                "raw_output": raw_output,
                "raw_validator_score": raw_score,
                "validation": validation,
            }
        )

    raw_scores = [item["raw_validator_score"] for item in evaluated]
    low = min(raw_scores) if raw_scores else 0.0
    high = max(raw_scores) if raw_scores else 0.0
    created_at = datetime.now(UTC).isoformat()
    persisted: list[dict[str, Any]] = []

    for item in evaluated:
        relative = _relative_score(float(item["raw_validator_score"]), low, high)
        item["relative_score"] = relative
        score = {
            "id": f"oqs_{run_id}_{task_id}_{item['worker_id']}".replace(" ", "_").replace(":", "_").replace("@", "_"),
            "dispatch_id": f"{run_id}:{task_id}:{item['worker_id']}",
            "worker_id": item["worker_id"],
            "operation_domain": domain,
            "validator_name": str(item["validation"].get("validator") or task.get("validator") or "unknown"),
            "composite_score": relative,
            "dimensional_scores": {
                **(item["validation"].get("scores") if isinstance(item["validation"].get("scores"), dict) else {}),
                "relative": relative,
            },
            "task_id": task_id,
            "proof_kind": "live",
            "validation": {
                "proof_kind": "live",
                "run_id": run_id,
                "task_id": task_id,
                "operation_domain": domain,
                "raw_output": item["raw_output"],
                "raw_validator_score": item["raw_validator_score"],
                "relative_score": relative,
                "relative_method": "min_max_within_identical_task_population",
                "validation": item["validation"],
            },
            "created_at": created_at,
        }
        await store.record_operation_quality_score(score)
        persisted.append(score)

    ranking = sorted(evaluated, key=lambda item: (-float(item["relative_score"]), item["worker_id"]))
    return {
        "state": "produced",
        "proof_kind": "live",
        "run_id": run_id,
        "task_id": task_id,
        "operation_domain": domain,
        "workers_evaluated": len(workers),
        "ranking": [
            {
                "worker_id": item["worker_id"],
                "raw_validator_score": round(float(item["raw_validator_score"]), 4),
                "relative_score": item["relative_score"],
            }
            for item in ranking
        ],
        "persisted_scores": [
            {
                "worker_id": score["worker_id"],
                "operation_domain": score["operation_domain"],
                "composite_score": score["composite_score"],
                "proof_kind": score["proof_kind"],
            }
            for score in persisted
        ],
    }
