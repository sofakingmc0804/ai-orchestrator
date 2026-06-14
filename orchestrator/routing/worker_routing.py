"""Worker-aware routing engine.

Extends the original routing engine to use worker cards from the DB.
Selects the best worker for a job based on:
1. Capability match (worker must have required capabilities)
2. Job class fit (worker's best_jobs vs job_class)
3. Contract type (local → subscription → metered)
4. Budget state (quota remaining, reserve protection)
5. Benchmark scores (if available)

Date: 2026-06-10
Phase: 2 (Routing Integration)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from orchestrator.models import BillingClass, ConsequenceTier, Intent, RoutingDecision
from orchestrator.provider_aliases import lookup_by_provider, provider_keys
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
from orchestrator.surface_adapters import dispatch_adapter_for_worker


CONTRACT_BLOCKLIST = {"metered_extra_cost", "third_party_metered", "unknown_cost"}
CODING_MODEL_HINTS = ("coder", "code", "codex", "deepseek", "qwen")
SUBSCRIPTION_SNAPSHOT_MAX_AGE = timedelta(hours=24)
HEALTH_SCORES = {
    "healthy": 1.0,
    "degraded": 0.35,
    "unknown": 0.5,
    "stopped": 0.0,
}


def _as_dict(row: Any) -> dict[str, Any]:
    return row if isinstance(row, dict) else dict(row)


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


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _snapshot_is_stale(snapshot: dict[str, Any]) -> bool:
    checked_at = _parse_time(snapshot.get("checked_at") or snapshot.get("probed_at"))
    if checked_at is None:
        return False
    return datetime.now(UTC) - checked_at > SUBSCRIPTION_SNAPSHOT_MAX_AGE


def _latest_subscription_snapshots(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in rows or []:
        row = _as_dict(item)
        service_id = str(row.get("service_id") or "")
        if not service_id:
            continue
        current = latest.get(service_id)
        if current is None:
            latest[service_id] = row
            continue
        row_time = _parse_time(row.get("checked_at")) or datetime.min.replace(tzinfo=UTC)
        current_time = _parse_time(current.get("checked_at")) or datetime.min.replace(tzinfo=UTC)
        if row_time >= current_time:
            latest[service_id] = row
    return list(latest.values())


def _budget_row_from_probe(probe: dict[str, Any], source: str = "budget_probes") -> dict[str, Any]:
    provider_id = str(probe.get("provider_id") or "")
    return {
        "provider_id": provider_id,
        "remaining": int(probe.get("remaining") or 0),
        "limit": int(probe.get("limit") or probe.get("units_limit") or 0),
        "units_limit": int(probe.get("limit") or probe.get("units_limit") or 0),
        "ok": bool(probe.get("ok")) if probe.get("ok") is not None else None,
        "probed_at": probe.get("probed_at"),
        "error": probe.get("error"),
        "source": source,
    }


def _budget_row_from_subscription_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    service_id = str(snapshot.get("service_id") or "")
    stale = _snapshot_is_stale(snapshot)
    ok = bool(snapshot.get("ok")) and not stale
    error = snapshot.get("error")
    if stale:
        error = "subscription usage snapshot is stale"
    return {
        "provider_id": service_id,
        "service_id": service_id,
        "remaining": int(snapshot.get("tokens_remaining") or 0),
        "limit": int(snapshot.get("tokens_limit") or 0),
        "units_limit": int(snapshot.get("tokens_limit") or 0),
        "ok": ok,
        "probed_at": snapshot.get("checked_at"),
        "checked_at": snapshot.get("checked_at"),
        "error": error,
        "source": "subscription_usage_snapshots" if ok else "subscription_usage_snapshots:unusable",
        "status": snapshot.get("status"),
    }


def _budget_lookup_from_sources(
    subscription_usage_snapshots: list[dict[str, Any]] | None,
    budget_probes: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for probe in budget_probes or []:
        row = _budget_row_from_probe(_as_dict(probe))
        provider_id = str(row.get("provider_id") or "")
        if not provider_id:
            continue
        for key in provider_keys(provider_id):
            lookup.setdefault(key, row)

    unusable_snapshots: dict[str, dict[str, Any]] = {}
    live_snapshots: dict[str, dict[str, Any]] = {}
    for snapshot in _latest_subscription_snapshots(subscription_usage_snapshots):
        row = _budget_row_from_subscription_snapshot(snapshot)
        service_id = str(row.get("service_id") or "")
        if not service_id:
            continue
        target = live_snapshots if row.get("ok") else unusable_snapshots
        for key in provider_keys(service_id):
            target[key] = row

    for key, row in unusable_snapshots.items():
        if key in lookup:
            fallback = dict(lookup[key])
            fallback["source"] = "budget_probes:fallback"
            fallback["fallback_reason"] = row.get("error") or row.get("status") or "subscription snapshot unusable"
            lookup[key] = fallback
        else:
            lookup[key] = row
    lookup.update(live_snapshots)
    return lookup


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


def _job_preferred_stats(job_class_spec: dict[str, Any] | None) -> dict[str, int]:
    if not job_class_spec:
        return {}
    value = job_class_spec.get("preferred_stats") or job_class_spec.get("preferred_stats_json")
    if isinstance(value, dict):
        return {str(key): int(val) for key, val in value.items()}
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return {str(key): int(val) for key, val in parsed.items()} if isinstance(parsed, dict) else {}


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


def _budget_score(worker: dict[str, Any], budget_lookup: dict[str, dict[str, Any]]) -> float:
    contract = str(worker.get("contract_type") or "")
    if contract == BillingClass.LOCAL_RESOURCE.value:
        return 1.0
    if contract == BillingClass.SUBSCRIPTION_UNLIMITED.value:
        return 0.9
    provider = str(worker.get("provider_id") or worker.get("surface") or "")
    probe = lookup_by_provider(provider, budget_lookup)
    if not probe:
        return 0.45 if contract in {BillingClass.SUBSCRIPTION_QUOTA.value, BillingClass.SUBSCRIPTION_USAGE.value} else 0.0
    if not probe.get("ok"):
        return 0.15
    limit = float(probe.get("limit") or 0)
    remaining = float(probe.get("remaining") or 0)
    if limit <= 0:
        return 0.75
    return max(0.0, min(1.0, remaining / limit))


def _reserve_rejection_reason(
    intent: Intent,
    worker: dict[str, Any],
    adapter_name: str,
    quota_state: dict[str, dict[str, Any]],
    budget_lookup: dict[str, dict[str, Any]],
) -> str | None:
    contract = str(worker.get("contract_type") or "")
    if contract not in {BillingClass.SUBSCRIPTION_QUOTA.value, BillingClass.SUBSCRIPTION_USAGE.value}:
        return None

    provider = str(worker.get("provider_id") or worker.get("surface") or PROVIDER_BY_ADAPTER.get(adapter_name) or adapter_name)
    quota = lookup_by_provider(provider, quota_state)
    probe = lookup_by_provider(provider, budget_lookup)
    source = quota or probe
    if not source:
        return None
    if source.get("ok") is not None and not source.get("ok"):
        return f"quota probe failed for {provider}: {source.get('error') or 'probe unavailable'}"

    limit = int(source.get("units_limit") or source.get("limit") or 0)
    remaining = int(source.get("remaining") or 0)
    if limit <= 0:
        return None

    priority = str(intent.parsed_payload.get("priority") or "").lower()
    reserve = 0.0 if priority == "urgent" else RESERVE_BY_TIER.get(intent.consequence_tier.value, 0.20)
    reserve_units = limit * reserve
    if remaining <= reserve_units:
        return f"quota reserve protected for {provider}: remaining {remaining}/{limit} at reserve {reserve:.0%}"
    return None


def _contract_pressure_score(worker: dict[str, Any]) -> float:
    contract = str(worker.get("contract_type") or "")
    stats = _json_dict(worker, "stats_json")
    cost_pressure = float(stats.get("cost_pressure") or 0) / 10
    base_by_contract = {
        BillingClass.LOCAL_RESOURCE.value: 1.0,
        BillingClass.SUBSCRIPTION_UNLIMITED.value: 0.72,
        BillingClass.SUBSCRIPTION_USAGE.value: 0.64,
        BillingClass.SUBSCRIPTION_QUOTA.value: 0.55,
    }
    base = base_by_contract.get(contract, 0.0)
    if cost_pressure:
        base = (base * 0.75) + (cost_pressure * 0.25)
    return max(0.0, min(1.0, base))


def _health_state_for_adapter(adapter_name: str, service_health: dict[str, str]) -> str:
    if not service_health:
        return "healthy"
    return str(service_health.get(adapter_name) or "unknown").lower()


def _health_score(health_state: str) -> float:
    return HEALTH_SCORES.get(health_state, 0.5)


def _marginal_cost_class(worker: dict[str, Any]) -> str:
    contract = str(worker.get("contract_type") or "")
    surface = str(worker.get("surface") or "")
    if surface == "ollama-cloud" and contract == BillingClass.SUBSCRIPTION_USAGE.value:
        return "flat_rate"
    if contract in {BillingClass.LOCAL_RESOURCE.value, BillingClass.SUBSCRIPTION_UNLIMITED.value}:
        return "flat_rate"
    if contract in {BillingClass.SUBSCRIPTION_QUOTA.value, BillingClass.SUBSCRIPTION_USAGE.value}:
        return "quota_limited_flat_rate"
    return "metered_or_unknown"


def _marginal_cost_score(worker: dict[str, Any]) -> float:
    cost_class = _marginal_cost_class(worker)
    if cost_class == "flat_rate":
        return 1.0
    if cost_class == "quota_limited_flat_rate":
        return 0.6
    return 0.0


def _failover_floor(worker: dict[str, Any]) -> bool:
    return _marginal_cost_class(worker) == "flat_rate" and str(worker.get("surface") or "") == "ollama-cloud"


def _preferred_stat_score(worker: dict[str, Any], preferred_stats: dict[str, int]) -> float:
    stats = _json_dict(worker, "stats_json")
    if not preferred_stats:
        defaults = ["speed", "stability", "coding"]
        values = [float(stats.get(key) or 0) / 10 for key in defaults if key in stats]
        return sum(values) / len(values) if values else 0.5
    total_weight = sum(max(weight, 0) for weight in preferred_stats.values()) or 1
    weighted = 0.0
    for stat, weight in preferred_stats.items():
        weighted += (float(stats.get(stat) or 0) / 10) * max(weight, 0)
    return max(0.0, min(1.0, weighted / total_weight))


def _speed_score(worker: dict[str, Any]) -> float:
    stats = _json_dict(worker, "stats_json")
    base = float(stats.get("speed") or 5) / 10
    model = str(worker.get("model_id") or "").lower()
    if any(size in model for size in ("0.5b", "1b", "3b", "7b", "8b")):
        base += 0.08
    if any(size in model for size in ("20b", "30b", "70b", "120b")):
        base -= 0.18
    return max(0.0, min(1.0, base))


def _model_fit_score(worker: dict[str, Any], job_class: str) -> float:
    model = str(worker.get("model_id") or "").lower()
    if job_class in {"repo_coding", "simple_coding", "agentic_repair", "deep_debugging"}:
        return 1.0 if any(hint in model for hint in CODING_MODEL_HINTS) else 0.55
    if job_class in {"routing_triage", "bulk_extraction", "schema_validation"}:
        return 0.9 if any(hint in model for hint in ("mini", "flash", "0.5b", "3b")) else 0.65
    return 0.75


def _token_efficiency_score(worker: dict[str, Any], token_usage_summary: dict[str, dict[str, Any]]) -> float:
    provider = str(worker.get("provider_id") or worker.get("surface") or "")
    model = str(worker.get("model_id") or "")
    summary = token_usage_summary.get(f"{provider}|{model}") or token_usage_summary.get(f"{provider.replace('-', '_')}|{model}")
    if not summary:
        return 0.65
    avg = float(summary.get("avg_tokens_total") or 0)
    success_rate = float(summary.get("success_rate") or 0)
    if avg <= 0:
        efficiency = 0.65
    else:
        efficiency = max(0.0, min(1.0, 1.0 - (avg / 8192)))
    return max(0.0, min(1.0, (efficiency * 0.45) + (success_rate * 0.55)))


def _measured_quality_score(
    worker: dict[str, Any],
    job_class: str,
    operation_quality_scores: dict[str, dict[str, dict[str, Any]]],
) -> tuple[float, str]:
    worker_id = str(worker.get("worker_id") or "")
    worker_scores = operation_quality_scores.get(worker_id) or {}
    domain_score = worker_scores.get(job_class)
    if domain_score:
        return max(0.0, min(1.0, float(domain_score.get("composite_score") or 0.0))), f"operation_quality_scores:{job_class}"
    if worker_scores:
        values = [float(item.get("composite_score") or 0.0) for item in worker_scores.values()]
        if values:
            return max(0.0, min(1.0, sum(values) / len(values))), "operation_quality_scores:overall"
    return 0.5, "none"


def route_with_workers(
    intent: Intent,
    workers: list[dict[str, Any]],
    job_class: str,
    quota_state: dict[str, dict[str, Any]] | None = None,
    project_policy: dict[str, Any] | None = None,
    score_contract: dict[str, Any] | None = None,
    budget_probes: list[dict[str, Any]] | None = None,
    subscription_usage_snapshots: list[dict[str, Any]] | None = None,
    job_class_spec: dict[str, Any] | None = None,
    token_usage_summary: dict[str, dict[str, Any]] | None = None,
    operation_quality_scores: dict[str, dict[str, dict[str, Any]]] | None = None,
    service_health: dict[str, str] | None = None,
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
        budget_probes: Legacy budget probe fallback rows from DB
        subscription_usage_snapshots: Live subscription usage snapshots from DB

    Returns:
        RoutingDecision with chosen worker_id and reasoning
    """
    quota_state = quota_state or {}
    project_policy = project_policy or {}
    token_usage_summary = token_usage_summary or {}
    operation_quality_scores = operation_quality_scores or {}
    service_health = service_health or {}

    budget_lookup = _budget_lookup_from_sources(subscription_usage_snapshots, budget_probes)

    required_caps = _job_required_capabilities(job_class_spec)
    preferred_stats = _job_preferred_stats(job_class_spec)

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

        # Dispatch is a fact on the worker card or in the surface registry.
        adapter_name = dispatch_adapter_for_worker(worker)
        if not adapter_name:
            row["rejected_reason"] = f"no dispatch adapter mapped for surface {worker.get('surface')}"
            rejected.append(row)
            continue
        row["adapter_name"] = adapter_name
        row["provider"] = worker.get("provider_id") or worker.get("surface")
        health_state = _health_state_for_adapter(adapter_name, service_health)
        row["health_state"] = health_state
        row["health_score"] = _health_score(health_state)
        reason = _project_policy_rejection(row, project_policy)
        if reason:
            row["rejected_reason"] = reason
            rejected.append(row)
            continue

        reason = _reserve_rejection_reason(intent, worker, adapter_name, quota_state, budget_lookup)
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

        benchmark_score = float(worker.get("benchmark_score") or 0.0)
        stat_score = _preferred_stat_score(worker, preferred_stats)
        speed_score = _speed_score(worker)
        budget_fit = _budget_score(worker, budget_lookup)
        budget_source = lookup_by_provider(str(worker.get("provider_id") or worker.get("surface") or ""), budget_lookup)
        contract_score = _contract_pressure_score(worker)
        model_fit = _model_fit_score(worker, job_class)
        token_score = _token_efficiency_score(worker, token_usage_summary)
        measured_quality, quality_source = _measured_quality_score(worker, job_class, operation_quality_scores)
        marginal_cost = _marginal_cost_score(worker)
        composite = (
            (job_score * 0.12)
            + (cap_score * 0.12)
            + (stat_score * 0.10)
            + (speed_score * 0.07)
            + (budget_fit * 0.10)
            + (contract_score * 0.18)
            + (model_fit * 0.08)
            + (token_score * 0.06)
            + (measured_quality * 0.14)
            + (benchmark_score * 0.03)
        )
        composite = max(0.0, min(1.0, composite))

        row["composite_score"] = composite
        row["job_class_fit"] = job_score
        row["capability_match"] = cap_score
        row["preferred_stat_score"] = stat_score
        row["speed_score"] = speed_score
        row["budget_score"] = budget_fit
        row["budget_source"] = str((budget_source or {}).get("source") or "none")
        row["contract_pressure_score"] = contract_score
        row["model_fit_score"] = model_fit
        row["token_efficiency_score"] = token_score
        row["measured_quality_score"] = measured_quality
        row["quality_source"] = quality_source
        row["marginal_cost_score"] = marginal_cost
        row["marginal_cost_class"] = _marginal_cost_class(worker)
        row["failover_floor"] = _failover_floor(worker)
        considered.append(row)

    # Ordered ladder: health, live quota, measured quality, marginal cost, then fit.
    considered.sort(
        key=lambda w: (
            -float(w.get("health_score", 0)),
            -float(w.get("budget_score", 0)),
            -float(w.get("measured_quality_score", 0)),
            -float(w.get("marginal_cost_score", 0)),
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
            f"preferred stats ({chosen_worker.get('preferred_stat_score', 0):.2f}), "
            f"speed ({chosen_worker.get('speed_score', 0):.2f}), "
            f"budget ({chosen_worker.get('budget_score', 0):.2f}), "
            f"contract pressure ({chosen_worker.get('contract_pressure_score', 0):.2f}), "
            f"model fit ({chosen_worker.get('model_fit_score', 0):.2f}), "
            f"token efficiency ({chosen_worker.get('token_efficiency_score', 0):.2f}), "
            f"measured quality ({chosen_worker.get('measured_quality_score', 0):.2f}), "
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
    budget_probes: list[dict[str, Any]] | None = None,
    subscription_usage_snapshots: list[dict[str, Any]] | None = None,
    token_usage_summary: dict[str, dict[str, Any]] | None = None,
    operation_quality_scores: dict[str, dict[str, dict[str, Any]]] | None = None,
    service_health: dict[str, str] | None = None,
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
        subscription_usage_snapshots: Live subscription usage snapshots

    Returns:
        RoutingDecision with chosen worker
    """
    if not workers:
        # Fallback to original routing (capability-based)
        from orchestrator.routing.engine import route_intent
        return route_intent(intent, [], quota_state, project_policy)

    return route_with_workers(
        intent,
        workers,
        job_class,
        quota_state,
        project_policy,
        budget_probes=budget_probes,
        subscription_usage_snapshots=subscription_usage_snapshots,
        job_class_spec=job_class_spec,
        token_usage_summary=token_usage_summary,
        operation_quality_scores=operation_quality_scores,
        service_health=service_health,
    )
