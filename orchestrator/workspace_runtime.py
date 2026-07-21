from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.state.store import StateStore


class WorkspacePolicyError(ValueError):
    """Raised when a workspace boundary or governed execution rule is violated."""


WORKSPACE_ROOTS: tuple[dict[str, Any], ...] = (
    {
        "id": "personal",
        "label": "Personal",
        "classification": "private_machine_work",
        "policy": {
            "connectors": "personal_only",
            "transfer_out": "proposal_required",
            "external_side_effects": "owner_approval_required",
        },
    },
    {
        "id": "example",
        "label": "Example",
        "classification": "business_work",
        "policy": {
            "connectors": "example_only",
            "transfer_in": "owner_approval_required",
            "external_side_effects": "owner_approval_required",
        },
    },
    {
        "id": "system",
        "label": "System",
        "classification": "machine_resource_governance",
        "policy": {
            "content": "aggregate_only",
            "connectors": "none",
            "external_side_effects": "forbidden",
        },
    },
    {
        "id": "unclassified_legacy",
        "label": "Unclassified legacy",
        "classification": "legacy_isolation",
        "policy": {
            "retrieval": "explicit_assignment_required",
            "transfer_out": "owner_approval_required",
            "external_side_effects": "forbidden",
        },
    },
)


DEFAULT_MODE_PACKS: tuple[dict[str, Any], ...] = (
    {
        "id": "software_engineering.v1",
        "version": "v1",
        "name": "Software engineering",
        "allowed_workspaces": ["personal", "example"],
        "source_authority": "repo_and_runtime_receipts",
        "preloaded_skills": ["repo_read", "test_runner"],
        "model_requirements": {"capabilities": ["coding", "tools"], "local_first": True},
        "evaluator": "test_and_runtime_receipt",
        "budget": {"max_tokens": 120000, "cost_class": "cheapest_competent"},
        "completion_test": {"kind": "runtime", "requires": ["test_receipt", "consumer_output"]},
    },
    {
        "id": "technical_writing.v1",
        "version": "v1",
        "name": "Technical writing",
        "allowed_workspaces": ["personal", "example"],
        "source_authority": "named_sources_and_owner_receipts",
        "preloaded_skills": ["source_reader"],
        "model_requirements": {"capabilities": ["writing", "source_grounding"], "local_first": True},
        "evaluator": "source_coverage_review",
        "budget": {"max_tokens": 60000, "cost_class": "cheapest_competent"},
        "completion_test": {"kind": "review", "requires": ["source_links", "consumer_output"]},
    },
    {
        "id": "research.v1",
        "version": "v1",
        "name": "Research",
        "allowed_workspaces": ["personal", "example"],
        "source_authority": "primary_sources_first",
        "preloaded_skills": ["source_reader"],
        "model_requirements": {"capabilities": ["research", "source_grounding"], "local_first": True},
        "evaluator": "claim_source_trace",
        "budget": {"max_tokens": 80000, "cost_class": "cheapest_competent"},
        "completion_test": {"kind": "evidence", "requires": ["source_trace", "consumer_output"]},
    },
    {
        "id": "industrial_controls.v1",
        "version": "v1",
        "name": "Industrial controls engineering",
        "allowed_workspaces": ["personal", "example"],
        "source_authority": "approved_controls_documents",
        "preloaded_skills": ["source_reader"],
        "model_requirements": {"capabilities": ["engineering", "safety_review"], "local_first": True},
        "evaluator": "controls_peer_review",
        "budget": {"max_tokens": 100000, "cost_class": "cheapest_competent"},
        "completion_test": {"kind": "review", "requires": ["safety_review", "consumer_output"]},
    },
    {
        "id": "project_management.v1",
        "version": "v1",
        "name": "Project management",
        "allowed_workspaces": ["personal", "example"],
        "source_authority": "workspace_packets_and_owner_decisions",
        "preloaded_skills": ["source_reader"],
        "model_requirements": {"capabilities": ["planning", "coordination"], "local_first": True},
        "evaluator": "consumer_acceptance",
        "budget": {"max_tokens": 60000, "cost_class": "cheapest_competent"},
        "completion_test": {"kind": "delivery", "requires": ["named_consumer", "delivery_receipt"]},
    },
)


def workspace_connector_context(workspace_id: str, known_service_count: int) -> dict[str, Any]:
    """Expose only connector grants, never an unclassified service catalogue.

    The existing machine service inventory predates workspace assignment.  Its
    connector records therefore remain in the legacy lane until an owner makes
    a grant; neither Personal nor Example is allowed to infer ownership from a
    service name, a saved login, or a provider health row.
    """
    if workspace_id not in {"personal", "example"}:
        raise WorkspacePolicyError("connector context requires a Personal or Example workspace")
    return {
        "workspace_id": workspace_id,
        "state": "owner_assignment_required",
        "connectors": [],
        "unclassified_service_count": max(int(known_service_count), 0),
        "message": "No connector is assigned to this workspace yet; the existing service inventory remains unclassified.",
    }


