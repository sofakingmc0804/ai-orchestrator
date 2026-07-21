from __future__ import annotations

import hashlib
from typing import Any

from orchestrator.governance.job_classifier import classify_with_fallback
from orchestrator.intent.interpreter import parse_intent
from orchestrator.routing.worker_routing import route_intent_worker_aware
from orchestrator.state.store import StateStore


def _stable_route_intent_id(job_class: str, text: str) -> str:
    digest = hashlib.sha256(f"{job_class}\0{text}".encode("utf-8")).hexdigest()[:16]
    return f"route_{digest}"


def _service_health_lookup(services: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("adapter_name")): str(row.get("health_state") or "unknown")
        for row in services
        if row.get("adapter_name")
    }


async def route_brain(
    store: StateStore,
    *,
    text: str,
    job_class: str | None = None,
    project_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classification = classify_with_fallback(text, job_class)
    resolved_job_class = str(classification["job_class"])
    job_spec = await store.db.fetchrow("SELECT * FROM job_classes WHERE job_class = ?", resolved_job_class)
    if not job_spec and not classification.get("overridden"):
        fallback_spec = await store.db.fetchrow("SELECT * FROM job_classes WHERE job_class = ?", "routing_triage")
        if fallback_spec:
            classification = {
                **classification,
                "fallback_from_job_class": resolved_job_class,
                "reasoning": f"{classification.get('reasoning', '')}; unknown class fell back to routing_triage",
            }
            resolved_job_class = "routing_triage"
            job_spec = fallback_spec
    if not job_spec:
        known = await store.db.fetch("SELECT job_class FROM job_classes ORDER BY job_class")
        return {
            "state": "continuation_required",
            "job_class": resolved_job_class,
            "classification": classification,
            "error": f"Job class not found: {resolved_job_class}",
            "known_job_classes": [row["job_class"] for row in known],
            "decision": None,
            "ranked_ladder": [],
        }

    intent = parse_intent(text, source="brain-route")
    intent.id = _stable_route_intent_id(resolved_job_class, text)
    workers = await store.db.fetch("SELECT * FROM worker_cards")
    decision = route_intent_worker_aware(
        intent,
        workers,
        resolved_job_class,
        quota_state=await store.latest_quota_state(),
        project_policy=project_policy or {},
        job_class_spec=job_spec,
        budget_probes=await store.list_budget_probes(),
        subscription_usage_snapshots=await store.list_subscription_usage_snapshots(),
        token_usage_summary=await store.token_usage_summary(),
        operation_quality_scores=await store.load_live_operation_quality_scores(),
        service_health=_service_health_lookup(await store.list_services()),
    )
    decision_payload = decision.model_dump(mode="json")
    decision_payload.pop("decided_at", None)
    return {
        "state": "produced" if decision.chosen_adapter else "continuation_required",
        "route_id": intent.id,
        "job_class": resolved_job_class,
        "classification": classification,
        "decision": decision_payload,
        "ranked_ladder": decision_payload.get("candidates_considered", []),
        "rejected": decision_payload.get("candidates_rejected", []),
        "reasoning": decision.reasoning,
    }
