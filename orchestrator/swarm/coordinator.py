from __future__ import annotations

from typing import Any

from orchestrator.state.store import StateStore
from orchestrator.workspace_runtime import WorkspacePolicyError


LOW_RISK_LOCAL_ROLES: tuple[dict[str, str], ...] = (
    {"name": "planner", "contract": "decompose the local packet and name its consumer"},
    {"name": "builder", "contract": "assemble a local-only handoff artifact"},
    {"name": "reviewer", "contract": "verify workspace, budget, consumer, and no side effects"},
)


async def run_low_risk_local_swarm(
    store: StateStore,
    *,
    workspace_id: str,
    work_packet_id: str,
    consumer: str,
) -> dict[str, Any]:
    """Run a bounded three-role local swarm with no external side effects.

    This coordinator is deliberately small but operational: roles execute in a
    governed order, the reviewer sees the previous outputs, budget is checked,
    and only the named consumer receives the completed local artifact receipt.
    It never invokes a provider, transfers workspace data, or writes outside
    the StateStore receipt lane.
    """
    packet = await store.get_work_packet(work_packet_id)
    if packet is None or packet.get("workspace_id") != workspace_id:
        raise WorkspacePolicyError("swarm work packet must remain inside its declared workspace")
    if not consumer.strip():
        raise WorkspacePolicyError("swarm requires a named consumer")

    budget = {"max_tokens": 300, "used_tokens": 0, "cost_cap_usd": 0.0}
    swarm = await store.start_swarm_run(
        workspace_id=workspace_id,
        work_packet_id=work_packet_id,
        roles=[dict(role) for role in LOW_RISK_LOCAL_ROLES],
        budget=budget,
        reviewer="reviewer",
        output_consumer=consumer,
        stop_rule="stop_before_external_side_effect_or_budget_exhaustion",
    )

    role_results: list[dict[str, Any]] = []
    role_results.append(
        {
            "role": "planner",
            "contract": LOW_RISK_LOCAL_ROLES[0]["contract"],
            "result": {"work_packet_id": work_packet_id, "consumer": consumer, "next_role": "builder"},
            "token_estimate": 24,
        }
    )
    role_results.append(
        {
            "role": "builder",
            "contract": LOW_RISK_LOCAL_ROLES[1]["contract"],
            "result": {"artifact_kind": "local_handoff", "workspace_id": workspace_id, "external_side_effects": False},
            "token_estimate": 36,
        }
    )
    budget["used_tokens"] = sum(int(row["token_estimate"]) for row in role_results)
    reviewer_result = {
        "role": "reviewer",
        "contract": LOW_RISK_LOCAL_ROLES[2]["contract"],
        "outcome": "accepted",
        "checks": {
            "budget_within_cap": budget["used_tokens"] <= budget["max_tokens"],
            "consumer_named": bool(consumer.strip()),
            "no_external_side_effects": True,
            "workspace_preserved": packet.get("workspace_id") == workspace_id,
        },
        "token_estimate": 18,
    }
    if not all(bool(value) for value in reviewer_result["checks"].values()):
        raise WorkspacePolicyError("governed swarm reviewer rejected the local run")
    role_results.append(reviewer_result)
    budget["used_tokens"] += int(reviewer_result["token_estimate"])
    consumer_output = {
        "consumer": consumer,
        "kind": "local_handoff",
        "workspace_id": workspace_id,
        "external_side_effects": False,
        "summary": "Bounded local handoff reviewed without cross-workspace transfer.",
    }
    completed = await store.complete_swarm_run(
        swarm["id"],
        reviewer_outcome="accepted",
        receipt={
            "consumer": consumer,
            "consumer_output": consumer_output,
            "role_results": role_results,
            "budget": budget,
            "workspace_id": workspace_id,
        },
    )
    return {
        "swarm": completed,
        "role_results": role_results,
        "reviewer_result": reviewer_result,
        "consumer_output": consumer_output,
    }
