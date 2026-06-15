from __future__ import annotations

from orchestrator.evaluation.soundness_critic import run_bounded_soundness_critic


def test_bounded_soundness_critic_returns_specific_source_objection() -> None:
    result = run_bounded_soundness_critic(
        "The service was approved for production.",
        [
            {
                "source_id": "SRC-1",
                "statement": "The service was denied for production.",
                "forbidden_claims": ["approved for production"],
            }
        ],
        [{"id": "source-match", "source_id": "SRC-1"}],
        high_stakes=True,
    )

    assert result["status"] == "UNCERTAIN"
    assert result["routing_reason"] == "final critique"
    assert result["multi_agent_debate"] is False
    assert len(result["attempts"]) == 3
    assert "claim contradicts source SRC-1" in result["attempts"][0]["objection"]


def test_bounded_soundness_critic_passes_after_revision_without_exceeding_limit() -> None:
    def revise(_claim: str, _verdict: dict, _attempt: int) -> str:
        return "The service was denied for production."

    result = run_bounded_soundness_critic(
        "The service was approved for production.",
        [
            {
                "source_id": "SRC-1",
                "statement": "The service was denied for production.",
                "forbidden_claims": ["approved for production"],
            }
        ],
        [{"id": "source-match", "source_id": "SRC-1"}],
        high_stakes=True,
        revision_provider=revise,
    )

    assert result["status"] == "PASS"
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["status"] == "objection"
    assert result["attempts"][1]["status"] == "pass"


def test_bounded_soundness_critic_skips_non_high_stakes_checkpoints() -> None:
    result = run_bounded_soundness_critic(
        "Casual claim.",
        [],
        [],
        high_stakes=False,
    )

    assert result["status"] == "skipped"
    assert result["attempts"] == []
