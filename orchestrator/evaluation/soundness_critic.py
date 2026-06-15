from __future__ import annotations

from collections.abc import Callable
from typing import Any


Critic = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]
RevisionProvider = Callable[[str, dict[str, Any], int], str]


def evidence_source_critic(
    claim: str,
    cited_evidence: list[dict[str, Any]],
    criteria: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic critic for source-contradiction fixtures.

    Evidence rows may carry forbidden_claims phrases. If the claim contains one,
    the critic returns a specific objection tied to the source id and statement.
    """

    claim_lower = claim.lower()
    for source in cited_evidence:
        source_id = str(source.get("source_id") or source.get("id") or "unknown-source")
        statement = str(source.get("statement") or source.get("text") or "")
        for phrase in source.get("forbidden_claims", []) or []:
            phrase_text = str(phrase)
            if phrase_text and phrase_text.lower() in claim_lower:
                return {
                    "status": "objection",
                    "objection": f"claim contradicts source {source_id}: contains '{phrase_text}' but source says '{statement}'",
                    "source_id": source_id,
                    "criterion": "source_contradiction",
                }

    for criterion in criteria:
        required_phrase = str(criterion.get("required_claim_phrase") or "")
        if required_phrase and required_phrase.lower() not in claim_lower:
            return {
                "status": "objection",
                "objection": f"claim fails criterion {criterion.get('id') or 'criterion'}: missing '{required_phrase}'",
                "source_id": str(criterion.get("source_id") or ""),
                "criterion": str(criterion.get("id") or "required_claim_phrase"),
            }

    return {"status": "pass", "objection": "", "criterion": "all_criteria_satisfied"}


def run_bounded_soundness_critic(
    claim: str,
    cited_evidence: list[dict[str, Any]],
    criteria: list[dict[str, Any]],
    *,
    high_stakes: bool,
    critic: Critic = evidence_source_critic,
    revision_provider: RevisionProvider | None = None,
    max_revisions: int = 3,
) -> dict[str, Any]:
    if not high_stakes:
        return {
            "status": "skipped",
            "reason": "not_high_stakes",
            "attempts": [],
            "max_revisions": max_revisions,
            "multi_agent_debate": False,
        }

    max_revisions = max(1, min(3, int(max_revisions)))
    current_claim = claim
    attempts: list[dict[str, Any]] = []

    for attempt_number in range(1, max_revisions + 1):
        verdict = critic(current_claim, cited_evidence, criteria)
        status = str(verdict.get("status") or "objection")
        objection = str(verdict.get("objection") or "")
        attempts.append(
            {
                "attempt": attempt_number,
                "status": status,
                "objection": objection,
                "criterion": str(verdict.get("criterion") or ""),
                "source_id": str(verdict.get("source_id") or ""),
            }
        )

        if status == "pass":
            return {
                "status": "PASS",
                "routing_reason": "final critique",
                "claim": current_claim,
                "attempts": attempts,
                "max_revisions": max_revisions,
                "multi_agent_debate": False,
            }

        if revision_provider is not None and attempt_number < max_revisions:
            current_claim = revision_provider(current_claim, verdict, attempt_number)

    return {
        "status": "UNCERTAIN",
        "routing_reason": "final critique",
        "claim": current_claim,
        "attempts": attempts,
        "max_revisions": max_revisions,
        "multi_agent_debate": False,
    }