async def build_platform_console_payload(store: "StateStore") -> dict[str, Any]:
    """Return a deliberately aggregate-only system view.

    No packet intent, payload, transfer summary, workspace name, memory, connector,
    or output is returned here.  The console can prove resource behavior without
    becoming a back door into Personal or Example work.
    """

    services = await store.list_services()
    events = await store.list_resource_events(limit=1000)
    episodes = await store.list_evaluation_episodes(limit=1000)
    worker_cards = await store.list_worker_cards(limit=1000)
    repairs = await store.list_repair_queue(include_resolved=False, limit=100)
    receipts = await store.list_receipts(limit=1000)

    metering: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (str(event["provider"]), str(event["model"]))
        item = metering.setdefault(
            key,
            {
                "provider": key[0],
                "model": key[1],
                "events": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "tokens_total": 0,
                "estimated_cost_usd": 0.0,
                "actual_cost_usd": 0.0,
                "estimated_cost_unknown_events": 0,
                "actual_cost_unknown_events": 0,
                "quota_sources": set(),
                "measurement_sources": set(),
                "context_pressure_max": 0.0,
            },
        )
        item["events"] += 1
        item["tokens_in"] += int(event.get("tokens_in") or 0)
        item["tokens_out"] += int(event.get("tokens_out") or 0)
        item["tokens_total"] += int(event.get("tokens_total") or 0)
        if event.get("estimated_cost_usd") is None:
            item["estimated_cost_unknown_events"] += 1
        else:
            item["estimated_cost_usd"] += float(event["estimated_cost_usd"])
        if event.get("actual_cost_usd") is None:
            item["actual_cost_unknown_events"] += 1
        else:
            item["actual_cost_usd"] += float(event["actual_cost_usd"])
        item["quota_sources"].add(str(event.get("quota_source") or "unknown"))
        item["measurement_sources"].add(str(event.get("measurement_source") or "unknown"))
        item["context_pressure_max"] = max(item["context_pressure_max"], float(event.get("context_pressure") or 0.0))

    metering_rows: list[dict[str, Any]] = []
    for row in metering.values():
        row["estimated_cost_usd"] = None if row["estimated_cost_unknown_events"] else round(row["estimated_cost_usd"], 6)
        row["actual_cost_usd"] = None if row["actual_cost_unknown_events"] else round(row["actual_cost_usd"], 6)
        row["quota_sources"] = sorted(row["quota_sources"])
        row["measurement_sources"] = sorted(row["measurement_sources"])
        metering_rows.append(row)
    metering_rows.sort(key=lambda row: (-int(row["tokens_total"]), str(row["provider"]), str(row["model"])))

    episodes_by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        episodes_by_model[str(episode["selected_model"])].append(episode)

    workers_by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for worker in worker_cards:
        model_id = str(worker.get("model_id") or "")
        if model_id:
            workers_by_model[model_id].append(worker)

    def parse_list(value: object) -> list[str]:
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        return sorted({str(item) for item in parsed if isinstance(item, str)}) if isinstance(parsed, list) else []

    def parse_object(value: object) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    model_cards: list[dict[str, Any]] = []
    for row in metering_rows:
        model_episodes = episodes_by_model.get(str(row["model"]), [])
        def provider_matches(worker_provider: object, metered_provider: object) -> bool:
            left = str(worker_provider or "").lower()
            right = str(metered_provider or "").lower()
            return left == right or {left, right} <= {"ollama", "ollama-local"}

        workers = [
            worker
            for worker in workers_by_model.get(str(row["model"]), [])
            if not worker.get("provider_id") or provider_matches(worker.get("provider_id"), row["provider"])
        ]
        static_capabilities = {
            "worker_count": len(workers),
            "worker_ids": sorted(str(worker.get("worker_id")) for worker in workers if worker.get("worker_id")),
            "capabilities": sorted({capability for worker in workers for capability in parse_list(worker.get("capabilities_json"))}),
            "tools": sorted({tool for worker in workers for tool in parse_list(worker.get("tools_json"))}),
            "modalities": sorted({modality for worker in workers for modality in parse_list(worker.get("modalities_json"))}),
            "context_window": max((int(worker.get("context_window") or 0) for worker in workers), default=0) or None,
            "stats": {str(worker.get("worker_id")): parse_object(worker.get("stats_json")) for worker in workers if worker.get("worker_id")},
        }
        scored = [float(item["score"]) for item in model_episodes if item.get("score") is not None]
        accepted = sum(1 for item in model_episodes if str(item.get("reviewer_outcome")) == "accepted")
        average_score = round(sum(scored) / len(scored), 4) if scored else None
        evidence = {
            "episode_count": len(model_episodes),
            "accepted_count": accepted,
            "average_score": average_score,
        }
        model_cards.append(
            {
                "provider": row["provider"],
                "model": row["model"],
                "static_capabilities": static_capabilities,
                "metering": {
                    "tokens_total": row["tokens_total"],
                    "actual_cost_usd": row["actual_cost_usd"],
                    "context_pressure_max": row["context_pressure_max"],
                    "quota_sources": row["quota_sources"],
                    "measurement_sources": row["measurement_sources"],
                },
                "specialization_evidence": evidence,
                "specialization_claim": {
                    "supported": evidence["episode_count"] > 0 and evidence["average_score"] is not None,
                    "episode_count": evidence["episode_count"],
                    "score": evidence["average_score"],
                },
            }
        )

    healthy_services = sum(1 for service in services if str(service.get("health_state")) == "healthy")
    service_summary = {
        "total": len(services),
        "healthy": healthy_services,
        "non_healthy": max(len(services) - healthy_services, 0),
    }
    repair_summary = {
        "open_count": len(repairs),
        "by_source": sorted({str(row.get("failure_source") or "unknown") for row in repairs}),
    }
    receipt_summary = {
        "total": len(receipts),
        "successful": sum(1 for receipt in receipts if bool(receipt.get("success"))),
    }
    return {
        "scope": "system",
        "service_summary": service_summary,
        "metering": metering_rows,
        "model_cards": model_cards,
        "repair_summary": repair_summary,
        "receipt_summary": receipt_summary,
        "content_policy": "aggregate_only",
    }
