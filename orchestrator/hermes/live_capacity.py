"""Build live, provider-scoped worker cards for Hermes capacity lanes.

The static roster describes known models.  This module supplies the small
runtime seam that admits only models observed in a live provider catalog and
attaches the corresponding quota/cost evidence without copying credentials.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


FORBIDDEN_CONTRACTS = {"metered_extra_cost", "third_party_metered", "unknown_cost"}

_CONTRACT_RULES = {
    "local_resource": {
        "approval_required": False,
        "salary_bucket": "local compute",
        "overtime_rule": "No cash cost; bounded by local hardware availability.",
    },
    "subscription_quota": {
        "approval_required": False,
        "salary_bucket": "subscription quota",
        "overtime_rule": "Use only while live account quota remains above reserve.",
    },
    "subscription_usage": {
        "approval_required": False,
        "salary_bucket": "included provider usage",
        "overtime_rule": "Use only when live provider evidence proves included usage.",
    },
    "third_party_metered": {
        "approval_required": True,
        "salary_bucket": "third-party metered API",
        "overtime_rule": "Blocked until paid balance and explicit billing approval are proved.",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def provider_contract(provider_id: str, *, model_id: str, cost_proved: bool) -> str:
    """Classify one live provider/model without treating catalog presence as quota."""

    provider = provider_id.strip().lower()
    model = model_id.strip().lower()
    if provider in {"lmstudio", "lm-studio", "ollama-local", "ollama-http"}:
        return "local_resource"
    if provider == "openrouter" and model.endswith(":free") and cost_proved:
        return "subscription_usage"
    if provider == "nous" and cost_proved:
        return "subscription_usage"
    if provider == "openai-codex" and cost_proved:
        return "subscription_quota"
    if provider in {"ollama-cloud", "github-copilot", "codex-chatgpt"} and cost_proved:
        return "subscription_quota"
    if provider in {"claude-max", "claude-code-cli"} and cost_proved:
        return "subscription_quota"
    return "third_party_metered"


def _capabilities_for_model(model_id: str, provider_id: str) -> list[str]:
    model = model_id.lower()
    provider = provider_id.lower()
    if provider in {"lmstudio", "lm-studio"} and "embed" in model:
        return ["embeddings", "embed_text"]
    capabilities = ["reasoning", "structured_output", "chat"]
    if any(marker in model for marker in ("code", "coder", "codex", "deepseek", "qwen", "glm")):
        capabilities.extend(["coding", "code_review"])
    if any(marker in model for marker in ("vision", "vl", "omni")):
        capabilities.append("vision")
    if provider in {"nous", "openai-codex"}:
        capabilities.extend(["agentic", "tools"])
    return sorted(set(capabilities))


def build_live_worker_card(
    *,
    provider_id: str,
    model_id: str,
    contract_type: str,
    quota_state: dict[str, Any] | None,
    context_tokens: int = 0,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Create the roster shape consumed by the existing worker selector."""

    provider = provider_id.strip().lower()
    model = model_id.strip()
    contract = contract_type.strip().lower()
    rules = _CONTRACT_RULES.get(contract, _CONTRACT_RULES["third_party_metered"])
    quota = deepcopy(quota_state or {})
    status = str(quota.get("status") or "unknown")
    evidence = ["live_model_catalog"]
    if quota:
        evidence.append("live_quota_receipt")
    caps = sorted(set(capabilities or _capabilities_for_model(model, provider)))
    best_jobs = ["deep_debugging", "architecture"] if "reasoning" in caps else []
    if "coding" in caps:
        best_jobs.extend(["simple_coding", "repo_coding"])
    if "embeddings" in caps:
        best_jobs.append("schema_validation")
    if "agentic" in caps:
        best_jobs.append("agentic_repair")
    surface = "lm-studio" if provider in {"lmstudio", "lm-studio"} else provider
    return {
        "worker_id": f"{model}@{provider}",
        "model_id": model,
        "base_model": model,
        "surface": surface,
        "provider": provider,
        "provider_id": provider,
        "contract_type": contract,
        "salary_bucket": rules["salary_bucket"],
        "overtime_rule": rules["overtime_rule"],
        "budget_source_id": str(quota.get("source") or f"{provider}-live"),
        "remaining_budget_source": str(quota.get("source") or f"{provider}-live"),
        "marginal_cost": {
            "unit": "local_hardware_time" if contract == "local_resource" else "provider_specific",
            "input_per_mtok": 0 if contract == "local_resource" else None,
            "output_per_mtok": 0 if contract == "local_resource" else None,
        },
        "hardware_fit": "local" if contract == "local_resource" else "cloud",
        "context_window": int(context_tokens or 0),
        "modalities": ["text"],
        "capabilities": caps,
        "tools": ["chat", "openai_compatible_api"] if "embeddings" not in caps else ["embeddings", "openai_compatible_api"],
        "stats": {
            "coding": 8 if "coding" in caps else 3,
            "reasoning": 8 if "reasoning" in caps else 3,
            "structured_output": 7 if "structured_output" in caps else 3,
            "stability": 7 if status == "ok" else 4,
            "cost_pressure": 10 if contract == "local_resource" else 8,
            "agentic_loop": 8 if "agentic" in caps else 3,
        },
        "best_jobs": sorted(set(best_jobs)),
        "avoid_jobs": [] if status == "ok" else ["agentic_repair", "deep_debugging"],
        "badges": ["LIVE_CATALOG", "LIVE_QUOTA" if quota else "QUOTA_UNPROVED"],
        "approval_required": bool(rules["approval_required"]),
        "source_evidence": evidence,
        "dynamic_state": {
            "refresh_status": status,
            "remaining_budget_state": str(quota.get("remaining_budget_state") or status),
            "current_usage": quota,
            "last_verified": utc_now(),
        },
        "last_verified": utc_now(),
    }


def merge_live_workers(roster: dict[str, Any], live_workers: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace refreshed worker ids and append new live ids exactly once."""

    merged = deepcopy(roster)
    rows = {str(row.get("worker_id")): row for row in (merged.get("workers") or []) if row.get("worker_id")}
    for worker in live_workers:
        worker_id = str(worker.get("worker_id") or "").strip()
        if worker_id:
            rows[worker_id] = deepcopy(worker)
    merged["workers"] = sorted(rows.values(), key=lambda row: str(row.get("worker_id") or ""))
    merged["updated_at"] = utc_now()
    return merged
