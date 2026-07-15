"""Task 3 preflight group 7: public models and event catalog (RED first)."""

import hashlib

import pytest
from pydantic import ValidationError


def test_public_models_are_strict_frozen_and_round_trip():
    from orchestrator.workbench.models import (
        FrameCursor,
        FrameState,
        NativeReference,
    )

    ref = NativeReference(provider="claude", account_id="a", profile_id="p", session_id="s", thread_id="t")
    assert ref.model_validate(ref.model_dump()) == ref
    with pytest.raises(ValidationError):
        NativeReference(provider="claude", account_id="a", profile_id="p", session_id="s", thread_id="t", extra="x")
    with pytest.raises((ValidationError, TypeError)):
        ref.provider = "other"
    cursor = FrameCursor(
        task_id="task", branch_id="main", frame_version=0, head_sequence=0,
        head_event_checksum=hashlib.sha256(b"root").hexdigest(),
        frame_state_checksum=hashlib.sha256(b"state").hexdigest(),
    )
    assert cursor.frame_version == 0


def test_public_models_reject_future_event_authority_fields():
    from orchestrator.workbench.models import FrameState, FrameSnapshot

    state = FrameState(task_id="task", branch_id="main", frame_version=0, nodes=(), edges=(), state_checksum="0" * 64)
    with pytest.raises(ValidationError):
        state.model_copy(update={"head_event_id": "future"})
    with pytest.raises(ValidationError):
        FrameSnapshot(state=state, head_sequence=0, head_event_checksum="0" * 64, event_id="future")


def test_closed_reason_and_failure_codes_and_typed_undeclared_mapping():
    from orchestrator.workbench.models import FrameInvalidationReason, Task3FailureCode

    with pytest.raises(ValueError):
        FrameInvalidationReason("invented")
    with pytest.raises(ValueError):
        Task3FailureCode("invented")
    from orchestrator.workbench.failures import UndeclaredOutcomeError

    error = UndeclaredOutcomeError(
        task_id="task", branch_id="main", subject_id="candidate-1",
        materiality_reasons=("missing_dimension",), changed_dimensions=("intent",),
    )
    assert error.failure.code == "undeclared_outcome"


def test_canonical_hash_domains_are_text_independent_and_literal():
    from orchestrator.workbench.models import (
        NativeReference,
        QuestionOccurrence,
        native_question_identity_checksum,
    )

    ref = NativeReference(provider="claude", account_id="a", profile_id="p", session_id="s", thread_id="t")
    first = native_question_identity_checksum(service="claude", run_id="run", native_reference=ref, native_question_id="q")
    second = native_question_identity_checksum(service="claude", run_id="run", native_reference=ref, native_question_id="q")
    assert first == second
    assert first == hashlib.sha256(
        b'{"domain":"workbench.decision.native_question.v1","native_question_id":"q","native_reference":{"account_id":"a","profile_id":"p","provider":"claude","question_group_id":null,"request_id":null,"session_id":"s","thread_id":"t","tool_use_id":null,"turn_id":null},"run_id":"run","service":"claude"}'
    ).hexdigest()


def test_canonical_tuple_order_and_duplicate_rejection():
    from orchestrator.workbench.models import FrameChangeSet, EdgeUpsert

    with pytest.raises(ValidationError):
        FrameChangeSet(node_operations=(), edge_operations=(
            EdgeUpsert(operation_id="x", relation="supports", from_node_key="a", to_node_key="b"),
            EdgeUpsert(operation_id="x", relation="supports", from_node_key="b", to_node_key="a"),
        ))


