"""Worker-aware routing engine.

Extends the original routing engine to use worker cards from the DB.
Selects the best worker for a job based on:
1. Capability match (worker must have required capabilities)
2. Job class fit (worker's best_jobs vs job_class)
3. Contract type (local → subscription → metered)
4. Budget state (quota remaining, reserve protection)
5. Benchmark scores (if available)

Author: Hermes Agent
Date: 2026-06-10
Phase: 2 (Routing Integration)
"""

from __future__ import annotations

import json
from typing import Any

from orchestrator.models import BillingClass, ConsequenceTier, Intent, RoutingDecision
from orchestrator.routing.engine import (
    BILLING_ORDER,
    FORBIDDEN,
    PROVIDER_BY_ADAPTER,
    RESERVE_BY_TIER,
    TIER_ORDER,
    load_score_contract,
    _overlay_benchmark_scores,
    _preference_rank,
    _project_policy_rejection,
    _quota_rejection_reason,
)


CONTRACT_BLOCKLIST = {"metered_extra_cost", "third_party_metered", "unknown_cost"}


def _json_list(row: dict[str, Any], key: str) -> list[Any]:
    value = row.get(key)
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _job_required_capabilities(job_class_spec: dict[str, Any] | None) -> set[str]:
    if not job_class_spec:
        return set()
    value = job_class_spec.get("required_capabilities") or job_class_spec.get("required_capabilities_json")
    if isinstance(value, list):
        return {str(item) for item in value}
    if not value:
        return set()
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return set()
    return {str(item) for item in parsed} if isinstance(parsed, list) else set()


def _worker_capability_pool(worker: dict[str, Any]) -> set[str]:
    stats = _json_dict(worker, "stats_json")
    pool = set()
    pool.update(str(item) for item in _json_list(worker, "capabilities_json"))
    pool.update(str(item) for item in _json_list(worker, "tools_json"))
    pool.update(str(item) for item in _json_list(worker, "modalities_json"))
    pool.update(str(item) for item in _json_list(worker, "best_jobs_json"))
    pool.update(str(key) for key, value in stats.items() if int(value or 0) >= 7)
    return pool


def _worker_matches_job_class(worker: dict[str, Any], job_class: str) -> tuple[bool, float]:
    """Check if worker is suitable for job class.

    Returns:
        (matches, score) — matches is True if worker can do this job,
        score is 0.0-1.0 based on best_jobs/avoid_jobs alignment
    """
    best_jobs = _json_list(worker, "best_jobs_json")
    avoid_jobs = _json_list(worker, "avoid_jobs_json")

    # Strong negative signal
    if job_class in avoid_jobs:
        return False, 0.0

    # Strong positive signal
    if job_class in best_jobs:
        return True, 1.0

    # Neutral: worker can do it but not specialized
    return True, 0.5


def _worker_capability_match(worker: dict[str, Any], required_caps: set[str]) -> float:
    """Calculate capability match score for worker.

    Returns:
        0.0-1.0 — fraction of required capabilities worker has
    """
    worker_caps = _worker_capability_pool(worker)
    if not required_caps:
        return 1.0
    return len(required_caps & worker_caps) / len(required_caps)


