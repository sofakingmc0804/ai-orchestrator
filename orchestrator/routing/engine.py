from __future__ import annotations

from typing import Any

from orchestrator.models import BillingClass, ConsequenceTier, Intent, RoutingDecision


BILLING_ORDER = {
    BillingClass.LOCAL_RESOURCE.value: 0,
    BillingClass.SUBSCRIPTION_UNLIMITED.value: 1,
    BillingClass.SUBSCRIPTION_QUOTA.value: 2,
    BillingClass.UNKNOWN_COST.value: 99,
    BillingClass.METERED_EXTRA_COST.value: 100,
}

FORBIDDEN = {BillingClass.METERED_EXTRA_COST.value, BillingClass.UNKNOWN_COST.value}
TIER_ORDER = {ConsequenceTier.LOW.value: 0, ConsequenceTier.MEDIUM.value: 1, ConsequenceTier.HIGH.value: 2, ConsequenceTier.CRITICAL.value: 3}
RESERVE_BY_TIER = {
    ConsequenceTier.LOW.value: 0.20,
    ConsequenceTier.MEDIUM.value: 0.20,
    ConsequenceTier.HIGH.value: 0.05,
    ConsequenceTier.CRITICAL.value: 0.0,
}
PROVIDER_BY_ADAPTER = {
    "copilot-gh": "github_copilot",
    "copilot-vscode": "github_copilot",
    "claude-desktop-mcp": "claude",
    "claude-code-cli": "claude",
    "gemini-cli": "gemini",
}


def _quota_rejection_reason(intent: Intent, row: dict[str, Any], quota_state: dict[str, dict[str, Any]]) -> str | None:
    if row.get("billing_class") != BillingClass.SUBSCRIPTION_QUOTA.value:
        return None
    provider = PROVIDER_BY_ADAPTER.get(str(row.get("adapter_name") or ""))
    if not provider:
        return None
    quota = quota_state.get(provider)
    if not quota:
        return None
    limit = int(quota.get("units_limit") or 0)
    remaining = int(quota.get("remaining") or 0)
    if limit <= 0:
        return None
    priority = str(intent.parsed_payload.get("priority") or "").lower()
    reserve = 0.0 if priority == "urgent" else RESERVE_BY_TIER.get(intent.consequence_tier.value, 0.20)
    reserve_units = limit * reserve
    if remaining <= reserve_units:
        return f"quota reserve protected for {provider}: remaining {remaining}/{limit} at reserve {reserve:.0%}"
    return None


def _project_policy_rejection(row: dict[str, Any], project_policy: dict[str, Any]) -> str | None:
    adapter = str(row.get("adapter_name") or "")
    allowed = {str(item) for item in project_policy.get("allowed_adapters") or []}
    forbidden = {str(item) for item in project_policy.get("forbidden_adapters") or []}
    if adapter in forbidden:
        return "adapter forbidden by project policy"
    if allowed and adapter not in allowed:
        return "adapter not in project allowed_adapters"
    return None


def _preference_rank(row: dict[str, Any], project_policy: dict[str, Any]) -> int:
    preferred = [str(item) for item in project_policy.get("preferred_adapters") or []]
    adapter = str(row.get("adapter_name") or "")
    return preferred.index(adapter) if adapter in preferred else 999


def route_intent(intent: Intent, capabilities: list[dict[str, Any]], quota_state: dict[str, dict[str, Any]] | None = None, project_policy: dict[str, Any] | None = None) -> RoutingDecision:
    quota_state = quota_state or {}
    project_policy = project_policy or {}
    required = str(intent.parsed_payload.get("required_capability") or "classify_text")
    considered: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    matches = [c for c in capabilities if c.get("capability_id") == required and c.get("enabled", 1)]
    for cap in matches:
        row = dict(cap)
        reason = None
        if row.get("billing_class") in FORBIDDEN:
            reason = "forbidden billing class"
        elif TIER_ORDER.get(intent.consequence_tier.value, 99) > TIER_ORDER.get(str(row.get("consequence_max")), -1):
            reason = "intent consequence exceeds adapter maximum"
        else:
            reason = _project_policy_rejection(row, project_policy) or _quota_rejection_reason(intent, row, quota_state)
        if reason:
            row["rejected_reason"] = reason
            rejected.append(row)
        else:
            considered.append(row)
    considered.sort(
        key=lambda c: (
            BILLING_ORDER.get(str(c.get("billing_class")), 99),
            _preference_rank(c, project_policy),
            str(c.get("latency_band")),
            -int(c.get("rating_quality") or 0),
        )
    )
    chosen = considered[0]["adapter_name"] if considered else None
    if chosen:
        reasoning = f"Selected {chosen} for {required}: cheapest allowed capability match under consequence, cost, quota, and project policy."
    else:
        reasoning = f"No allowed adapter satisfied {required}; create repair queue entry."
    return RoutingDecision(
        intent_id=intent.id,
        chosen_adapter=chosen,
        candidates_considered=considered,
        candidates_rejected=rejected,
        reasoning=reasoning,
    )