def test_production_registry_contains_only_task3_v1_catalog_and_intent_owner():
    from orchestrator.workbench.events import EventRegistry, CORE_PARTICIPANT_ID

    registry = EventRegistry.production_task3()
    names = {event_type for event_type, version in registry.definitions() if version == 1}
    expected = {
        "task.created", "branch.forked", "frame.proposal_created", "frame.proposal_rejected",
        "frame.change_confirmed", "branch.activated", "question.declaration_required",
        "question.default_displayed", "question.ignored", "question.native_repeated",
        "decision.enqueued", "decision.source_merged", "decision.queue_reordered",
        "decision.free_form_received", "decision.reply_repeated", "decision.interpretation_proposed",
        "decision.interpretation_rejected", "decision.resolved", "decision.superseded",
        "decision.late_reply_recorded", "service_run.registered", "service_run.receipt_recorded",
        "service_run.state_changed", "evidence.recorded",
    }
    assert names == expected
    assert all(defn.authority_participant in {CORE_PARTICIPANT_ID, "intent_core"} for defn in registry.definitions().values())


def test_every_catalog_payload_is_event_specific_and_rejects_empty_or_cross_event_shape():
    from orchestrator.workbench.events import EventRegistry
    registry = EventRegistry.production_task3()
    for (event_type, version), definition in registry.definitions().items():
        if event_type in {"task.created", "branch.forked"}:
            continue
        with pytest.raises(Exception):
            definition.payload_model.model_validate({})
        with pytest.raises(Exception):
            definition.payload_model.model_validate({"title": "wrong", "initial_branch_id": "main"})
        # Every public payload is closed: a syntactically valid field cannot be
        # smuggled into an unrelated event envelope.
        fields = set(definition.payload_model.model_fields)
        first = next(iter(fields))
        with pytest.raises(Exception):
            definition.payload_model.model_validate({first: None, "unclaimed_extra": True})


def test_representative_payloads_are_literal_and_round_trip_without_future_fields():
    from orchestrator.workbench.events import (
        FrameProposalRejectedPayload, DecisionQueueReorderedPayload,
        ServiceRunReceiptRecordedPayload,
    )
    assert FrameProposalRejectedPayload(
        proposal_id="p", expected_preview_state_checksum="a" * 64, reason="owner_request"
    ).model_dump(mode="json")["proposal_id"] == "p"
    assert DecisionQueueReorderedPayload(
        expected_queue_revision=1, expected_queue_checksum="a" * 64,
        queued_decision_ids=("d1",), resulting_queue={"task_id":"t","branch_id":"b","revision":1,"checksum":"a"*64,"active":None,"queued":()}
    ).expected_queue_revision == 1
    with pytest.raises(ValidationError):
        ServiceRunReceiptRecordedPayload.model_validate({"run_id": "r", "future_event_id": "x"})


def test_task2_canonical_json_rejects_arbitrary_objects_and_nonfinite_values():
    from orchestrator.workbench.events import canonical_json_bytes
    class O: pass
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes({"x": O()})
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes({"x": float("nan")})


def test_strict_run_state_and_reason_enums_reject_coercion_and_unknowns():
    from orchestrator.workbench.events import ServiceRunStateChangedPayload
    with pytest.raises(ValidationError):
        ServiceRunStateChangedPayload(run_id="r", expected_revision="1", expected_state="running", expected_output_validity="current", new_state="complete", resulting_output_validity="current", reason_code="dependency_changed", resulting_task_state="active")
    with pytest.raises(ValidationError):
        ServiceRunStateChangedPayload(run_id="r", expected_revision=1, expected_state="bogus", expected_output_validity="current", new_state="complete", resulting_output_validity="current", reason_code="bogus", resulting_task_state="active")


def test_reply_occurrence_discriminator_requires_matching_member():
    from orchestrator.workbench.models import ReplyOccurrence
    base = {"occurrence_id":"o", "native_reply_checksum":"a"*64, "received_at":"2026-01-01T00:00:00Z", "provenance":{"source_kind":"owner","source_id":"x"}, "classification":"accepted"}
    with pytest.raises(ValidationError):
        ReplyOccurrence.model_validate({**base, "reply_kind":"free_form", "reply":{"option_id":"x", "identity":{"channel":"workbench","provider":"p","account_id":"a","thread_id":"t","message_id":"m","native_reply_id":"n"}}})


