from __future__ import annotations

import hashlib
import json
from typing import Iterable

from orchestrator.workbench.models import MaterialityAssessment, MaterialityReason, OutcomePosition


class MaterialityGate:
    @staticmethod
    def assess(position: OutcomePosition, *, delegated_dimensions: Iterable[str] = (), default_dimension: str | None = None) -> MaterialityAssessment:
        changed = tuple(delta.dimension for delta in position.deltas if delta.baseline != delta.candidate)
        identity = hashlib.sha256(json.dumps([(delta.dimension, delta.candidate) for delta in position.deltas], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        if not changed:
            return MaterialityAssessment(disposition="ignore", semantic_identity=identity)
        if ("external_action" in changed or "irreversibility" in changed) and (not position.reversible or position.external_action_refs):
            return MaterialityAssessment(disposition="declaration_required", semantic_identity=identity, changed_dimensions=changed, reason_codes=(MaterialityReason.IRREVERSIBILITY_CONTRADICTION,))
        delegated = set(delegated_dimensions)
        if default_dimension in changed and default_dimension in delegated and position.reversible:
            return MaterialityAssessment(disposition="display_default", semantic_identity=identity, changed_dimensions=changed, selected_default_option_id="default")
        return MaterialityAssessment(disposition="ask", semantic_identity=identity, changed_dimensions=changed)
