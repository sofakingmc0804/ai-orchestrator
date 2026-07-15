import pytest

from orchestrator.workbench.frame import FrameConflict, FrameService
from orchestrator.workbench.models import (
    EdgeUpsert,
    FrameChangeSet,
    FrameCursor,
    FrameNode,
    FrameState,
    NodeUpsert,
    Provenance,
)


def _provenance() -> Provenance:
    return Provenance(source_kind="owner", source_id="owner")


def _empty_state(version: int = 0) -> FrameState:
    return FrameState(
        task_id="task",
        branch_id="main",
        frame_version=version,
        nodes=(),
        edges=(),
        state_checksum=FrameService.hash_state(
            task_id="task", branch_id="main", frame_version=version, nodes=(), edges=()
        ),
    )


def _base(state: FrameState) -> FrameCursor:
    return FrameCursor(
        task_id=state.task_id,
        branch_id=state.branch_id,
        frame_version=state.frame_version,
        head_sequence=0,
        frame_state_checksum=state.state_checksum,
    )


def _change(text: str = "first") -> FrameChangeSet:
    return FrameChangeSet(
        node_operations=(
            NodeUpsert(
                operation_id="op-1",
                node_key="intent",
                new_node_id="node-1",
                kind="intent",
                text=text,
                value={"text": text},
                provenance=_provenance(),
            ),
        )
    )


def test_propose_change_requires_authentic_current_base_and_is_deterministic():
    service = FrameService(_empty_state())
    proposal = service.propose(
        proposal_id="proposal-1", base=_base(service.state), changes=_change(), provenance=_provenance()
    )
    assert proposal.proposed_version == 1
    assert proposal.preview.frame_version == 1
    assert proposal.preview_state_checksum == proposal.preview.state_checksum
    assert service.state.frame_version == 0
    with pytest.raises(FrameConflict):
        service.propose(proposal_id="proposal-2", base=_base(service.state).model_copy(update={"frame_state_checksum": "f" * 64}), changes=_change(), provenance=_provenance())


def test_preview_hash_is_deterministic_and_mutation_sensitive():
    service = FrameService(_empty_state())
    first = service.propose(proposal_id="p1", base=_base(service.state), changes=_change("one"), provenance=_provenance())
    second = service.propose(proposal_id="p2", base=_base(service.state), changes=_change("two"), provenance=_provenance())
    assert first.preview_state_checksum != second.preview_state_checksum
    assert FrameService.hash_preview(_change("one")) == FrameService.hash_preview(_change("one"))


def test_confirm_advances_once_and_rejects_stale_or_repeated_confirmation():
    service = FrameService(_empty_state())
    proposal = service.propose(proposal_id="p1", base=_base(service.state), changes=_change(), provenance=_provenance())
    confirmed = service.confirm("p1", current=_base(service.state))
    assert confirmed.frame_version == 1
    assert confirmed.nodes[0].node_key == "intent"
    with pytest.raises(FrameConflict):
        service.confirm("p1", current=_base(service.state))


def test_confirm_rejects_unknown_proposal_without_mutating_state():
    service = FrameService(_empty_state())
    with pytest.raises(FrameConflict):
        service.confirm("missing", current=_base(service.state))
    assert service.state == _empty_state()


def test_base_head_tokens_are_part_of_frame_authentication():
    service = FrameService(_empty_state())
    base = _base(service.state).model_copy(update={"head_sequence": 9})
    with pytest.raises(FrameConflict):
        service.propose(proposal_id="head-race", base=base, changes=_change(), provenance=_provenance())


def test_dependency_cycle_is_rejected_before_preview_is_persisted():
    service = FrameService(_empty_state())
    changes = FrameChangeSet(
        node_operations=(
            NodeUpsert(operation_id="a", node_key="a", new_node_id="a1", kind="fact", text="a", value=1, depends_on_node_keys=("b",), provenance=_provenance()),
            NodeUpsert(operation_id="b", node_key="b", new_node_id="b1", kind="fact", text="b", value=1, depends_on_node_keys=("a",), provenance=_provenance()),
        )
    )
    with pytest.raises(FrameConflict):
        service.propose(proposal_id="cycle", base=_base(service.state), changes=changes, provenance=_provenance())


def test_edge_operations_are_reflected_in_confirmed_snapshot():
    service = FrameService(_empty_state())
    changes = FrameChangeSet(
        node_operations=(
            NodeUpsert(operation_id="a", node_key="a", new_node_id="a1", kind="fact", text="a", value=1, provenance=_provenance()),
            NodeUpsert(operation_id="b", node_key="b", new_node_id="b1", kind="fact", text="b", value=1, provenance=_provenance()),
        ),
        edge_operations=(EdgeUpsert(operation_id="c", relation="supports", from_node_key="a", to_node_key="b"),),
    )
    proposal = service.propose(proposal_id="edge", base=_base(service.state), changes=changes, provenance=_provenance())
    assert proposal.preview.edges


def test_reject_change_closes_pending_proposal_without_advancing_frame():
    service = FrameService(_empty_state())
    service.propose(proposal_id="reject", base=_base(service.state), changes=_change(), provenance=_provenance())
    service.reject("reject")
    with pytest.raises(FrameConflict):
        service.confirm("reject", current=_base(service.state))
    assert service.state.frame_version == 0