def test_missing_structured_resolution_models_are_public():
    from orchestrator.workbench.models import StructuredDecisionInterpretation, DecisionResolution, DecisionResolutionResult
    assert StructuredDecisionInterpretation and DecisionResolution and DecisionResolutionResult


def test_outcome_position_rejects_duplicate_or_wrong_dimension_order():
    from orchestrator.workbench.models import OutcomeDelta, OutcomePosition
    dims = ["intent","success","consequence","authority","cost","owner_visible_ux","external_action","irreversibility"]
    good = {"position_id":"p", "deltas": tuple(OutcomeDelta(dimension=d, baseline=None, candidate=None) for d in dims), "reversible":True}
    assert OutcomePosition.model_validate(good)
    dup = dims[:-1] + ["external_action"]
    with pytest.raises(ValidationError):
        OutcomePosition(position_id="p", deltas=tuple(OutcomeDelta(dimension=d, baseline=None, candidate=None) for d in dup), reversible=True)


def test_tuple_fields_accept_json_roundtrip_lists_but_reject_scalar_coercion():
    from orchestrator.workbench.models import Provenance, NativeReference
    ref = NativeReference(provider="p", account_id="a", profile_id="pr", session_id="s", thread_id="t")
    p = Provenance(source_kind="owner", source_id="x", native_refs=(ref,))
    wire = p.model_dump(mode="json")
    assert Provenance.model_validate(wire) == p
    with pytest.raises(ValidationError):
        NativeReference(provider="p", account_id="a", profile_id="pr", session_id="s", thread_id=1)


def test_all_checksum_fields_reject_uppercase_or_non_hex_even_when_nullable():
    from orchestrator.workbench.models import FrameCursor, FrameSnapshot, FrameState
    with pytest.raises(ValidationError):
        FrameCursor(task_id="t", branch_id="main", frame_version=0, head_sequence=0,
                    head_event_checksum="A" * 64, frame_state_checksum="a" * 64)
    state = FrameState(task_id="t", branch_id="main", frame_version=0, state_checksum="a" * 64)
    with pytest.raises(ValidationError):
        FrameSnapshot(state=state, head_sequence=0, head_event_checksum="g" * 64)


def test_closed_task3_enums_and_nonnegative_queue_fields():
    from orchestrator.workbench.models import DecisionKind, DecisionTier, FrameNodeKind, DecisionRequest
    with pytest.raises(ValueError): DecisionKind("invented")
    with pytest.raises(ValueError): DecisionTier("invented")
    with pytest.raises(ValueError): FrameNodeKind("invented")
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate({"decision_id":"d","task_id":"t","branch_id":"b","revision":0,
            "state":"queued","tier":"routine","kind":"intent_clarification","queue_order":0,
            "semantic_identity":"s","question":"q","options":[],"free_form_allowed":True,
            "changed_outcome":"x","positions":[],"materiality":{"disposition":"ask","semantic_identity":"s"},
            "consequence_if_unresolved":"x","affected_node_keys":[],"provenance":{"source_kind":"owner","source_id":"x"},"source_occurrences":[]})


def test_native_envelope_and_resolution_result_are_xor_shapes():
    from orchestrator.workbench.models import NativeIdentityEnvelope, DecisionResolution, DecisionResolutionResult
    with pytest.raises(ValidationError):
        NativeIdentityEnvelope(adapter_provider="p", adapter_contract_revision="1", account_id="a", profile_id="p", model_id="m", capability_inventory_revision="1", transport_generation="1", native_session_id="s", native_thread_id="t", native_request_id="r")
    with pytest.raises(ValidationError):
        DecisionResolution(resolution_id="r", decision_id="d", position={}, resolving_occurrence_id="o")


def test_event_any_fields_reject_arbitrary_python_objects():
    from orchestrator.workbench.events import EvidenceRecordedPayload
    class Arbitrary: pass
    with pytest.raises((ValidationError, TypeError, ValueError)):
        EvidenceRecordedPayload(evidence_id="e", task_id="t", branch_id="b", success_node_id="n", success_node_frame_version=0,
            frame_version=0, observed_head_event_id="x", observed_head_sequence=0, predicate_id="p", predicate_json=Arbitrary(),
            expected_outcome={"ok": True}, authority_kind="owner", authority_locator="x", verifier_kind="x", verifier_identity="x",
            verification_status="current", observed_at="2026-01-01T00:00:00Z")