def route_with_workers(
    intent: Intent,
    workers: list[dict[str, Any]],
    job_class: str,
    quota_state: dict[str, dict[str, Any]] | None = None,
    project_policy: dict[str, Any] | None = None,
    score_contract: dict[str, Any] | None = None,
    budget_probes: list[dict[str, Any]] | None = None,
    job_class_spec: dict[str, Any] | None = None,
) -> RoutingDecision:
    """Route intent to best worker using worker cards.

    Args:
        intent: The intent to route
        workers: List of worker cards from DB
        job_class: The job class (e.g., 'repo_coding')
        quota_state: Budget/quota state per provider
        job_class: The job class (e.g., 'repo_coding')
        quota_state: Budget/quota state per provider
        project_policy: Project-specific routing policy
        score_contract: Benchmark score contract
        budget_probes: Fresh budget probe results from DB

    Returns:
        RoutingDecision with chosen worker_id and reasoning
    """
    quota_state = quota_state or {}
    project_policy = project_policy or {}

    # Build budget probe lookup
    budget_lookup: dict[str, dict[str, Any]] = {}
    if budget_probes:
        for probe in budget_probes:
            budget_lookup[probe['provider_id']] = {
                'remaining': probe['remaining'] or 0,
                'limit': probe['limit'] or 0,
                'ok': probe['ok'],
                'probed_at': probe['probed_at'],
            }

    required_caps = _job_required_capabilities(job_class_spec)

    considered: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for worker in workers:
        worker_id = worker["worker_id"]
        row = dict(worker)
        row["billing_class"] = worker.get("contract_type")

        # Check job class fit
        matches_job, job_score = _worker_matches_job_class(worker, job_class)
        if not matches_job:
            row["rejected_reason"] = f"worker avoids {job_class} jobs"
            rejected.append(row)
            continue

        # Check capability match
        cap_score = _worker_capability_match(worker, required_caps)
        if cap_score < 1.0:
            row["rejected_reason"] = f"insufficient capability match ({cap_score:.1%})"
            rejected.append(row)
            continue

        # Check billing class
        if worker.get("contract_type") in CONTRACT_BLOCKLIST:
            row["rejected_reason"] = "forbidden contract type"
            rejected.append(row)
            continue

        # Check project policy (map worker surface to adapter)
        surface_to_adapter = {
            "claude-desktop-mcp": "claude-desktop-mcp",
            "claude-max": "claude-code-cli",
            "claude-code-cli": "claude-code-cli",
            "codex-chatgpt": "codex-desktop",
            "codex-cli": "codex-cli",
            "copilot-gh": "copilot-gh",
            "github-copilot": "copilot-gh",
            "copilot-vscode": "copilot-vscode",
            "gemini-cli": "gemini-cli",
            "nous": "hermes-agent",
            "hermes-nous": "hermes-agent",
            "ollama-cloud": "ollama-cloud",
            "ollama-local": "ollama-http",
            "ollama-cli": "ollama-cli",
            "ollama-http": "ollama-http",
            "lm-studio": "lm-studio",
            "hermes-agent": "hermes-agent",
        }
        adapter_name = surface_to_adapter.get(worker.get("surface", ""))
        if not adapter_name:
            row["rejected_reason"] = f"no dispatch adapter mapped for surface {worker.get('surface')}"
            rejected.append(row)
            continue
        row["adapter_name"] = adapter_name
        row["provider"] = worker.get("provider_id") or worker.get("surface")
        reason = _project_policy_rejection(row, project_policy)
        if reason:
            row["rejected_reason"] = reason
            rejected.append(row)
            continue

        # Check quota (for subscription workers)
        if worker.get("contract_type") == "subscription_quota":
            provider = PROVIDER_BY_ADAPTER.get(adapter_name or "")
            if provider:
                reason = _quota_rejection_reason(intent, row, quota_state)
                if reason:
                    row["rejected_reason"] = reason
                    rejected.append(row)
                    continue

        # Calculate composite score
        # Higher is better: job_score + cap_score + benchmark bonus
        benchmark_score = float(worker.get("benchmark_score") or 0.0)
        composite = (job_score * 0.4) + (cap_score * 0.4) + (benchmark_score * 0.2)

        row["composite_score"] = composite
        row["job_class_fit"] = job_score
        row["capability_match"] = cap_score
        considered.append(row)

    # Sort by composite score, then contract type (cheapest first)
    considered.sort(
        key=lambda w: (
            -w.get("composite_score", 0),
            BILLING_ORDER.get(w.get("contract_type"), 99),
        )
    )

    # Pick winner
    chosen = considered[0].get("adapter_name") if considered else None
    chosen_worker = considered[0] if considered else None

    if chosen:
        model_note = f" (model: {chosen_worker.get('model_id')})" if chosen_worker.get("model_id") else ""
        reasoning = (
            f"Selected {chosen}{model_note} for job class {job_class}: "
            f"best composite score ({chosen_worker.get('composite_score', 0):.2f}) "
            f"from job fit ({chosen_worker.get('job_class_fit', 0):.2f}), "
            f"capability match ({chosen_worker.get('capability_match', 0):.2f}), "
            f"and benchmark score ({chosen_worker.get('benchmark_score', 0):.2f})."
        )
    else:
        reasoning = f"No suitable worker found for job class {job_class}; creating repair queue entry."

    return RoutingDecision(
        intent_id=intent.id,
        chosen_adapter=chosen,  # Will be worker_id in worker-aware flow
        candidates_considered=considered,
        candidates_rejected=rejected,
        reasoning=reasoning,
    )


def route_intent_worker_aware(
    intent: Intent,
    workers: list[dict[str, Any]],
    job_class: str,
    quota_state: dict[str, dict[str, Any]] | None = None,
    project_policy: dict[str, Any] | None = None,
    job_class_spec: dict[str, Any] | None = None,
) -> RoutingDecision:
    """Route intent using worker-aware routing.

    This is the main entry point for Phase 2 routing.
    Falls back to original capability-based routing if no workers available.

    Args:
        intent: Intent to route
        workers: Worker cards from DB
        job_class: Classified job class
        quota_state: Budget state
        project_policy: Project policy

    Returns:
        RoutingDecision with chosen worker
    """
    if not workers:
        # Fallback to original routing (capability-based)
        from orchestrator.routing.engine import route_intent
        return route_intent(intent, [], quota_state, project_policy)

    return route_with_workers(intent, workers, job_class, quota_state, project_policy, job_class_spec=job_class_spec)
