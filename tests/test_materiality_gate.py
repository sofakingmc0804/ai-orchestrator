import pytest

from orchestrator.workbench.materiality import MaterialityGate
from orchestrator.workbench.models import OutcomeDelta, OutcomePosition


_DIMENSIONS = ("intent", "success", "consequence", "authority", "cost", "owner_visible_ux", "external_action", "irreversibility")


def _position(*, changed: str | None = None, reversible: bool = True, external: tuple[str, ...] = ()) -> OutcomePosition:
    return OutcomePosition(
        position_id="position-1",
        deltas=tuple(OutcomeDelta(dimension=dimension, baseline="same", candidate=("changed" if dimension == changed else "same")) for dimension in _DIMENSIONS),
        reversible=reversible,
        external_action_refs=external,
    )


def test_identical_position_is_ignored():
    result = MaterialityGate.assess(_position())
    assert result.disposition == "ignore"
    assert result.changed_dimensions == ()


def test_undelegated_structural_change_requires_question():
    result = MaterialityGate.assess(_position(changed="intent"))
    assert result.disposition == "ask"
    assert result.changed_dimensions == ("intent",)


def test_exact_reversible_default_can_display_only_with_authority():
    result = MaterialityGate.assess(_position(changed="cost"), delegated_dimensions={"cost"}, default_dimension="cost")
    assert result.disposition == "display_default"
    assert result.selected_default_option_id == "default"


def test_irreversible_external_change_requires_declaration():
    result = MaterialityGate.assess(_position(changed="external_action", reversible=False, external=("send-1",)))
    assert result.disposition == "declaration_required"


def test_semantic_identity_is_canonical_sha256_and_not_wording_text():
    first = MaterialityGate.assess(_position(changed="intent"))
    second = MaterialityGate.assess(_position(changed="intent")).model_copy(update={"semantic_identity": first.semantic_identity})
    assert len(first.semantic_identity) == 64
    assert first.semantic_identity == second.semantic_identity