def test_enum_models_round_trip_through_json_wire_and_preserve_strictness():
    from orchestrator.workbench.models import (
        DecisionKind, DecisionTier, FrameInvalidationReason, MaterialityAssessment,
        NodeInvalidate, Provenance, ServiceRunState, OutputValidity, RunTransitionReason,
    )

    assessment = MaterialityAssessment(
        disposition="ask", semantic_identity="s",
        reason_codes=(__import__("orchestrator.workbench.models", fromlist=["MaterialityReason"]).MaterialityReason.MISSING_DIMENSION,),
    )
    wire = assessment.model_dump(mode="json")
    assert MaterialityAssessment.model_validate(wire) == assessment
    with pytest.raises(ValidationError):
        MaterialityAssessment.model_validate({**wire, "reason_codes": ["not-a-reason"]})
    with pytest.raises(ValidationError):
        MaterialityAssessment.model_validate({**wire, "reason_codes": [1]})

    node = NodeInvalidate(
        operation_id="op", node_key="n", expected_current_node_id="old", new_node_id="new",
        reason_code=FrameInvalidationReason.OWNER_CORRECTION,
        provenance=Provenance(source_kind="owner", source_id="x"),
    )
    assert NodeInvalidate.model_validate(node.model_dump(mode="json")) == node

    from orchestrator.workbench.events import ServiceRunStateChangedPayload
    payload = ServiceRunStateChangedPayload(
        run_id="r", expected_revision=1, expected_state=ServiceRunState.RUNNING,
        expected_output_validity=OutputValidity.CURRENT, new_state=ServiceRunState.COMPLETE,
        resulting_output_validity=OutputValidity.CURRENT,
        reason_code=RunTransitionReason.DEPENDENCY_CHANGED,
        resulting_task_state="active",
    )
    assert ServiceRunStateChangedPayload.model_validate(payload.model_dump(mode="json")) == payload
    with pytest.raises(ValidationError):
        ServiceRunStateChangedPayload.model_validate({**payload.model_dump(mode="json"), "new_state": 1})


def test_json_wire_event_validation_accepts_enum_strings_but_not_coerced_scalars():
    from orchestrator.workbench.events import EventRegistry, ServiceRunStateChangedPayload
    from orchestrator.workbench.models import EventValidationError
    from orchestrator.workbench.models import EventActor, EventDraft

    payload = ServiceRunStateChangedPayload(
        run_id="r", expected_revision=1, expected_state="running",
        expected_output_validity="current", new_state="complete",
        resulting_output_validity="current", reason_code="dependency_changed",
        resulting_task_state="active",
    )
    draft = EventDraft(
        event_type="service_run.state_changed", actor=EventActor(kind="system", actor_id="core"),
        payload=payload.model_dump(mode="json"),
    )
    validated = EventRegistry.production_task3().validate(draft)
    assert validated.payload == payload
    bad = dict(draft.payload)
    bad["new_state"] = 1
    with pytest.raises((ValidationError, EventValidationError)):
        EventRegistry.production_task3().validate(draft.model_copy(update={"payload": bad}))


def test_decision_queue_revision_is_nonnegative():
    from orchestrator.workbench.models import DecisionQueueSnapshot
    with pytest.raises(ValidationError):
        DecisionQueueSnapshot(task_id="t", branch_id="b", revision=-1, checksum="a" * 64)


def test_run_transition_reason_is_the_closed_normative_matrix():
    from orchestrator.workbench.models import RunTransitionReason
    expected = {
        "dispatch_started", "provider_attached", "owner_input_required", "owner_input_received",
        "dependency_changed", "dependency_restored", "repair_started", "retry_started",
        "recovery_reconciled", "verification_started", "service_interrupted", "service_completed",
        "service_failed", "owner_canceled", "output_staled", "missing_input_manifest",
        "reverification_required",
    }
    assert {item.value for item in RunTransitionReason} == expected
    for value in expected:
        assert RunTransitionReason(value).value == value


