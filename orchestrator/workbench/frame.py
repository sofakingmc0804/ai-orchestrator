from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from orchestrator.workbench.models import (
    CorrectionImpact,
    FrameChangeSet,
    FrameCursor,
    FrameNode,
    FrameEdge,
    FrameProposal,
    FrameState,
    NodeInvalidate,
    NodeUpsert,
    Provenance,
)


class FrameConflict(RuntimeError):
    """A proposal or confirmation does not match the witnessed frame authority."""


def _digest(value: Any, *, domain: str) -> str:
    payload = json.dumps({"domain": domain, "value": value}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _wire(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _wire(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _wire(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(v) for v in value]
    return value


class FrameService:
    def __init__(self, state: FrameState):
        self.state = state
        self._proposals: dict[str, FrameProposal] = {}

    @staticmethod
    def hash_state(*, task_id: str, branch_id: str, frame_version: int, nodes: tuple[FrameNode, ...], edges: tuple[Any, ...]) -> str:
        return _digest({"task_id": task_id, "branch_id": branch_id, "frame_version": frame_version, "nodes": _wire(nodes), "edges": _wire(edges)}, domain="workbench.frame.state.v1")

    @staticmethod
    def hash_preview(changes: FrameChangeSet) -> str:
        return _digest(_wire(changes), domain="workbench.frame.preview.v1")

    @staticmethod
    def hash_impact(impact: CorrectionImpact) -> str:
        return _digest(_wire(impact), domain="workbench.frame.impact.v1")

    def _check_base(self, base: FrameCursor) -> None:
        if (base.task_id, base.branch_id, base.frame_version, base.head_sequence, base.head_event_checksum, base.frame_state_checksum) != (self.state.task_id, self.state.branch_id, self.state.frame_version, 0, None, self.state.state_checksum):
            raise FrameConflict("frame base is stale or unauthenticated")

    def _preview(self, changes: FrameChangeSet) -> FrameState:
        nodes = {node.node_key: node for node in self.state.nodes}
        for operation in changes.node_operations:
            if isinstance(operation, NodeUpsert):
                prior = nodes.get(operation.node_key)
                if operation.expected_current_node_id is not None and (prior is None or prior.node_id != operation.expected_current_node_id):
                    raise FrameConflict("node expected-current token does not match")
                nodes[operation.node_key] = FrameNode(
                    node_id=operation.new_node_id, node_key=operation.node_key, task_id=self.state.task_id,
                    branch_id=self.state.branch_id, frame_version=self.state.frame_version + 1,
                    supersedes_node_id=prior.node_id if prior else None, kind=operation.kind, text=operation.text,
                    value=operation.value, status="confirmed", depends_on=operation.depends_on_node_keys,
                )
            elif isinstance(operation, NodeInvalidate):
                prior = nodes.get(operation.node_key)
                if prior is None or prior.node_id != operation.expected_current_node_id:
                    raise FrameConflict("node invalidation token does not match")
                nodes[operation.node_key] = prior.model_copy(update={"node_id": operation.new_node_id, "frame_version": self.state.frame_version + 1, "supersedes_node_id": prior.node_id, "status": "invalidated"})
        graph = {key: set(node.depends_on) for key, node in nodes.items()}
        for key, node in nodes.items():
            if key in graph:
                graph[key] = {dep for dep in graph[key] if dep in nodes}
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(key: str) -> None:
            if key in visiting:
                raise FrameConflict("frame dependency cycle")
            if key in visited:
                return
            visiting.add(key)
            for dep in graph.get(key, ()):
                visit(dep)
            visiting.remove(key)
            visited.add(key)
        for key in graph:
            visit(key)
        ordered = tuple(sorted(nodes.values(), key=lambda item: (item.node_key, item.node_id)))
        node_ids = {node.node_key: node.node_id for node in ordered}
        edges = list(self.state.edges)
        for operation in changes.edge_operations:
            if operation.from_node_key not in node_ids or operation.to_node_key not in node_ids:
                raise FrameConflict("edge endpoint is not in frame")
            edges.append(FrameEdge(edge_id=operation.operation_id, task_id=self.state.task_id, branch_id=self.state.branch_id, frame_version=self.state.frame_version + 1, from_node_id=node_ids[operation.from_node_key], to_node_id=node_ids[operation.to_node_key], relation=operation.relation))
        ordered_edges = tuple(sorted(edges, key=lambda item: (item.relation, item.from_node_id, item.to_node_id, item.edge_id)))
        checksum = self.hash_state(task_id=self.state.task_id, branch_id=self.state.branch_id, frame_version=self.state.frame_version + 1, nodes=ordered, edges=ordered_edges)
        return FrameState(task_id=self.state.task_id, branch_id=self.state.branch_id, frame_version=self.state.frame_version + 1, nodes=ordered, edges=ordered_edges, state_checksum=checksum)

    def propose(self, *, proposal_id: str, base: FrameCursor, changes: FrameChangeSet, provenance: Provenance) -> FrameProposal:
        self._check_base(base)
        if proposal_id in self._proposals:
            raise FrameConflict("proposal ID already exists")
        preview = self._preview(changes)
        impact = CorrectionImpact()
        proposal = FrameProposal(proposal_id=proposal_id, base=base, proposed_version=preview.frame_version, changes=changes, preview=preview, preview_state_checksum=preview.state_checksum, impact=impact, impact_preview_checksum=self.hash_impact(impact), status="pending", provenance=provenance)
        self._proposals[proposal_id] = proposal
        return proposal

    def confirm(self, proposal_id: str, *, current: FrameCursor) -> FrameState:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.status != "pending":
            raise FrameConflict("proposal is not pending")
        self._check_base(current)
        if proposal.base != current:
            raise FrameConflict("proposal base is stale")
        self.state = proposal.preview
        self._proposals[proposal_id] = proposal.model_copy(update={"status": "accepted"})
        return self.state

    def reject(self, proposal_id: str) -> None:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.status != "pending":
            raise FrameConflict("proposal is not pending")
        self._proposals[proposal_id] = proposal.model_copy(update={"status": "rejected"})
