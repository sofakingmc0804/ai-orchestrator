from __future__ import annotations

from orchestrator.evaluation.sparring import (
    FROZEN_HELD_OUT_SUITE,
    evaluate_held_out_suite,
    held_out_case_prompt,
    score_held_out_response,
)


def test_held_out_sparring_requires_every_case_and_only_promotes_an_objective_improvement() -> None:
    baseline = {case.id: 0.60 for case in FROZEN_HELD_OUT_SUITE}
    candidate = {case.id: 0.75 for case in FROZEN_HELD_OUT_SUITE}

    promoted = evaluate_held_out_suite(baseline_scores=baseline, candidate_scores=candidate)
    assert promoted["decision"] == "promote"
    assert promoted["case_count"] == 5
    assert promoted["candidate_mean"] > promoted["baseline_mean"]

    rejected = evaluate_held_out_suite(
        baseline_scores=baseline,
        candidate_scores={case.id: 0.55 for case in FROZEN_HELD_OUT_SUITE},
    )
    assert rejected["decision"] == "reject"
    assert rejected["candidate_mean"] < rejected["baseline_mean"]


def test_held_out_runtime_prompt_has_a_frozen_marker_and_does_not_accept_extra_text() -> None:
    case = FROZEN_HELD_OUT_SUITE[0]
    prompt, marker = held_out_case_prompt(case)

    assert case.id in prompt
    assert marker in prompt
    assert score_held_out_response(marker, marker) == 1.0
    assert score_held_out_response(f"Sure: {marker}", marker) == 0.0
