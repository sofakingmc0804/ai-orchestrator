from __future__ import annotations

import pytest

from orchestrator.adapters.builtins import build_adapters
from orchestrator.intent.interpreter import parse_intent
from orchestrator.models import ConsequenceTier
from orchestrator.routing.engine import load_score_contract, route_intent


async def _caps() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for adapter in build_adapters().values():
        for cap in await adapter.capabilities():
            rows.append(cap.model_dump(mode="json"))
    return rows


def test_intent_parser_maps_agent_surface_keywords() -> None:
    assert parse_intent("run this through the OpenClaw gateway model").parsed_payload["required_capability"] == "model_gateway"
    assert parse_intent("use Hermes tool dispatch for this local task").parsed_payload["required_capability"] == "tool_dispatch"
    assert parse_intent("create embeddings for this file").parsed_payload["required_capability"] == "embed_text"


@pytest.mark.asyncio
async def test_gateway_intent_routes_to_openclaw() -> None:
    decision = route_intent(parse_intent("run this through the OpenClaw gateway model"), await _caps())
    assert decision.chosen_adapter == "openclaw-gateway"


@pytest.mark.asyncio
async def test_tool_dispatch_intent_routes_to_hermes() -> None:
    decision = route_intent(parse_intent("use Hermes tool dispatch for this local task"), await _caps())
    assert decision.chosen_adapter == "hermes-agent"


@pytest.mark.asyncio
async def test_plain_classification_stays_on_ollama_http() -> None:
    decision = route_intent(parse_intent("classify this plain local text"), await _caps())
    assert decision.chosen_adapter == "ollama-http"


def test_subscription_quota_route_refuses_below_reserve() -> None:
    intent = parse_intent("chat about this code")
    intent.consequence_tier = ConsequenceTier.LOW
    intent.parsed_payload["required_capability"] = "coding_chat"
    caps = [
        {
            "adapter_name": "copilot-gh",
            "capability_id": "coding_chat",
            "enabled": 1,
            "billing_class": "subscription_quota",
            "consequence_max": "medium",
            "latency_band": "fast",
            "rating_quality": 4,
        }
    ]

    decision = route_intent(
        intent,
        caps,
        {"github_copilot": {"units_limit": 100, "remaining": 20}},
    )

    assert decision.chosen_adapter is None
    assert decision.candidates_rejected[0]["rejected_reason"].startswith("quota reserve protected")


def test_urgent_route_can_use_reserved_subscription_quota() -> None:
    intent = parse_intent("chat about this code")
    intent.parsed_payload["required_capability"] = "coding_chat"
    intent.parsed_payload["priority"] = "urgent"
    caps = [
        {
            "adapter_name": "copilot-gh",
            "capability_id": "coding_chat",
            "enabled": 1,
            "billing_class": "subscription_quota",
            "consequence_max": "medium",
            "latency_band": "fast",
            "rating_quality": 4,
        }
    ]

    decision = route_intent(
        intent,
        caps,
        {"github_copilot": {"units_limit": 100, "remaining": 1}},
    )

    assert decision.chosen_adapter == "copilot-gh"


def test_project_policy_forbids_matching_adapter() -> None:
    intent = parse_intent("classify this local text")
    caps = [
        {
            "adapter_name": "ollama-http",
            "capability_id": "classify_text",
            "enabled": 1,
            "billing_class": "local_resource",
            "consequence_max": "medium",
            "latency_band": "fast",
            "rating_quality": 4,
        }
    ]

    decision = route_intent(intent, caps, project_policy={"forbidden_adapters": ["ollama-http"]})

    assert decision.chosen_adapter is None
    assert decision.candidates_rejected[0]["rejected_reason"] == "adapter forbidden by project policy"


def test_project_policy_preference_breaks_same_cost_tie() -> None:
    intent = parse_intent("classify this local text")
    caps = [
        {
            "adapter_name": "ollama-cli",
            "capability_id": "classify_text",
            "enabled": 1,
            "billing_class": "local_resource",
            "consequence_max": "medium",
            "latency_band": "fast",
            "rating_quality": 4,
        },
        {
            "adapter_name": "ollama-http",
            "capability_id": "classify_text",
            "enabled": 1,
            "billing_class": "local_resource",
            "consequence_max": "medium",
            "latency_band": "fast",
            "rating_quality": 4,
        },
    ]

    decision = route_intent(intent, caps, project_policy={"preferred_adapters": ["ollama-http"]})

    assert decision.chosen_adapter == "ollama-http"


def test_default_score_contract_loads_migrated_repo_contract() -> None:
    contract = load_score_contract()

    assert contract is not None
    assert contract["schema_version"].startswith("operation-score-contract/")
    assert "financial_operations" in contract["domain_leaders"]


def test_score_contract_changes_same_cost_local_routing_decision() -> None:
    intent = parse_intent("handle the local finance operation")
    intent.parsed_payload["required_capability"] = "local_chat"
    intent.parsed_payload["domain_id"] = "financial_operations"
    caps = [
        {
            "adapter_name": "lm-studio",
            "capability_id": "local_chat",
            "enabled": 1,
            "billing_class": "local_resource",
            "consequence_max": "medium",
            "latency_band": "fast",
            "provider": "lm_studio",
        },
        {
            "adapter_name": "ollama-http",
            "capability_id": "local_chat",
            "enabled": 1,
            "billing_class": "local_resource",
            "consequence_max": "medium",
            "latency_band": "slow",
            "provider": "ollama",
        },
    ]
    score_contract = {
        "schema_version": "operation-score-contract/v2",
        "tested_surfaces": ["ollama_http"],
        "domain_leaders": {
            "financial_operations": [
                {"model": "gemma4:12b", "mean_composite": 0.91, "pass_rate": 1.0},
                {"model": "gemma4:e4b", "mean_composite": 0.44, "pass_rate": 0.5},
            ]
        },
        "routing_rules": [
            {
                "domain_id": "financial_operations",
                "preferred_local_models": ["gemma4:12b"],
                "fallback_local_models": ["gemma4:e4b"],
            }
        ],
    }

    without_scores = route_intent(intent, caps, score_contract={})
    with_scores = route_intent(intent, caps, score_contract=score_contract)

    assert without_scores.chosen_adapter == "lm-studio"
    assert with_scores.chosen_adapter == "ollama-http"
    assert with_scores.candidates_considered[0]["recommended_model"] == "gemma4:12b"


def test_missing_domain_scores_keep_existing_routing_order() -> None:
    intent = parse_intent("handle the local finance operation")
    intent.parsed_payload["required_capability"] = "local_chat"
    intent.parsed_payload["domain_id"] = "unbenchmarked_domain"
    caps = [
        {
            "adapter_name": "lm-studio",
            "capability_id": "local_chat",
            "enabled": 1,
            "billing_class": "local_resource",
            "consequence_max": "medium",
            "latency_band": "fast",
            "provider": "lm_studio",
        },
        {
            "adapter_name": "ollama-http",
            "capability_id": "local_chat",
            "enabled": 1,
            "billing_class": "local_resource",
            "consequence_max": "medium",
            "latency_band": "slow",
            "provider": "ollama",
        },
    ]

    decision = route_intent(
        intent,
        caps,
        score_contract={
            "schema_version": "operation-score-contract/v2",
            "tested_surfaces": ["ollama_http"],
            "domain_leaders": {},
            "routing_rules": [],
        },
    )

    assert decision.chosen_adapter == "lm-studio"
    assert all("recommended_model" not in row for row in decision.candidates_considered)