def test_decision_payloads_require_structured_typed_members_and_round_trip():
    from orchestrator.workbench.events import DecisionInterpretationProposedPayload, DecisionResolvedPayload
    from orchestrator.workbench.models import DecisionResolution, StructuredDecisionInterpretation
    with pytest.raises(ValidationError):
        DecisionInterpretationProposedPayload(decision_id="d", interpretation={"arbitrary": True}, expected_decision_revision=1, expected_queue_revision=1, resulting_decision_revision=2, resulting_queue={})
    with pytest.raises(ValidationError):
        DecisionResolvedPayload(decision_id="d", occurrence={}, resolution={"arbitrary": True}, expected_decision_revision=1, expected_queue_revision=1, resulting_decision={}, resulting_queue={}, resulting_task_state="active")
    assert DecisionInterpretationProposedPayload.model_fields["interpretation"].annotation is StructuredDecisionInterpretation
    assert DecisionResolvedPayload.model_fields["resolution"].annotation is DecisionResolution


def test_task3_failure_surface_is_closed_discriminated_and_mismatch_safe():
    from orchestrator.workbench.failures import (
        FailureSubject, StaleFrameCursorDetails, Task3Failure,
        Task3FailureCode,
    )
    assert len(tuple(Task3FailureCode)) == 19
    detail = StaleFrameCursorDetails(
        code="stale_frame_cursor", predicate_id="head", expected_head_sequence=2,
        expected_frame_version=1, actual_head_sequence=3, actual_frame_version=2,
    )
    failure = Task3Failure(
        schema_version=1, code="stale_frame_cursor", task_id="task", branch_id="main",
        subject=FailureSubject(kind="event", id="event-3"), details=detail, message="stale",
    )
    assert Task3Failure.model_validate_json(failure.model_dump_json()) == failure
    with pytest.raises(ValidationError):
        Task3Failure.model_validate({**failure.model_dump(mode="python"), "code": "dependency_cycle"})
    with pytest.raises(ValidationError):
        StaleFrameCursorDetails.model_validate({**detail.model_dump(mode="python"), "extra": True})


def test_service_run_registered_output_validity_is_closed_enum():
    from orchestrator.workbench.events import ServiceRunRegisteredPayload
    from orchestrator.workbench.models import OutputValidity

    payload = ServiceRunRegisteredPayload(
        run_id="r", service="svc", role="worker", task_id="t", branch_id="main",
        frame_version=0, input_manifest={"x": 1}, input_node_ids=(),
        initial_state="queued", output_validity=OutputValidity.CURRENT,
        resulting_task_state="active",
    )
    assert payload.output_validity is OutputValidity.CURRENT
    with pytest.raises(ValidationError):
        ServiceRunRegisteredPayload.model_validate({**payload.model_dump(mode="json"), "output_validity": "invented"})
    with pytest.raises(ValidationError):
        ServiceRunRegisteredPayload.model_validate({**payload.model_dump(mode="json"), "output_validity": 1})


def test_impact_materiality_and_outcome_reference_tuples_are_sorted_unique():
    from orchestrator.workbench.models import CorrectionImpact, MaterialityAssessment, OutcomePosition, OutcomeDelta

    with pytest.raises(ValidationError):
        CorrectionImpact(superseded_node_ids=("b", "a"))
    with pytest.raises(ValidationError):
        CorrectionImpact(superseded_node_ids=("a", "a"))
    with pytest.raises(ValidationError):
        CorrectionImpact(irreversible_external_action_refs=("x", "x"))
    with pytest.raises(ValidationError):
        MaterialityAssessment(disposition="ask", semantic_identity="s", changed_dimensions=("success", "intent"))
    with pytest.raises(ValidationError):
        MaterialityAssessment(disposition="ask", semantic_identity="s", reason_codes=("missing_dimension", "missing_dimension"))
    dims = ("intent", "success", "consequence", "authority", "cost", "owner_visible_ux", "external_action", "irreversibility")
    with pytest.raises(ValidationError):
        OutcomePosition(
            position_id="p", deltas=tuple(OutcomeDelta(dimension=d, baseline=None, candidate=None) for d in dims),
            reversible=True, external_action_refs=("b", "a"),
        )
    with pytest.raises(ValidationError):
        OutcomePosition(
            position_id="p", deltas=tuple(OutcomeDelta(dimension=d, baseline=None, candidate=None) for d in dims),
            reversible=True, external_action_authority_node_ids=("n", "n"),
        )


