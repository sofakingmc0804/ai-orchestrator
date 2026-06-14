from __future__ import annotations

import json
from typing import Any

from orchestrator.state.store import StateStore


PREMIUM_CONTRACT_MARKERS = ("subscription", "premium", "paid_flat_rate")
PREMIUM_NAME_MARKERS = ("claude", "codex", "chatgpt", "copilot", "gemini", "openai", "anthropic")


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _haystack(*values: Any) -> str:
    return " ".join(str(value or "").lower() for value in values)


def _is_premium_worker(worker: dict[str, Any] | None, fallback: str) -> bool:
    if worker:
        contract_type = str(worker.get("contract_type") or "").lower()
        if any(marker in contract_type for marker in PREMIUM_CONTRACT_MARKERS):
            return True
        if contract_type == "local_resource":
            return False
        fallback = _haystack(
            fallback,
            worker.get("worker_id"),
            worker.get("provider_id"),
            worker.get("surface"),
            worker.get("model_id"),
            worker.get("base_model"),
        )
    return any(marker in fallback.lower() for marker in PREMIUM_NAME_MARKERS)


def _match_worker(token_row: dict[str, Any], workers: list[dict[str, Any]]) -> dict[str, Any] | None:
    token_model = str(token_row.get("model") or "").lower()
    token_provider = str(token_row.get("provider") or "").lower()
    token_adapter = str(token_row.get("adapter_name") or "").lower()
    token_quota = str(token_row.get("quota_provider") or "").lower()
    best: tuple[int, dict[str, Any] | None] = (0, None)

    for worker in workers:
        worker_id = str(worker.get("worker_id") or "").lower()
        model_id = str(worker.get("model_id") or "").lower()
        base_model = str(worker.get("base_model") or "").lower()
        provider_id = str(worker.get("provider_id") or "").lower()
        surface = str(worker.get("surface") or "").lower()
        score = 0
        if token_model and token_model in {model_id, base_model}:
            score += 5
        elif token_model and token_model in worker_id:
            score += 3
        if token_provider and token_provider in {provider_id, surface}:
            score += 4
        elif token_provider and token_provider in worker_id:
            score += 2
        if token_adapter and token_adapter in {provider_id, surface}:
            score += 3
        if token_quota and token_quota in {provider_id, surface}:
            score += 2
        if score > best[0]:
            best = (score, worker)

    return best[1] if best[0] >= 4 else None


def _quality_by_worker(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("proof_kind") or "") != "live":
            continue
        worker_id = str(row.get("worker_id") or "")
        if not worker_id:
            continue
        domain = str(row.get("operation_domain") or "unknown")
        worker = grouped.setdefault(worker_id, {"samples": 0, "score_total": 0.0, "domains": {}})
        worker["samples"] += 1
        worker["score_total"] += float(row.get("composite_score") or 0.0)
        domain_item = worker["domains"].setdefault(domain, {"samples": 0, "score_total": 0.0})
        domain_item["samples"] += 1
        domain_item["score_total"] += float(row.get("composite_score") or 0.0)

    for worker in grouped.values():
        samples = max(int(worker["samples"]), 1)
        worker["avg_quality_score"] = round(float(worker.pop("score_total")) / samples, 4)
        domains: dict[str, Any] = {}
        for domain, item in worker["domains"].items():
            domain_samples = max(int(item["samples"]), 1)
            domains[domain] = {
                "samples": int(item["samples"]),
                "avg_quality_score": round(float(item.pop("score_total")) / domain_samples, 4),
            }
        worker["domains"] = domains
    return grouped


async def build_token_accounting_payload(store: StateStore, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit), 1000))
    directives = await store.token_usage_by_directive(limit=limit)
    token_rows = await store.list_token_usage(limit=max(limit * 25, 1000))
    workers = await store.list_worker_cards(limit=1000)
    quality = _quality_by_worker(await store.list_operation_quality_scores(limit=2000))

    totals = {
        "directives": len(directives),
        "completed_directives": sum(1 for row in directives if row.get("completed")),
        "attempts": sum(int(row.get("attempts") or 0) for row in directives),
        "tokens_in": sum(int(row.get("tokens_in") or 0) for row in directives),
        "tokens_out": sum(int(row.get("tokens_out") or 0) for row in directives),
        "tokens_total": sum(int(row.get("tokens_total") or 0) for row in directives),
    }
    completed = int(totals["completed_directives"])
    totals["tokens_per_completed_directive"] = (
        round(int(totals["tokens_total"]) / completed, 2) if completed else None
    )

    by_agent: dict[str, dict[str, Any]] = {}
    for row in token_rows:
        worker = _match_worker(row, workers)
        fallback_id = " / ".join(
            part
            for part in (
                str(row.get("provider") or ""),
                str(row.get("model") or ""),
                str(row.get("adapter_name") or ""),
            )
            if part
        ) or "unknown-agent"
        agent_id = str((worker or {}).get("worker_id") or fallback_id)
        item = by_agent.setdefault(
            agent_id,
            {
                "agent_id": agent_id,
                "worker_id": (worker or {}).get("worker_id"),
                "provider_id": (worker or {}).get("provider_id") or row.get("provider"),
                "model_id": (worker or {}).get("model_id") or row.get("model"),
                "contract_type": (worker or {}).get("contract_type") or "unknown",
                "marginal_cost": _json_dict((worker or {}).get("marginal_cost_json")),
                "attempts": 0,
                "successful_attempts": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "tokens_total": 0,
                "_directives": set(),
                "_completed_directives": set(),
                "is_premium_agent": _is_premium_worker(worker, fallback_id),
            },
        )
        directive_id = str(row.get("intent_id") or row.get("dispatch_id") or row.get("id") or "")
        success = bool(row.get("success"))
        item["attempts"] += 1
        item["successful_attempts"] += int(success)
        item["tokens_in"] += int(row.get("tokens_in") or 0)
        item["tokens_out"] += int(row.get("tokens_out") or 0)
        item["tokens_total"] += int(row.get("tokens_total") or 0)
        if directive_id:
            item["_directives"].add(directive_id)
            if success:
                item["_completed_directives"].add(directive_id)

    agents: list[dict[str, Any]] = []
    for item in by_agent.values():
        directive_count = len(item.pop("_directives"))
        completed_count = len(item.pop("_completed_directives"))
        worker_quality = quality.get(str(item.get("worker_id") or item["agent_id"])) or {}
        item["directives"] = directive_count
        item["completed_directives"] = completed_count
        item["tokens_per_completed_directive"] = (
            round(int(item["tokens_total"]) / completed_count, 2) if completed_count else None
        )
        item["avg_quality_score"] = worker_quality.get("avg_quality_score")
        item["quality_samples"] = int(worker_quality.get("samples") or 0)
        item["quality_by_operation_domain"] = worker_quality.get("domains") or {}
        agents.append(item)

    agents.sort(
        key=lambda item: (
            not bool(item.get("is_premium_agent")),
            -(int(item.get("tokens_total") or 0)),
            str(item.get("agent_id") or ""),
        )
    )
    premium_agents = [item for item in agents if item.get("is_premium_agent")]

    return {
        "state": "produced",
        "proof_kind": "live",
        "proof_table": "token_usage",
        "limit": limit,
        "totals": totals,
        "directives": directives,
        "cost_quality_by_agent": agents,
        "premium_agents": premium_agents,
    }