def test_failure_detail_tuples_and_undeclared_error_are_typed_and_canonical():
    from orchestrator.workbench.failures import (
        FailureSubject, Task3Failure, UndeclaredOutcomeDetails, UndeclaredOutcomeError,
        UnauthenticFrameBaseDetails, TaskCacheConflictDetails,
    )

    with pytest.raises(ValidationError):
        UnauthenticFrameBaseDetails(code="unauthentic_frame_base", predicate_id="p", base_frame_version=0,
                                    claimed_state_checksum="a" * 64, missing_event_ids=("e2", "e1"))
    with pytest.raises(ValidationError):
        TaskCacheConflictDetails(code="task_cache_conflict", task_id="t", predicate_id="p",
                                 mismatched_fields=("z", "a"), expected_cache={"expected_selected_branch_id":"main", "expected_frame_version":0, "expected_state":"active", "expected_updated_at":"2026-01-01T00:00:00Z", "expected_last_transition_event_id":None})
    with pytest.raises(ValidationError):
        UndeclaredOutcomeDetails(code="undeclared_outcome", materiality_reasons=("missing_dimension", "duplicate_dimension"), changed_dimensions=("success", "intent"))

    error = UndeclaredOutcomeError(task_id="t", branch_id="main", subject_id="candidate-1",
                                   materiality_reasons=("missing_dimension",), changed_dimensions=("intent",))
    assert isinstance(error.failure, Task3Failure)
    assert isinstance(error.failure.details, UndeclaredOutcomeDetails)
    assert error.failure.subject == FailureSubject(kind="candidate", id="candidate-1")
    assert error.failure.details.materiality_reasons == ("missing_dimension",)


def test_task3_models_bind_cross_field_authority_and_canonical_order():
    from orchestrator.workbench.models import (
        CorrectionImpact, DecisionQueueSnapshot, DecisionRequest, DecisionOption,
        DecisionKind, DecisionTier, MaterialityAssessment, NativeIdentityEnvelope,
        NativeQuestionCandidate, NativeReference, OutcomeDelta, OutcomePosition,
        Provenance, QuestionOccurrence, RunImpact,
    )

    with pytest.raises(ValidationError):
        NativeIdentityEnvelope(adapter_provider=" ", adapter_contract_revision="1", account_id="a", profile_id="p", model_id="m", capability_inventory_revision="1", transport_generation="1", native_session_id="s", native_thread_id="t")

    ref = NativeReference(provider="p", account_id="a", profile_id="pr", session_id="s", thread_id="t")
    prov = Provenance(source_kind="owner", source_id="owner", native_refs=(ref,))
    q = QuestionOccurrence(occurrence_id="q1", service="svc", native_reference=ref, native_question_id="nq", native_question_identity_checksum="a"*64, question_content_checksum="b"*64, question_text="q", option_labels=(), received_at="2026-01-01T00:00:00Z", provenance=prov, classification="accepted")
    deltas = tuple(OutcomeDelta(dimension=d, baseline=None, candidate=None) for d in ("intent","success","consequence","authority","cost","owner_visible_ux","external_action","irreversibility"))
    pos = OutcomePosition(position_id="pos", deltas=deltas, reversible=True)
    request = DecisionRequest(decision_id="d1", task_id="task", branch_id="main", revision=1, state="active", tier=DecisionTier.ROUTINE, kind=DecisionKind.INTENT_CLARIFICATION, queue_order=0, semantic_identity="sid", question="q", options=(DecisionOption(option_id="o", label="o", description="o", position_id="pos"),), free_form_allowed=False, changed_outcome="x", positions=(pos,), materiality=MaterialityAssessment(disposition="ask", semantic_identity="sid"), consequence_if_unresolved="x", affected_node_keys=("a",), provenance=prov, source_occurrences=(q,))
    with pytest.raises(ValidationError):
        DecisionQueueSnapshot(task_id="forged", branch_id="main", revision=1, checksum="a"*64, active=request)
    with pytest.raises(ValidationError):
        DecisionQueueSnapshot(task_id="task", branch_id="main", revision=1, checksum="a"*64, active=None, queued=(request.model_copy(update={"state":"queued", "queue_order":1}),))
    with pytest.raises(ValidationError):
        DecisionQueueSnapshot(task_id="task", branch_id="main", revision=0, checksum="a"*64, active=None)
    with pytest.raises(ValidationError):
        NativeQuestionCandidate(candidate_id="c", kind=DecisionKind.INTENT_CLARIFICATION, question="q", options=(), free_form_allowed=True, changed_outcome="x", consequence_if_unresolved="x", affected_node_keys=("z", "a"), tier=DecisionTier.ROUTINE, positions=(pos,), source=q)
    with pytest.raises(ValidationError):
        CorrectionImpact(run_impacts=(RunImpact(run_id="z", expected_revision=1, expected_state="running", expected_output_validity="current", expected_last_event_id="e", resulting_state="complete", resulting_output_validity="current", reason_code="service_completed", action="repairing"), RunImpact(run_id="a", expected_revision=1, expected_state="running", expected_output_validity="current", expected_last_event_id="e", resulting_state="complete", resulting_output_validity="current", reason_code="service_completed", action="repairing")))


def test_task3_identity_chain_rejects_blank_optional_ids_and_provenance_source():
    from pydantic import ValidationError
    from orchestrator.workbench.models import NativeIdentityEnvelope, NativeReference, Provenance

    required = dict(provider="p", account_id="a", profile_id="pr", session_id="s", thread_id="t")
    for field in ("turn_id", "request_id", "tool_use_id", "question_group_id"):
        with pytest.raises(ValidationError):
            NativeReference(**required, **{field: " "})
    # Optional-chain semantics remain intact: omitted descendants are valid.
    ref = NativeReference(**required)
    assert ref.turn_id is None
    assert NativeReference.model_validate_json(ref.model_dump_json()) == ref

    envelope_required = dict(
        adapter_provider="p", adapter_contract_revision="1", account_id="a", profile_id="pr",
        model_id="m", capability_inventory_revision="1", transport_generation="1",
        native_session_id="s", native_thread_id="t",
    )
    for field in ("native_turn_id", "native_request_id", "native_tool_use_id", "native_question_group_id"):
        with pytest.raises(ValidationError):
            NativeIdentityEnvelope(**envelope_required, **{field: ""})
    envelope = NativeIdentityEnvelope(**envelope_required)
    assert envelope.native_turn_id is None
    assert NativeIdentityEnvelope.model_validate_json(envelope.model_dump_json()) == envelope

    with pytest.raises(ValidationError):
        Provenance(source_kind="owner", source_id=" ")
    provenance = Provenance(source_kind="owner", source_id="owner")
    assert Provenance.model_validate_json(provenance.model_dump_json()) == provenance


def test_task3_failure_subject_kind_is_bound_to_code():
    from orchestrator.workbench.failures import FailureSubject, StaleFrameCursorDetails, Task3Failure
    detail = StaleFrameCursorDetails(code="stale_frame_cursor", predicate_id="head", expected_head_sequence=1, expected_frame_version=0, actual_head_sequence=2, actual_frame_version=0)
    with pytest.raises(ValidationError):
        Task3Failure(schema_version=1, code="stale_frame_cursor", task_id="task", branch_id="main", subject=FailureSubject(kind="decision", id="d"), details=detail, message="stale")
