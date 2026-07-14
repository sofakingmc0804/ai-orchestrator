-- Migration 4 installs the first event-backed Task-3 authority boundary.
-- No accepted Task-3 writer predates this migration, so any preexisting row
-- must fail closed rather than being backfilled or silently authenticated.

CREATE TABLE task3_authority_upgrade_guard(marker INTEGER NOT NULL) STRICT;

CREATE TRIGGER task3_authority_upgrade_requires_empty_authority
BEFORE INSERT ON task3_authority_upgrade_guard
WHEN EXISTS (SELECT 1 FROM frame_nodes LIMIT 1)
  OR EXISTS (SELECT 1 FROM frame_edges LIMIT 1)
  OR EXISTS (SELECT 1 FROM frame_proposals LIMIT 1)
  OR EXISTS (SELECT 1 FROM decision_requests LIMIT 1)
  OR EXISTS (SELECT 1 FROM service_runs LIMIT 1)
  OR EXISTS (SELECT 1 FROM service_run_inputs LIMIT 1)
  OR EXISTS (SELECT 1 FROM evidence_refs LIMIT 1)
BEGIN
    SELECT RAISE(ABORT, 'migration_precondition:4:preexisting_task3_authority_without_events');
END;

INSERT INTO task3_authority_upgrade_guard(marker) VALUES (1);
DROP TRIGGER task3_authority_upgrade_requires_empty_authority;
DROP TABLE task3_authority_upgrade_guard;

ALTER TABLE workbench_tasks ADD COLUMN last_transition_event_id TEXT;
ALTER TABLE workbench_branches ADD COLUMN last_transition_event_id TEXT;

UPDATE workbench_tasks
SET last_transition_event_id = (
    SELECT event.event_id
    FROM workbench_events event
    WHERE event.task_id = workbench_tasks.id
      AND event.event_type = 'task.created'
      AND event.sequence = 1
      AND event.frame_version = 0
      AND event.branch_id = workbench_tasks.active_branch_id
      AND event.created_at = workbench_tasks.created_at
      AND json_extract(event.payload_json, '$.title') = workbench_tasks.title
      AND json_extract(event.payload_json, '$.initial_branch_id') = workbench_tasks.active_branch_id
      AND event.payload_json = json_object(
          'initial_branch_id',workbench_tasks.active_branch_id,
          'title',workbench_tasks.title
      )
    ORDER BY event.sequence
    LIMIT 1
)
WHERE EXISTS (
    SELECT 1 FROM workbench_events event
    WHERE event.task_id = workbench_tasks.id
      AND event.event_type = 'task.created'
      AND event.sequence = 1
      AND event.frame_version = 0
      AND event.branch_id = workbench_tasks.active_branch_id
      AND event.created_at = workbench_tasks.created_at
      AND json_extract(event.payload_json, '$.title') = workbench_tasks.title
      AND json_extract(event.payload_json, '$.initial_branch_id') = workbench_tasks.active_branch_id
      AND event.payload_json = json_object(
          'initial_branch_id',workbench_tasks.active_branch_id,
          'title',workbench_tasks.title
      )
);

UPDATE workbench_branches
SET last_transition_event_id = CASE
    WHEN parent_branch_id IS NULL THEN (
        SELECT event.event_id
        FROM workbench_events event
        WHERE event.task_id = workbench_branches.task_id
          AND event.branch_id = workbench_branches.branch_id
          AND event.event_type = 'task.created'
          AND event.sequence = 1
          AND event.frame_version = 0
          AND event.created_at = workbench_branches.created_at
          AND json_extract(event.payload_json, '$.title') = (
              SELECT task.title FROM workbench_tasks task
              WHERE task.id = workbench_branches.task_id
          )
          AND json_extract(event.payload_json, '$.initial_branch_id') = workbench_branches.branch_id
          AND event.payload_json = json_object(
              'initial_branch_id',workbench_branches.branch_id,
              'title',(
                  SELECT task.title FROM workbench_tasks task
                  WHERE task.id = workbench_branches.task_id
              )
          )
        ORDER BY event.sequence
        LIMIT 1
    )
    ELSE created_by_event_id
END
WHERE (parent_branch_id IS NULL AND EXISTS (
          SELECT 1 FROM workbench_events event
          WHERE event.task_id = workbench_branches.task_id
            AND event.branch_id = workbench_branches.branch_id
            AND event.event_type = 'task.created'
            AND event.sequence = 1
            AND event.frame_version = 0
            AND event.created_at = workbench_branches.created_at
            AND json_extract(event.payload_json, '$.title') = (
                SELECT task.title FROM workbench_tasks task
                WHERE task.id = workbench_branches.task_id
            )
            AND json_extract(event.payload_json, '$.initial_branch_id') = workbench_branches.branch_id
            AND event.payload_json = json_object(
                'initial_branch_id',workbench_branches.branch_id,
                'title',(
                    SELECT task.title FROM workbench_tasks task
                    WHERE task.id = workbench_branches.task_id
                )
            )
      ))
   OR (parent_branch_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM workbench_events event
          WHERE event.task_id = workbench_branches.task_id
            AND event.event_id = workbench_branches.created_by_event_id
            AND event.event_type = 'branch.forked'
            AND event.branch_id = workbench_branches.parent_branch_id
            AND event.frame_version = workbench_branches.forked_from_frame_version
            AND event.sequence > workbench_branches.forked_from_sequence
            AND event.created_at = workbench_branches.created_at
            AND json_extract(event.payload_json, '$.new_branch_id') = workbench_branches.branch_id
            AND json_extract(event.payload_json, '$.parent_branch_id') = workbench_branches.parent_branch_id
            AND json_extract(event.payload_json, '$.forked_from_sequence') = workbench_branches.forked_from_sequence
            AND json_extract(event.payload_json, '$.forked_from_frame_version') = workbench_branches.forked_from_frame_version
            AND event.payload_json = json_object(
                'forked_from_frame_version',workbench_branches.forked_from_frame_version,
                'forked_from_sequence',workbench_branches.forked_from_sequence,
                'new_branch_id',workbench_branches.branch_id,
                'parent_branch_id',workbench_branches.parent_branch_id
            )
      ));

DROP INDEX idx_decision_requests_open_queue_order;
DROP INDEX idx_decision_requests_one_active;
DROP INDEX idx_decision_requests_active_identity;
DROP TABLE decision_requests;

CREATE TABLE decision_requests (
    decision_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 1),
    last_event_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','active','resolved','superseded')),
    tier TEXT NOT NULL CHECK (tier IN ('routine','blocking','critical')),
    kind TEXT NOT NULL CHECK (kind IN (
        'intent_clarification','tool_approval','external_action_approval','evidence_checkpoint',
        'service_team_override','frame_interpretation_confirmation'
    )),
    queue_order INTEGER NOT NULL CHECK (typeof(queue_order) = 'integer' AND queue_order >= 0),
    semantic_identity TEXT NOT NULL CHECK (
        typeof(semantic_identity) = 'text' AND length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    question TEXT NOT NULL,
    options_json TEXT NOT NULL CHECK (
        typeof(options_json) = 'text' AND json_valid(options_json)
        AND options_json = json(options_json) AND json_type(options_json) = 'array'
    ),
    free_form_allowed INTEGER NOT NULL CHECK (
        typeof(free_form_allowed) = 'integer' AND free_form_allowed IN (0,1)
    ),
    recommendation_option_id TEXT,
    recommendation_reason TEXT,
    changed_outcome TEXT NOT NULL,
    positions_json TEXT NOT NULL CHECK (
        typeof(positions_json) = 'text' AND json_valid(positions_json)
        AND positions_json = json(positions_json) AND json_type(positions_json) = 'array'
    ),
    materiality_json TEXT NOT NULL CHECK (
        typeof(materiality_json) = 'text' AND json_valid(materiality_json)
        AND materiality_json = json(materiality_json) AND json_type(materiality_json) = 'object'
    ),
    consequence_if_unresolved TEXT NOT NULL,
    affected_node_keys_json TEXT NOT NULL CHECK (
        typeof(affected_node_keys_json) = 'text' AND json_valid(affected_node_keys_json)
        AND affected_node_keys_json = json(affected_node_keys_json)
        AND json_type(affected_node_keys_json) = 'array'
    ),
    provenance_json TEXT NOT NULL CHECK (
        typeof(provenance_json) = 'text' AND json_valid(provenance_json)
        AND provenance_json = json(provenance_json) AND json_type(provenance_json) = 'object'
    ),
    source_occurrences_json TEXT NOT NULL CHECK (
        typeof(source_occurrences_json) = 'text' AND json_valid(source_occurrences_json)
        AND source_occurrences_json = json(source_occurrences_json)
        AND json_type(source_occurrences_json) = 'array'
    ),
    pending_interpretation_json TEXT CHECK (
        pending_interpretation_json IS NULL OR (
            typeof(pending_interpretation_json) = 'text'
            AND json_valid(pending_interpretation_json)
            AND pending_interpretation_json = json(pending_interpretation_json)
            AND json_type(pending_interpretation_json) = 'object'
        )
    ),
    resolution_json TEXT CHECK (
        resolution_json IS NULL OR (
            typeof(resolution_json) = 'text' AND json_valid(resolution_json)
            AND resolution_json = json(resolution_json) AND json_type(resolution_json) = 'object'
        )
    ),
    created_by_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE (task_id, decision_id),
    CHECK (pending_interpretation_json IS NULL OR resolution_json IS NULL),
    CHECK ((state = 'resolved') = (resolution_json IS NOT NULL)),
    CHECK ((state IN ('resolved','superseded')) = (decided_at IS NOT NULL)),
    CHECK ((recommendation_option_id IS NULL) = (recommendation_reason IS NULL)),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, created_by_event_id) REFERENCES workbench_events(task_id, event_id),
    FOREIGN KEY (task_id, last_event_id) REFERENCES workbench_events(task_id, event_id)
) STRICT;

CREATE UNIQUE INDEX idx_decision_requests_active_identity
ON decision_requests(task_id, branch_id, semantic_identity)
WHERE state IN ('queued','active');

CREATE UNIQUE INDEX idx_decision_requests_one_active
ON decision_requests(task_id, branch_id)
WHERE state = 'active';

DROP TABLE service_run_inputs;
DROP INDEX idx_service_runs_open_native_request;
DROP INDEX idx_service_runs_native_question_identity;
DROP INDEX idx_service_runs_native_tool_identity;
DROP INDEX idx_service_runs_native_request_identity;
DROP INDEX idx_service_runs_native_session_identity;
DROP INDEX idx_service_runs_task_branch_state;
DROP TABLE service_runs;

CREATE TABLE service_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    service TEXT NOT NULL CHECK (service IN ('claude','codex','hermes','gemini','local')),
    role TEXT NOT NULL,
    frame_version INTEGER NOT NULL CHECK (typeof(frame_version) = 'integer' AND frame_version >= 0),
    adapter_provider TEXT,
    adapter_contract_revision TEXT,
    account_id TEXT,
    profile_id TEXT,
    model_id TEXT,
    capability_inventory_revision TEXT,
    transport_generation TEXT,
    native_session_id TEXT,
    native_thread_id TEXT,
    native_turn_id TEXT,
    native_request_id TEXT,
    native_tool_use_id TEXT,
    native_question_group_id TEXT,
    launch_origin TEXT CHECK (launch_origin IS NULL OR launch_origin = 'governed'),
    native_handle_json TEXT CHECK (
        native_handle_json IS NULL OR (
            typeof(native_handle_json) = 'text' AND json_valid(native_handle_json)
            AND native_handle_json = json(native_handle_json)
        )
    ),
    branch_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'queued','starting','running','waiting_owner','paused_dependency','repairing','verifying',
        'interrupted','complete','failed','canceled'
    )),
    receipt_event_id TEXT,
    input_manifest_json TEXT NOT NULL CHECK (
        typeof(input_manifest_json) = 'text' AND json_valid(input_manifest_json)
        AND input_manifest_json = json(input_manifest_json)
    ),
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 1),
    last_event_id TEXT NOT NULL,
    output_validity TEXT NOT NULL CHECK (output_validity IN ('current','stale','reverification_required')),
    UNIQUE (task_id, run_id),
    UNIQUE (task_id, run_id, frame_version),
    UNIQUE (task_id, run_id, branch_id, frame_version),
    CHECK (
        (adapter_provider IS NULL AND adapter_contract_revision IS NULL AND account_id IS NULL
         AND profile_id IS NULL AND model_id IS NULL AND capability_inventory_revision IS NULL
         AND transport_generation IS NULL AND native_session_id IS NULL AND native_thread_id IS NULL
         AND native_turn_id IS NULL AND native_request_id IS NULL AND native_tool_use_id IS NULL
         AND native_question_group_id IS NULL AND launch_origin IS NULL AND native_handle_json IS NULL)
        OR
        (length(trim(adapter_provider)) > 0 AND length(trim(adapter_contract_revision)) > 0
         AND length(trim(account_id)) > 0 AND length(trim(profile_id)) > 0
         AND length(trim(model_id)) > 0 AND length(trim(capability_inventory_revision)) > 0
         AND length(trim(transport_generation)) > 0 AND length(trim(native_session_id)) > 0
         AND length(trim(native_thread_id)) > 0 AND launch_origin = 'governed')
    ),
    CHECK (native_request_id IS NULL OR native_turn_id IS NOT NULL),
    CHECK (native_tool_use_id IS NULL OR native_request_id IS NOT NULL),
    CHECK (native_question_group_id IS NULL OR native_request_id IS NOT NULL),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, receipt_event_id) REFERENCES workbench_events(task_id, event_id),
    FOREIGN KEY (task_id, last_event_id) REFERENCES workbench_events(task_id, event_id)
) STRICT;

CREATE INDEX idx_service_runs_task_branch_state
ON service_runs(task_id, branch_id, state);
CREATE INDEX idx_service_runs_native_session_identity
ON service_runs(adapter_provider, account_id, profile_id, native_session_id)
WHERE native_session_id IS NOT NULL;
CREATE UNIQUE INDEX idx_service_runs_native_request_identity
ON service_runs(adapter_provider,account_id,profile_id,native_session_id,native_thread_id,native_turn_id,native_request_id)
WHERE native_request_id IS NOT NULL;
CREATE UNIQUE INDEX idx_service_runs_native_tool_identity
ON service_runs(adapter_provider,account_id,profile_id,native_session_id,native_thread_id,native_turn_id,native_request_id,native_tool_use_id)
WHERE native_tool_use_id IS NOT NULL;
CREATE UNIQUE INDEX idx_service_runs_native_question_identity
ON service_runs(adapter_provider,account_id,profile_id,native_session_id,native_thread_id,native_turn_id,native_request_id,native_question_group_id)
WHERE native_question_group_id IS NOT NULL;
CREATE INDEX idx_service_runs_open_native_request
ON service_runs(adapter_provider,account_id,profile_id,model_id,native_thread_id,native_turn_id,native_request_id,state)
WHERE native_request_id IS NOT NULL
  AND state IN ('starting','running','waiting_owner','paused_dependency','repairing','verifying','interrupted');

CREATE TABLE service_run_inputs (
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    input_frame_version INTEGER NOT NULL CHECK (
        typeof(input_frame_version) = 'integer' AND input_frame_version >= 0
    ),
    ordinal INTEGER NOT NULL CHECK (typeof(ordinal) = 'integer' AND ordinal >= 0),
    PRIMARY KEY (run_id, node_id),
    UNIQUE (run_id, ordinal),
    FOREIGN KEY (task_id, run_id) REFERENCES service_runs(task_id, run_id),
    FOREIGN KEY (task_id, run_id, input_frame_version)
        REFERENCES service_runs(task_id, run_id, frame_version),
    FOREIGN KEY (task_id, node_id) REFERENCES frame_nodes(task_id, node_id)
) STRICT;

CREATE INDEX idx_service_run_inputs_node ON service_run_inputs(node_id, run_id);

ALTER TABLE frame_proposals RENAME TO frame_proposals_v2_retired;

CREATE TABLE frame_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    base_version INTEGER NOT NULL CHECK (typeof(base_version) = 'integer' AND base_version >= 0),
    proposed_version INTEGER NOT NULL CHECK (
        typeof(proposed_version) = 'integer' AND proposed_version = base_version + 1
    ),
    expected_head_sequence INTEGER NOT NULL CHECK (
        typeof(expected_head_sequence) = 'integer' AND expected_head_sequence >= 0
    ),
    base_head_checksum TEXT NOT NULL CHECK (
        length(base_head_checksum) = 64 AND base_head_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    base_state_checksum TEXT NOT NULL CHECK (
        length(base_state_checksum) = 64 AND base_state_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    preview_state_checksum TEXT NOT NULL CHECK (
        length(preview_state_checksum) = 64 AND preview_state_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    impact_preview_checksum TEXT NOT NULL CHECK (
        length(impact_preview_checksum) = 64 AND impact_preview_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected','superseded')),
    changes_json TEXT NOT NULL CHECK (
        json_valid(changes_json) AND changes_json = json(changes_json) AND json_type(changes_json) = 'object'
    ),
    impact_preview_json TEXT NOT NULL CHECK (
        json_valid(impact_preview_json) AND impact_preview_json = json(impact_preview_json)
        AND json_type(impact_preview_json) = 'object'
    ),
    provenance_json TEXT NOT NULL CHECK (
        json_valid(provenance_json) AND provenance_json = json(provenance_json)
        AND json_type(provenance_json) = 'object'
    ),
    created_by TEXT NOT NULL,
    created_by_event_id TEXT NOT NULL,
    decided_by_event_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE (task_id, id),
    CHECK ((status = 'pending') = (decided_by_event_id IS NULL AND decided_at IS NULL)),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, created_by_event_id) REFERENCES workbench_events(task_id, event_id),
    FOREIGN KEY (task_id, decided_by_event_id) REFERENCES workbench_events(task_id, event_id)
) STRICT;

DROP TABLE frame_proposals_v2_retired;

CREATE TABLE decision_queue_heads (
    task_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 1),
    active_decision_id TEXT,
    ordered_open_ids_json TEXT NOT NULL CHECK (
        typeof(ordered_open_ids_json) = 'text' AND json_valid(ordered_open_ids_json)
        AND ordered_open_ids_json = json(ordered_open_ids_json)
        AND json_type(ordered_open_ids_json) = 'array'
    ),
    queue_checksum TEXT NOT NULL CHECK (
        typeof(queue_checksum) = 'text' AND length(queue_checksum) = 64
        AND queue_checksum NOT GLOB '*[^0-9a-f]*'
    ),
    last_event_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, branch_id),
    CHECK (
        (active_decision_id IS NULL AND json_array_length(ordered_open_ids_json) = 0)
        OR (active_decision_id IS NOT NULL AND json_array_length(ordered_open_ids_json) > 0
            AND json_extract(ordered_open_ids_json, '$[0]') = active_decision_id)
    ),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, active_decision_id) REFERENCES decision_requests(task_id, decision_id),
    FOREIGN KEY (task_id, last_event_id) REFERENCES workbench_events(task_id, event_id)
) STRICT;

CREATE UNIQUE INDEX idx_evidence_refs_task_id
ON evidence_refs(task_id, id);

CREATE TABLE evidence_invalidation_overlays (
    task_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    invalidation_branch_id TEXT NOT NULL,
    invalidated_at TEXT NOT NULL,
    invalidated_by_event_id TEXT NOT NULL,
    invalidated_by_event_sequence INTEGER NOT NULL CHECK (
        typeof(invalidated_by_event_sequence) = 'integer' AND invalidated_by_event_sequence > 0
    ),
    invalidation_reason TEXT NOT NULL,
    PRIMARY KEY (task_id, evidence_id, invalidation_branch_id),
    FOREIGN KEY (task_id, evidence_id) REFERENCES evidence_refs(task_id, id),
    FOREIGN KEY (task_id, invalidation_branch_id)
        REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, invalidated_by_event_id, invalidated_by_event_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence)
) STRICT;

DROP TRIGGER evidence_refs_only_complete_invalidation_update;
DROP TRIGGER evidence_refs_validate_invalidation_update;

CREATE TRIGGER frame_edges_no_update
BEFORE UPDATE ON frame_edges
BEGIN
    SELECT RAISE(ABORT, 'frame edges are immutable');
END;

CREATE TRIGGER frame_edges_no_delete
BEFORE DELETE ON frame_edges
BEGIN
    SELECT RAISE(ABORT, 'frame edges are immutable');
END;

CREATE TRIGGER service_run_inputs_no_update
BEFORE UPDATE ON service_run_inputs
BEGIN
    SELECT RAISE(ABORT, 'service run inputs are immutable');
END;

CREATE TRIGGER service_run_inputs_no_delete
BEFORE DELETE ON service_run_inputs
BEGIN
    SELECT RAISE(ABORT, 'service run inputs are immutable');
END;

CREATE TRIGGER evidence_refs_task3_validate_insert
BEFORE INSERT ON evidence_refs
WHEN NEW.invalidated_at IS NOT NULL
  OR NEW.invalidated_event_id IS NOT NULL
  OR NEW.invalidated_event_sequence IS NOT NULL
  OR NEW.invalidation_reason IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Task-3 evidence source rows must begin current');
END;

CREATE TRIGGER evidence_refs_task3_no_update
BEFORE UPDATE ON evidence_refs
BEGIN
    SELECT RAISE(ABORT, 'Task-3 evidence source rows are immutable');
END;

CREATE TRIGGER frame_proposals_task3_validate_insert
BEFORE INSERT ON frame_proposals
BEGIN
    SELECT CASE WHEN NEW.status <> 'pending'
        THEN RAISE(ABORT, 'frame proposals must begin pending') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.created_by_event_id
          AND event.event_type = 'frame.proposal_created'
          AND event.created_at = NEW.created_at
          AND NEW.created_by = event.actor_kind || ':' || event.actor_id
          AND json_type(event.payload_json, '$.proposal') = 'object'
          AND json_extract(event.payload_json, '$.proposal.proposal_id') = NEW.id
          AND json_extract(event.payload_json, '$.proposal.base.task_id') = NEW.task_id
          AND json_extract(event.payload_json, '$.proposal.base.branch_id') = NEW.branch_id
          AND json_extract(event.payload_json, '$.proposal.base.frame_version') = NEW.base_version
          AND json_extract(event.payload_json, '$.proposal.base.head_sequence') = NEW.expected_head_sequence
          AND json_extract(event.payload_json, '$.proposal.base.head_event_checksum') = NEW.base_head_checksum
          AND json_extract(event.payload_json, '$.proposal.base.frame_state_checksum') = NEW.base_state_checksum
          AND json_extract(event.payload_json, '$.proposal.proposed_version') = NEW.proposed_version
          AND event.payload_json -> '$.proposal.changes' = NEW.changes_json
          AND json_extract(event.payload_json, '$.proposal.preview_state_checksum') = NEW.preview_state_checksum
          AND event.payload_json -> '$.proposal.impact' = NEW.impact_preview_json
          AND json_extract(event.payload_json, '$.proposal.impact_preview_checksum') = NEW.impact_preview_checksum
          AND json_extract(event.payload_json, '$.proposal.status') = 'pending'
          AND event.payload_json -> '$.proposal.provenance' = NEW.provenance_json
    ) THEN RAISE(ABORT, 'frame proposal must match its exact creation event') END;
END;

CREATE TRIGGER frame_proposals_task3_transition
BEFORE UPDATE ON frame_proposals
BEGIN
    SELECT CASE WHEN NEW.id IS NOT OLD.id
      OR NEW.task_id IS NOT OLD.task_id
      OR NEW.branch_id IS NOT OLD.branch_id
      OR NEW.base_version IS NOT OLD.base_version
      OR NEW.proposed_version IS NOT OLD.proposed_version
      OR NEW.expected_head_sequence IS NOT OLD.expected_head_sequence
      OR NEW.base_head_checksum IS NOT OLD.base_head_checksum
      OR NEW.base_state_checksum IS NOT OLD.base_state_checksum
      OR NEW.preview_state_checksum IS NOT OLD.preview_state_checksum
      OR NEW.impact_preview_checksum IS NOT OLD.impact_preview_checksum
      OR NEW.changes_json IS NOT OLD.changes_json
      OR NEW.impact_preview_json IS NOT OLD.impact_preview_json
      OR NEW.provenance_json IS NOT OLD.provenance_json
      OR NEW.created_by IS NOT OLD.created_by
      OR NEW.created_by_event_id IS NOT OLD.created_by_event_id
      OR NEW.created_at IS NOT OLD.created_at
      OR OLD.status <> 'pending'
      OR NEW.status NOT IN ('accepted','rejected','superseded')
      OR NEW.decided_by_event_id IS NULL
      OR NEW.decided_at IS NULL
    THEN RAISE(ABORT, 'frame proposal identity is frozen and transitions once from pending') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        JOIN workbench_events created
          ON created.task_id = OLD.task_id AND created.event_id = OLD.created_by_event_id
        WHERE event.task_id = OLD.task_id
          AND event.branch_id = OLD.branch_id
          AND event.event_id = NEW.decided_by_event_id
          AND event.sequence > created.sequence
          AND event.created_at = NEW.decided_at
          AND (
              (NEW.status = 'accepted'
               AND event.event_type = 'frame.change_confirmed'
               AND json_extract(event.payload_json, '$.proposal_id') = OLD.id
               AND json_extract(event.payload_json, '$.expected_preview_state_checksum') = OLD.preview_state_checksum
               AND json_extract(event.payload_json, '$.expected_impact_preview_checksum') = OLD.impact_preview_checksum)
              OR
              (NEW.status = 'superseded'
               AND event.event_type = 'frame.change_confirmed'
               AND json_extract(event.payload_json, '$.proposal_id') <> OLD.id
               AND EXISTS (
                   SELECT 1 FROM json_each(event.payload_json, '$.proposal_supersessions') peer
                   WHERE json_extract(peer.value, '$.proposal_id') = OLD.id
                     AND json_extract(peer.value, '$.expected_created_by_event_id') = OLD.created_by_event_id
                     AND json_extract(peer.value, '$.expected_preview_state_checksum') = OLD.preview_state_checksum
               ))
              OR
              (NEW.status = 'rejected'
               AND event.event_type = 'frame.proposal_rejected'
               AND json_extract(event.payload_json, '$.proposal_id') = OLD.id
               AND json_extract(event.payload_json, '$.expected_preview_state_checksum') = OLD.preview_state_checksum)
          )
    ) THEN RAISE(ABORT, 'frame proposal transition must match one exact later decision event') END;
    SELECT CASE WHEN NEW.status = 'superseded' AND EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = OLD.task_id AND event.event_id = NEW.decided_by_event_id
          AND json_extract(event.payload_json, '$.proposal_id') = OLD.id
    ) THEN RAISE(ABORT, 'frame confirmation primary must become accepted, never superseded') END;
END;

CREATE TRIGGER frame_proposals_task3_no_delete
BEFORE DELETE ON frame_proposals
BEGIN
    SELECT RAISE(ABORT, 'frame proposals cannot be deleted');
END;

CREATE TRIGGER decision_requests_task3_validate_insert
BEFORE INSERT ON decision_requests
BEGIN
    SELECT CASE WHEN NEW.revision <> 1
        THEN RAISE(ABORT, 'decision insert revision must be 1') END;
    SELECT CASE WHEN json_array_length(NEW.source_occurrences_json) <> 1
      OR json_extract(NEW.source_occurrences_json, '$[0].occurrence_kind') <> 'question'
      OR json_extract(NEW.source_occurrences_json, '$[0].classification') <> 'accepted'
    THEN RAISE(ABORT, 'decision first source occurrence must be the accepted question') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM decision_requests existing, json_each(existing.source_occurrences_json) occurrence
        WHERE json_extract(occurrence.value, '$.occurrence_kind') = 'question'
          AND json_extract(occurrence.value, '$.classification') = 'accepted'
          AND json_extract(occurrence.value, '$.native_question_identity_checksum') =
              json_extract(NEW.source_occurrences_json, '$[0].native_question_identity_checksum')
    ) THEN RAISE(ABORT, 'accepted native question identity is globally unique') END;
    SELECT CASE WHEN length(json_extract(
        NEW.source_occurrences_json, '$[0].native_question_identity_checksum'
    )) <> 64 OR json_extract(
        NEW.source_occurrences_json, '$[0].native_question_identity_checksum'
    ) GLOB '*[^0-9a-f]*'
    THEN RAISE(ABORT, 'accepted native question identity must be lowercase 64-hex') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM json_each(NEW.affected_node_keys_json) item
        WHERE item.type <> 'text' OR length(trim(CAST(item.value AS TEXT))) = 0
    ) OR EXISTS (
        SELECT 1
        FROM json_each(NEW.affected_node_keys_json) left_item
        JOIN json_each(NEW.affected_node_keys_json) right_item
          ON CAST(left_item.key AS INTEGER) < CAST(right_item.key AS INTEGER)
        WHERE CAST(left_item.value AS TEXT) >= CAST(right_item.value AS TEXT)
    ) THEN RAISE(ABORT, 'affected node keys must be sorted unique strings') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM json_each(NEW.options_json) option_item
        WHERE option_item.type <> 'object'
           OR json_type(option_item.value, '$.option_id') <> 'text'
           OR length(trim(json_extract(option_item.value, '$.option_id'))) = 0
           OR json_type(option_item.value, '$.position_id') <> 'text'
           OR length(trim(json_extract(option_item.value, '$.position_id'))) = 0
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.positions_json) position_item
        WHERE position_item.type <> 'object'
           OR json_type(position_item.value, '$.position_id') <> 'text'
           OR length(trim(json_extract(position_item.value, '$.position_id'))) = 0
    ) OR EXISTS (
        SELECT 1
        FROM json_each(NEW.options_json) left_option
        JOIN json_each(NEW.options_json) right_option
          ON CAST(left_option.key AS INTEGER) < CAST(right_option.key AS INTEGER)
        WHERE json_extract(left_option.value, '$.option_id') = json_extract(right_option.value, '$.option_id')
    ) OR EXISTS (
        SELECT 1
        FROM json_each(NEW.positions_json) left_position
        JOIN json_each(NEW.positions_json) right_position
          ON CAST(left_position.key AS INTEGER) < CAST(right_position.key AS INTEGER)
        WHERE json_extract(left_position.value, '$.position_id') = json_extract(right_position.value, '$.position_id')
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.options_json) option_item
        WHERE NOT EXISTS (
            SELECT 1 FROM json_each(NEW.positions_json) position_item
            WHERE json_extract(position_item.value, '$.position_id') =
                  json_extract(option_item.value, '$.position_id')
        )
    ) OR (
        NEW.recommendation_option_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM json_each(NEW.options_json) option_item
            WHERE json_extract(option_item.value, '$.option_id') = NEW.recommendation_option_id
        )
    ) THEN RAISE(ABORT, 'decision option and position references must resolve exactly and uniquely') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.created_by_event_id
          AND event.event_id = NEW.last_event_id
          AND event.event_type = 'decision.enqueued'
          AND event.created_at = NEW.created_at
          AND json_type(event.payload_json, '$.candidate.source') = 'object'
          AND json(event.payload_json -> '$.candidate.source') = json(NEW.source_occurrences_json -> '$[0]')
          AND json_type(event.payload_json, '$.decision') = 'object'
          AND json_extract(event.payload_json, '$.decision.decision_id') = NEW.decision_id
          AND json_extract(event.payload_json, '$.decision.task_id') = NEW.task_id
          AND json_extract(event.payload_json, '$.decision.branch_id') = NEW.branch_id
          AND json_extract(event.payload_json, '$.decision.revision') = NEW.revision
          AND json_extract(event.payload_json, '$.decision.state') = NEW.state
          AND json_extract(event.payload_json, '$.decision.queue_order') = NEW.queue_order
          AND json_extract(event.payload_json, '$.decision.semantic_identity') = NEW.semantic_identity
          AND json_extract(event.payload_json, '$.decision.tier') = NEW.tier
          AND json_extract(event.payload_json, '$.decision.kind') = NEW.kind
          AND json_extract(event.payload_json, '$.decision.question') = NEW.question
          AND event.payload_json -> '$.decision.options' = NEW.options_json
          AND json_extract(event.payload_json, '$.decision.free_form_allowed') = NEW.free_form_allowed
          AND json_type(event.payload_json, '$.decision.recommendation_option_id') IS NOT NULL
          AND json_extract(event.payload_json, '$.decision.recommendation_option_id') IS NEW.recommendation_option_id
          AND json_type(event.payload_json, '$.decision.recommendation_reason') IS NOT NULL
          AND json_extract(event.payload_json, '$.decision.recommendation_reason') IS NEW.recommendation_reason
          AND json_extract(event.payload_json, '$.decision.changed_outcome') = NEW.changed_outcome
          AND event.payload_json -> '$.decision.positions' = NEW.positions_json
          AND event.payload_json -> '$.decision.materiality' = NEW.materiality_json
          AND json_extract(event.payload_json, '$.decision.consequence_if_unresolved') = NEW.consequence_if_unresolved
          AND event.payload_json -> '$.decision.affected_node_keys' = NEW.affected_node_keys_json
          AND event.payload_json -> '$.decision.provenance' = NEW.provenance_json
          AND event.payload_json -> '$.decision.source_occurrences' = NEW.source_occurrences_json
          AND json_type(event.payload_json, '$.decision.pending_interpretation') IS NOT NULL
          AND event.payload_json -> '$.decision.pending_interpretation' IS
              CASE WHEN NEW.pending_interpretation_json IS NULL THEN 'null' ELSE NEW.pending_interpretation_json END
          AND json_type(event.payload_json, '$.decision.resolution') IS NOT NULL
          AND event.payload_json -> '$.decision.resolution' IS
              CASE WHEN NEW.resolution_json IS NULL THEN 'null' ELSE NEW.resolution_json END
          AND NEW.state IN ('active','queued')
          AND ((NEW.state = 'active' AND NEW.queue_order = 0)
               OR (NEW.state = 'queued' AND NEW.queue_order > 0))
    ) THEN RAISE(ABORT, 'decision insert must match its exact enqueue event') END;
END;

CREATE TRIGGER decision_requests_task3_transition
BEFORE UPDATE ON decision_requests
BEGIN
    SELECT CASE WHEN NEW.decision_id IS NOT OLD.decision_id
      OR NEW.task_id IS NOT OLD.task_id
      OR NEW.branch_id IS NOT OLD.branch_id
      OR NEW.tier IS NOT OLD.tier
      OR NEW.kind IS NOT OLD.kind
      OR NEW.semantic_identity IS NOT OLD.semantic_identity
      OR NEW.question IS NOT OLD.question
      OR NEW.options_json IS NOT OLD.options_json
      OR NEW.free_form_allowed IS NOT OLD.free_form_allowed
      OR NEW.recommendation_option_id IS NOT OLD.recommendation_option_id
      OR NEW.recommendation_reason IS NOT OLD.recommendation_reason
      OR NEW.changed_outcome IS NOT OLD.changed_outcome
      OR NEW.positions_json IS NOT OLD.positions_json
      OR NEW.materiality_json IS NOT OLD.materiality_json
      OR NEW.consequence_if_unresolved IS NOT OLD.consequence_if_unresolved
      OR NEW.affected_node_keys_json IS NOT OLD.affected_node_keys_json
      OR NEW.provenance_json IS NOT OLD.provenance_json
      OR NEW.created_by_event_id IS NOT OLD.created_by_event_id
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.revision <> OLD.revision + 1
      OR NOT (
          NEW.state = OLD.state
          OR (OLD.state = 'queued' AND NEW.state IN ('active','resolved','superseded'))
          OR (OLD.state = 'active' AND NEW.state IN ('resolved','superseded'))
      )
    THEN RAISE(ABORT, 'decision update violates immutable identity, revision, or state transition') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        JOIN workbench_events prior
          ON prior.task_id = OLD.task_id AND prior.event_id = OLD.last_event_id
        WHERE event.task_id = OLD.task_id
          AND event.branch_id = OLD.branch_id
          AND event.event_id = NEW.last_event_id
          AND event.sequence > prior.sequence
          AND event.event_type IN (
              'decision.source_merged','decision.queue_reordered','decision.free_form_received',
              'decision.reply_repeated','decision.interpretation_proposed','decision.interpretation_rejected',
              'decision.resolved','decision.superseded','decision.late_reply_recorded'
          )
          AND (
              (event.event_type = 'decision.queue_reordered'
               AND json_type(event.payload_json, '$.expected_queue_revision') = 'integer')
              OR
              (event.event_type = 'decision.source_merged'
               AND json_extract(event.payload_json, '$.decision_id') = OLD.decision_id
               AND json_extract(event.payload_json, '$.expected_decision_revision') = OLD.revision
               AND json_extract(event.payload_json, '$.resulting_decision_revision') = NEW.revision)
              OR
              (event.event_type = 'decision.interpretation_proposed'
               AND json_extract(event.payload_json, '$.decision_id') = OLD.decision_id
               AND json_extract(event.payload_json, '$.expected_decision_revision') = OLD.revision
               AND json_extract(event.payload_json, '$.resulting_decision_revision') = NEW.revision)
              OR
              (event.event_type IN (
                   'decision.free_form_received','decision.reply_repeated',
                   'decision.interpretation_rejected','decision.resolved',
                   'decision.superseded','decision.late_reply_recorded'
               )
               AND json_extract(event.payload_json, '$.decision_id') = OLD.decision_id
               AND json_extract(event.payload_json, '$.expected_decision_revision') = OLD.revision
               AND json_type(event.payload_json, '$.resulting_decision') = 'object'
               AND json_extract(event.payload_json, '$.resulting_decision.decision_id') = NEW.decision_id
               AND json_extract(event.payload_json, '$.resulting_decision.task_id') = NEW.task_id
               AND json_extract(event.payload_json, '$.resulting_decision.branch_id') = NEW.branch_id
               AND json_extract(event.payload_json, '$.resulting_decision.revision') = NEW.revision
               AND json_extract(event.payload_json, '$.resulting_decision.state') = NEW.state
               AND json_extract(event.payload_json, '$.resulting_decision.queue_order') = NEW.queue_order
               AND json_extract(event.payload_json, '$.resulting_decision.semantic_identity') = NEW.semantic_identity
               AND json_extract(event.payload_json, '$.resulting_decision.tier') = NEW.tier
               AND json_extract(event.payload_json, '$.resulting_decision.kind') = NEW.kind
               AND json_extract(event.payload_json, '$.resulting_decision.question') = NEW.question
               AND event.payload_json -> '$.resulting_decision.options' = NEW.options_json
               AND json_extract(event.payload_json, '$.resulting_decision.free_form_allowed') = NEW.free_form_allowed
               AND json_type(event.payload_json, '$.resulting_decision.recommendation_option_id') IS NOT NULL
               AND json_extract(event.payload_json, '$.resulting_decision.recommendation_option_id') IS NEW.recommendation_option_id
               AND json_type(event.payload_json, '$.resulting_decision.recommendation_reason') IS NOT NULL
               AND json_extract(event.payload_json, '$.resulting_decision.recommendation_reason') IS NEW.recommendation_reason
               AND json_extract(event.payload_json, '$.resulting_decision.changed_outcome') = NEW.changed_outcome
               AND event.payload_json -> '$.resulting_decision.positions' = NEW.positions_json
               AND event.payload_json -> '$.resulting_decision.materiality' = NEW.materiality_json
               AND json_extract(event.payload_json, '$.resulting_decision.consequence_if_unresolved') = NEW.consequence_if_unresolved
               AND event.payload_json -> '$.resulting_decision.affected_node_keys' = NEW.affected_node_keys_json
               AND event.payload_json -> '$.resulting_decision.provenance' = NEW.provenance_json
               AND event.payload_json -> '$.resulting_decision.source_occurrences' = NEW.source_occurrences_json
               AND json_type(event.payload_json, '$.resulting_decision.pending_interpretation') IS NOT NULL
               AND event.payload_json -> '$.resulting_decision.pending_interpretation' IS
                   CASE WHEN NEW.pending_interpretation_json IS NULL THEN 'null' ELSE NEW.pending_interpretation_json END
               AND json_type(event.payload_json, '$.resulting_decision.resolution') IS NOT NULL
               AND event.payload_json -> '$.resulting_decision.resolution' IS
                   CASE WHEN NEW.resolution_json IS NULL THEN 'null' ELSE NEW.resolution_json END)
          )
    ) THEN RAISE(ABORT, 'decision update must match one exact later event') END;
    SELECT CASE WHEN NEW.source_occurrences_json IS NOT OLD.source_occurrences_json
      AND NOT EXISTS (
          SELECT 1 FROM workbench_events event
          WHERE event.task_id = OLD.task_id
            AND event.event_id = NEW.last_event_id
            AND event.event_type IN (
                'decision.source_merged','decision.free_form_received','decision.reply_repeated',
                'decision.interpretation_rejected','decision.resolved','decision.late_reply_recorded'
            )
            AND NEW.source_occurrences_json = (
                SELECT json_group_array(json(ordered_occurrence.occurrence_json))
                FROM (
                    SELECT occurrence_json
                    FROM (
                        SELECT json(prior_occurrence.value) AS occurrence_json,
                               json_extract(prior_occurrence.value, '$.received_at') AS received_at,
                               json_extract(prior_occurrence.value, '$.occurrence_id') AS occurrence_id
                        FROM json_each(OLD.source_occurrences_json) prior_occurrence
                        UNION ALL
                        SELECT json(CASE
                                   WHEN event.event_type = 'decision.source_merged'
                                   THEN event.payload_json -> '$.candidate.source'
                                   ELSE event.payload_json -> '$.occurrence'
                               END) AS occurrence_json,
                               json_extract(CASE
                                   WHEN event.event_type = 'decision.source_merged'
                                   THEN event.payload_json -> '$.candidate.source'
                                   ELSE event.payload_json -> '$.occurrence'
                               END, '$.received_at') AS received_at,
                               json_extract(CASE
                                   WHEN event.event_type = 'decision.source_merged'
                                   THEN event.payload_json -> '$.candidate.source'
                                   ELSE event.payload_json -> '$.occurrence'
                               END, '$.occurrence_id') AS occurrence_id
                    ) occurrence_union
                    ORDER BY received_at, occurrence_id
                ) ordered_occurrence
            )
      )
    THEN RAISE(ABORT, 'decision occurrence update must preserve prior bytes, add the event occurrence, and canonical-sort') END;
    SELECT CASE WHEN NOT (
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type = 'decision.queue_reordered'
        )
         AND OLD.state = 'queued' AND NEW.state = OLD.state
         AND NEW.source_occurrences_json = OLD.source_occurrences_json
         AND NEW.pending_interpretation_json IS OLD.pending_interpretation_json
         AND NEW.resolution_json IS OLD.resolution_json
         AND NEW.decided_at IS OLD.decided_at)
        OR
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type = 'decision.source_merged'
        )
         AND OLD.state IN ('active','queued') AND NEW.state = OLD.state
         AND NEW.queue_order = OLD.queue_order
         AND NEW.pending_interpretation_json IS OLD.pending_interpretation_json
         AND NEW.resolution_json IS OLD.resolution_json
         AND NEW.decided_at IS OLD.decided_at
         AND json_array_length(NEW.source_occurrences_json) = json_array_length(OLD.source_occurrences_json) + 1
         AND EXISTS (
             SELECT 1 FROM json_each(NEW.source_occurrences_json) occurrence
             WHERE json(occurrence.value) = json((
                 SELECT payload_json -> '$.candidate.source' FROM workbench_events
                 WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
             ))
         ))
        OR
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type = 'decision.late_reply_recorded'
        )
         AND OLD.state IN ('resolved','superseded') AND NEW.state = OLD.state
         AND NEW.queue_order = OLD.queue_order
         AND NEW.pending_interpretation_json IS OLD.pending_interpretation_json
         AND NEW.resolution_json IS OLD.resolution_json
         AND NEW.decided_at IS OLD.decided_at
         AND json_array_length(NEW.source_occurrences_json) = json_array_length(OLD.source_occurrences_json) + 1
         AND EXISTS (
             SELECT 1 FROM json_each(NEW.source_occurrences_json) occurrence
             WHERE json(occurrence.value) = json((
                 SELECT payload_json -> '$.occurrence' FROM workbench_events
                 WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
             ))
         ))
        OR
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type IN (
                'decision.free_form_received','decision.reply_repeated'
            )
        )
         AND OLD.state IN ('active','queued') AND NEW.state = OLD.state
         AND NEW.queue_order = OLD.queue_order
         AND NEW.pending_interpretation_json IS OLD.pending_interpretation_json
         AND NEW.resolution_json IS OLD.resolution_json
         AND NEW.decided_at IS OLD.decided_at
         AND json_array_length(NEW.source_occurrences_json) = json_array_length(OLD.source_occurrences_json) + 1
         AND EXISTS (
             SELECT 1 FROM json_each(NEW.source_occurrences_json) occurrence
             WHERE json(occurrence.value) = json((
                 SELECT payload_json -> '$.occurrence' FROM workbench_events
                 WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
             ))
         ))
        OR
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type = 'decision.interpretation_proposed'
        )
         AND OLD.state IN ('active','queued') AND NEW.state = OLD.state
         AND NEW.queue_order = OLD.queue_order
         AND NEW.source_occurrences_json = OLD.source_occurrences_json
         AND NEW.resolution_json IS OLD.resolution_json
         AND NEW.decided_at IS OLD.decided_at
         AND NEW.pending_interpretation_json = (
             SELECT payload_json -> '$.interpretation' FROM workbench_events
             WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
         ))
        OR
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type = 'decision.interpretation_rejected'
        )
         AND OLD.state IN ('active','queued') AND NEW.state = OLD.state
         AND NEW.queue_order = OLD.queue_order
         AND OLD.pending_interpretation_json IS NOT NULL
         AND NEW.pending_interpretation_json IS NULL
         AND NEW.resolution_json IS OLD.resolution_json
         AND NEW.decided_at IS OLD.decided_at
         AND json_array_length(NEW.source_occurrences_json) = json_array_length(OLD.source_occurrences_json) + 1)
        OR
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type = 'decision.resolved'
        )
         AND OLD.state IN ('active','queued') AND NEW.state = 'resolved'
         AND NEW.pending_interpretation_json IS NULL
         AND NEW.resolution_json = (
             SELECT payload_json -> '$.resolution' FROM workbench_events
             WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
         )
         AND NEW.decided_at = (
             SELECT created_at FROM workbench_events
             WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
         )
         AND json_array_length(NEW.source_occurrences_json) = json_array_length(OLD.source_occurrences_json) + 1
         AND EXISTS (
             SELECT 1 FROM json_each(NEW.source_occurrences_json) occurrence
             WHERE json(occurrence.value) = json((
                 SELECT payload_json -> '$.occurrence' FROM workbench_events
                 WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
             ))
         ))
        OR
        (NEW.last_event_id IN (
            SELECT event_id FROM workbench_events
            WHERE task_id = OLD.task_id AND event_type = 'decision.superseded'
        )
         AND OLD.state IN ('active','queued') AND NEW.state = 'superseded'
         AND NEW.pending_interpretation_json IS NULL
         AND NEW.resolution_json IS NULL
         AND NEW.source_occurrences_json = OLD.source_occurrences_json
         AND NEW.decided_at = (
             SELECT created_at FROM workbench_events
             WHERE task_id = OLD.task_id AND event_id = NEW.last_event_id
         ))
    ) THEN RAISE(ABORT, 'decision event-specific effects do not match the event discriminator') END;
END;

CREATE TRIGGER decision_requests_task3_no_delete
BEFORE DELETE ON decision_requests
BEGIN
    SELECT RAISE(ABORT, 'decision requests cannot be deleted');
END;

CREATE TRIGGER decision_queue_heads_validate_insert
BEFORE INSERT ON decision_queue_heads
BEGIN
    SELECT CASE WHEN NEW.revision <> 1
        THEN RAISE(ABORT, 'decision queue first persisted revision must be 1') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM json_each(NEW.ordered_open_ids_json) item
        WHERE item.type <> 'text' OR length(trim(CAST(item.value AS TEXT))) = 0
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.ordered_open_ids_json) left_item
        JOIN json_each(NEW.ordered_open_ids_json) right_item
          ON CAST(left_item.key AS INTEGER) < CAST(right_item.key AS INTEGER)
        WHERE left_item.value = right_item.value
    ) THEN RAISE(ABORT, 'decision queue IDs must be unique nonempty strings') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.last_event_id
          AND event.event_type = 'decision.enqueued'
          AND event.created_at = NEW.updated_at
          AND json_extract(event.payload_json, '$.resulting_queue.task_id') = NEW.task_id
          AND json_extract(event.payload_json, '$.resulting_queue.branch_id') = NEW.branch_id
          AND json_extract(event.payload_json, '$.resulting_queue.revision') = NEW.revision
          AND json_extract(event.payload_json, '$.resulting_queue.checksum') = NEW.queue_checksum
          AND json_extract(event.payload_json, '$.resulting_queue.task_id') = NEW.task_id
          AND json_extract(event.payload_json, '$.resulting_queue.branch_id') = NEW.branch_id
          AND json_extract(event.payload_json, '$.resulting_queue.active.decision_id') IS NEW.active_decision_id
          AND json_array_length(event.payload_json, '$.resulting_queue.queued') =
              json_array_length(NEW.ordered_open_ids_json) - CASE WHEN NEW.active_decision_id IS NULL THEN 0 ELSE 1 END
          AND json_extract(event.payload_json, '$.resulting_queue.active.decision_id') = NEW.active_decision_id
          AND json_array_length(NEW.ordered_open_ids_json) = 1
          AND json_array_length(json_extract(event.payload_json, '$.resulting_queue.queued')) = 0
          AND json_extract(event.payload_json, '$.resulting_queue.active') = (
              SELECT json_object(
                  'affected_node_keys',json(decision.affected_node_keys_json),
                  'branch_id',decision.branch_id,
                  'changed_outcome',decision.changed_outcome,
                  'consequence_if_unresolved',decision.consequence_if_unresolved,
                  'decision_id',decision.decision_id,
                  'free_form_allowed',json(CASE decision.free_form_allowed WHEN 1 THEN 'true' ELSE 'false' END),
                  'kind',decision.kind,
                  'materiality',json(decision.materiality_json),
                  'options',json(decision.options_json),
                  'pending_interpretation',json(COALESCE(decision.pending_interpretation_json,'null')),
                  'positions',json(decision.positions_json),
                  'provenance',json(decision.provenance_json),
                  'question',decision.question,
                  'queue_order',decision.queue_order,
                  'recommendation_option_id',decision.recommendation_option_id,
                  'recommendation_reason',decision.recommendation_reason,
                  'resolution',json(COALESCE(decision.resolution_json,'null')),
                  'revision',decision.revision,
                  'semantic_identity',decision.semantic_identity,
                  'source_occurrences',json(decision.source_occurrences_json),
                  'state',decision.state,
                  'task_id',decision.task_id,
                  'tier',decision.tier
              )
              FROM decision_requests decision
              WHERE decision.task_id = NEW.task_id
                AND decision.branch_id = NEW.branch_id
                AND decision.decision_id = NEW.active_decision_id
          )
    ) THEN RAISE(ABORT, 'decision queue head must match its exact first enqueue event') END;
    SELECT CASE WHEN
      NEW.active_decision_id IS NULL
      OR NOT EXISTS (
          SELECT 1 FROM decision_requests decision
          WHERE decision.task_id = NEW.task_id
            AND decision.branch_id = NEW.branch_id
            AND decision.decision_id = NEW.active_decision_id
            AND decision.state = 'active'
            AND decision.queue_order = 0
      )
      OR json_array_length(NEW.ordered_open_ids_json) <> (
          SELECT COUNT(*) FROM decision_requests decision
          WHERE decision.task_id = NEW.task_id
            AND decision.branch_id = NEW.branch_id
            AND decision.state IN ('active','queued')
      )
      OR EXISTS (
          SELECT 1 FROM json_each(NEW.ordered_open_ids_json) ordered
          WHERE NOT EXISTS (
              SELECT 1 FROM decision_requests decision
              WHERE decision.task_id = NEW.task_id
                AND decision.branch_id = NEW.branch_id
                AND decision.decision_id = ordered.value
                AND ((CAST(ordered.key AS INTEGER) = 0 AND decision.state = 'active' AND decision.queue_order = 0)
                  OR (CAST(ordered.key AS INTEGER) > 0 AND decision.state = 'queued'
                      AND decision.queue_order = CAST(ordered.key AS INTEGER)))
          )
      )
    THEN RAISE(ABORT, 'decision queue final state must be active-first, complete, and contiguous') END;
END;

CREATE TRIGGER decision_queue_heads_validate_update
BEFORE UPDATE ON decision_queue_heads
BEGIN
    SELECT CASE WHEN NEW.task_id IS NOT OLD.task_id
      OR NEW.branch_id IS NOT OLD.branch_id
      OR NEW.revision <> OLD.revision + 1
    THEN RAISE(ABORT, 'decision queue update must advance exactly one revision') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        JOIN workbench_events prior
          ON prior.task_id = OLD.task_id AND prior.event_id = OLD.last_event_id
        WHERE event.task_id = OLD.task_id
          AND event.branch_id = OLD.branch_id
          AND event.event_id = NEW.last_event_id
          AND event.sequence > prior.sequence
          AND event.created_at = NEW.updated_at
          AND event.event_type IN (
              'decision.enqueued','decision.source_merged','decision.queue_reordered',
              'decision.free_form_received','decision.reply_repeated','decision.interpretation_proposed',
              'decision.interpretation_rejected','decision.resolved','decision.superseded'
          )
          AND json_extract(event.payload_json, '$.resulting_queue.task_id') = NEW.task_id
          AND json_extract(event.payload_json, '$.resulting_queue.branch_id') = NEW.branch_id
          AND json_extract(event.payload_json, '$.resulting_queue.revision') = NEW.revision
          AND json_extract(event.payload_json, '$.resulting_queue.checksum') = NEW.queue_checksum
          AND json_type(event.payload_json, '$.resulting_queue.active') IS NOT NULL
          AND json_extract(event.payload_json, '$.resulting_queue.active.decision_id')
              IS NEW.active_decision_id
          AND json_type(event.payload_json, '$.resulting_queue.queued') = 'array'
          AND json_array_length(event.payload_json, '$.resulting_queue.queued') =
              json_array_length(NEW.ordered_open_ids_json)
              - CASE WHEN NEW.active_decision_id IS NULL THEN 0 ELSE 1 END
          AND json_extract(event.payload_json, '$.resulting_queue.active') IS (
              SELECT json_object(
                  'affected_node_keys',json(decision.affected_node_keys_json),
                  'branch_id',decision.branch_id,
                  'changed_outcome',decision.changed_outcome,
                  'consequence_if_unresolved',decision.consequence_if_unresolved,
                  'decision_id',decision.decision_id,
                  'free_form_allowed',json(CASE decision.free_form_allowed WHEN 1 THEN 'true' ELSE 'false' END),
                  'kind',decision.kind,
                  'materiality',json(decision.materiality_json),
                  'options',json(decision.options_json),
                  'pending_interpretation',json(COALESCE(decision.pending_interpretation_json,'null')),
                  'positions',json(decision.positions_json),
                  'provenance',json(decision.provenance_json),
                  'question',decision.question,
                  'queue_order',decision.queue_order,
                  'recommendation_option_id',decision.recommendation_option_id,
                  'recommendation_reason',decision.recommendation_reason,
                  'resolution',json(COALESCE(decision.resolution_json,'null')),
                  'revision',decision.revision,
                  'semantic_identity',decision.semantic_identity,
                  'source_occurrences',json(decision.source_occurrences_json),
                  'state',decision.state,
                  'task_id',decision.task_id,
                  'tier',decision.tier
              )
              FROM decision_requests decision
              WHERE decision.task_id = NEW.task_id
                AND decision.branch_id = NEW.branch_id
                AND decision.decision_id = NEW.active_decision_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM json_each(event.payload_json, '$.resulting_queue.queued') event_queue
              WHERE NOT EXISTS (
                  SELECT 1
                  FROM decision_requests decision
                  WHERE decision.task_id = NEW.task_id
                    AND decision.branch_id = NEW.branch_id
                    AND decision.state = 'queued'
                    AND decision.queue_order = CAST(event_queue.key AS INTEGER) + 1
                    AND event_queue.value = json_object(
                        'affected_node_keys',json(decision.affected_node_keys_json),
                        'branch_id',decision.branch_id,
                        'changed_outcome',decision.changed_outcome,
                        'consequence_if_unresolved',decision.consequence_if_unresolved,
                        'decision_id',decision.decision_id,
                        'free_form_allowed',json(CASE decision.free_form_allowed WHEN 1 THEN 'true' ELSE 'false' END),
                        'kind',decision.kind,
                        'materiality',json(decision.materiality_json),
                        'options',json(decision.options_json),
                        'pending_interpretation',json(COALESCE(decision.pending_interpretation_json,'null')),
                        'positions',json(decision.positions_json),
                        'provenance',json(decision.provenance_json),
                        'question',decision.question,
                        'queue_order',decision.queue_order,
                        'recommendation_option_id',decision.recommendation_option_id,
                        'recommendation_reason',decision.recommendation_reason,
                        'resolution',json(COALESCE(decision.resolution_json,'null')),
                        'revision',decision.revision,
                        'semantic_identity',decision.semantic_identity,
                        'source_occurrences',json(decision.source_occurrences_json),
                        'state',decision.state,
                        'task_id',decision.task_id,
                        'tier',decision.tier
                    )
              )
          )
    ) THEN RAISE(ABORT, 'decision queue update must match one exact later event and resulting queue') END;
    SELECT CASE WHEN
      (NEW.active_decision_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM decision_requests decision
          WHERE decision.task_id = NEW.task_id
            AND decision.branch_id = NEW.branch_id
            AND decision.decision_id = NEW.active_decision_id
            AND decision.state = 'active'
            AND decision.queue_order = 0
      ))
      OR (NEW.active_decision_id IS NULL AND EXISTS (
          SELECT 1 FROM decision_requests decision
          WHERE decision.task_id = NEW.task_id
            AND decision.branch_id = NEW.branch_id
            AND decision.state IN ('active','queued')
      ))
      OR json_array_length(NEW.ordered_open_ids_json) <> (
          SELECT COUNT(*) FROM decision_requests decision
          WHERE decision.task_id = NEW.task_id
            AND decision.branch_id = NEW.branch_id
            AND decision.state IN ('active','queued')
      )
      OR EXISTS (
          SELECT 1 FROM json_each(NEW.ordered_open_ids_json) ordered
          WHERE NOT EXISTS (
              SELECT 1 FROM decision_requests decision
              WHERE decision.task_id = NEW.task_id
                AND decision.branch_id = NEW.branch_id
                AND decision.decision_id = ordered.value
                AND (
                    (CAST(ordered.key AS INTEGER) = 0
                     AND decision.state = 'active' AND decision.queue_order = 0)
                    OR
                    (CAST(ordered.key AS INTEGER) > 0
                     AND decision.state = 'queued'
                     AND decision.queue_order = CAST(ordered.key AS INTEGER))
                )
          )
      )
      OR EXISTS (
          SELECT 1 FROM json_each(
              (SELECT event.payload_json FROM workbench_events event
               WHERE event.task_id = NEW.task_id AND event.event_id = NEW.last_event_id),
              '$.resulting_queue.queued'
          ) event_queue
          WHERE json_extract(event_queue.value, '$.decision_id') <>
                json_extract(NEW.ordered_open_ids_json, '$[' || (CAST(event_queue.key AS INTEGER) + 1) || ']')
      )
    THEN RAISE(ABORT, 'decision queue final state must be active-first, complete, and contiguous') END;
END;

CREATE TRIGGER decision_queue_heads_no_delete
BEFORE DELETE ON decision_queue_heads
BEGIN
    SELECT RAISE(ABORT, 'decision queue heads cannot be deleted');
END;

CREATE TRIGGER service_runs_task3_validate_insert
BEFORE INSERT ON service_runs
BEGIN
    SELECT CASE WHEN NEW.revision <> 1
      OR NEW.state <> 'queued'
      OR NEW.output_validity <> 'current'
      OR NEW.receipt_event_id IS NOT NULL
      OR NEW.started_at IS NOT NULL
      OR NEW.completed_at IS NOT NULL
    THEN RAISE(ABORT, 'service runs must register at revision 1 as unstarted queued current work') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.last_event_id
          AND event.event_type = 'service_run.registered'
          AND event.frame_version = NEW.frame_version
          AND event.created_at = NEW.updated_at
          AND json_extract(event.payload_json, '$.run_id') = NEW.run_id
          AND json_extract(event.payload_json, '$.service') = NEW.service
          AND json_extract(event.payload_json, '$.role') = NEW.role
          AND json_extract(event.payload_json, '$.task_id') = NEW.task_id
          AND json_extract(event.payload_json, '$.branch_id') = NEW.branch_id
          AND json_extract(event.payload_json, '$.frame_version') = NEW.frame_version
          AND event.payload_json -> '$.input_manifest' = NEW.input_manifest_json
          AND json_type(event.payload_json, '$.input_node_ids') = 'array'
          AND json_extract(event.payload_json, '$.initial_state') = NEW.state
          AND json_extract(event.payload_json, '$.output_validity') = NEW.output_validity
          AND json_type(event.payload_json, '$.native_identity_envelope') IS NOT NULL
          AND (
              (NEW.adapter_provider IS NULL
               AND json_type(event.payload_json, '$.native_identity_envelope') = 'null')
              OR
              (NEW.adapter_provider IS NOT NULL
               AND json_type(event.payload_json, '$.native_identity_envelope') = 'object'
               AND json_extract(event.payload_json, '$.native_identity_envelope.adapter_provider') = NEW.adapter_provider
               AND json_extract(event.payload_json, '$.native_identity_envelope.adapter_contract_revision') = NEW.adapter_contract_revision
               AND json_extract(event.payload_json, '$.native_identity_envelope.account_id') = NEW.account_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.profile_id') = NEW.profile_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.model_id') = NEW.model_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.capability_inventory_revision') = NEW.capability_inventory_revision
               AND json_extract(event.payload_json, '$.native_identity_envelope.transport_generation') = NEW.transport_generation
               AND json_extract(event.payload_json, '$.native_identity_envelope.native_session_id') = NEW.native_session_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.native_thread_id') = NEW.native_thread_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.native_turn_id') IS NEW.native_turn_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.native_request_id') IS NEW.native_request_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.native_tool_use_id') IS NEW.native_tool_use_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.native_question_group_id') IS NEW.native_question_group_id
               AND json_extract(event.payload_json, '$.native_identity_envelope.launch_origin') = NEW.launch_origin
               AND json_type(event.payload_json, '$.native_identity_envelope.native_handle') IS NOT NULL
               AND event.payload_json -> '$.native_identity_envelope.native_handle' IS
                   CASE WHEN NEW.native_handle_json IS NULL THEN 'null' ELSE NEW.native_handle_json END)
          )
    ) THEN RAISE(ABORT, 'service run registration must match its exact event') END;
END;

CREATE TRIGGER service_runs_task3_transition
BEFORE UPDATE ON service_runs
BEGIN
    SELECT CASE WHEN NEW.run_id IS NOT OLD.run_id
      OR NEW.task_id IS NOT OLD.task_id
      OR NEW.service IS NOT OLD.service
      OR NEW.role IS NOT OLD.role
      OR NEW.frame_version IS NOT OLD.frame_version
      OR NEW.branch_id IS NOT OLD.branch_id
      OR NEW.input_manifest_json IS NOT OLD.input_manifest_json
      OR NEW.revision <> OLD.revision + 1
      OR (OLD.receipt_event_id IS NOT NULL AND NEW.receipt_event_id IS NOT OLD.receipt_event_id)
      OR (OLD.started_at IS NOT NULL AND NEW.started_at IS NOT OLD.started_at)
      OR (OLD.completed_at IS NOT NULL AND NEW.completed_at IS NOT OLD.completed_at)
    THEN RAISE(ABORT, 'service run immutable identity or revision was changed') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = OLD.task_id
          AND event.event_id = NEW.last_event_id
          AND event.event_type = 'service_run.state_changed'
    ) AND NOT EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = OLD.task_id
          AND event.event_id = NEW.last_event_id
          AND event.event_type = 'service_run.state_changed'
          AND (
              (json_type(event.payload_json, '$.native_identity_attachment') = 'null'
               AND NEW.adapter_provider IS OLD.adapter_provider
               AND NEW.adapter_contract_revision IS OLD.adapter_contract_revision
               AND NEW.account_id IS OLD.account_id
               AND NEW.profile_id IS OLD.profile_id
               AND NEW.model_id IS OLD.model_id
               AND NEW.capability_inventory_revision IS OLD.capability_inventory_revision
               AND NEW.transport_generation IS OLD.transport_generation
               AND NEW.native_session_id IS OLD.native_session_id
               AND NEW.native_thread_id IS OLD.native_thread_id
               AND NEW.native_turn_id IS OLD.native_turn_id
               AND NEW.native_request_id IS OLD.native_request_id
               AND NEW.native_tool_use_id IS OLD.native_tool_use_id
               AND NEW.native_question_group_id IS OLD.native_question_group_id
               AND NEW.launch_origin IS OLD.launch_origin
               AND NEW.native_handle_json IS OLD.native_handle_json)
              OR
              (json_type(event.payload_json, '$.native_identity_attachment') = 'object'
               AND OLD.adapter_provider IS NULL AND OLD.adapter_contract_revision IS NULL
               AND OLD.account_id IS NULL AND OLD.profile_id IS NULL AND OLD.model_id IS NULL
               AND OLD.capability_inventory_revision IS NULL AND OLD.transport_generation IS NULL
               AND OLD.native_session_id IS NULL AND OLD.native_thread_id IS NULL
               AND OLD.native_turn_id IS NULL AND OLD.native_request_id IS NULL
               AND OLD.native_tool_use_id IS NULL AND OLD.native_question_group_id IS NULL
               AND OLD.launch_origin IS NULL AND OLD.native_handle_json IS NULL
               AND ((OLD.state = 'queued' AND NEW.state = 'starting')
                    OR (OLD.state = 'starting' AND NEW.state = 'running'))
               AND json_extract(event.payload_json, '$.native_identity_attachment.adapter_provider') = NEW.adapter_provider
               AND json_extract(event.payload_json, '$.native_identity_attachment.adapter_contract_revision') = NEW.adapter_contract_revision
               AND json_extract(event.payload_json, '$.native_identity_attachment.account_id') = NEW.account_id
               AND json_extract(event.payload_json, '$.native_identity_attachment.profile_id') = NEW.profile_id
               AND json_extract(event.payload_json, '$.native_identity_attachment.model_id') = NEW.model_id
               AND json_extract(event.payload_json, '$.native_identity_attachment.capability_inventory_revision') = NEW.capability_inventory_revision
               AND json_extract(event.payload_json, '$.native_identity_attachment.transport_generation') = NEW.transport_generation
               AND json_extract(event.payload_json, '$.native_identity_attachment.native_session_id') = NEW.native_session_id
               AND json_extract(event.payload_json, '$.native_identity_attachment.native_thread_id') = NEW.native_thread_id
               AND json_type(event.payload_json, '$.native_identity_attachment.native_turn_id') IS NOT NULL
               AND json_extract(event.payload_json, '$.native_identity_attachment.native_turn_id') IS NEW.native_turn_id
               AND json_type(event.payload_json, '$.native_identity_attachment.native_request_id') IS NOT NULL
               AND json_extract(event.payload_json, '$.native_identity_attachment.native_request_id') IS NEW.native_request_id
               AND json_type(event.payload_json, '$.native_identity_attachment.native_tool_use_id') IS NOT NULL
               AND json_extract(event.payload_json, '$.native_identity_attachment.native_tool_use_id') IS NEW.native_tool_use_id
               AND json_type(event.payload_json, '$.native_identity_attachment.native_question_group_id') IS NOT NULL
               AND json_extract(event.payload_json, '$.native_identity_attachment.native_question_group_id') IS NEW.native_question_group_id
               AND json_extract(event.payload_json, '$.native_identity_attachment.launch_origin') = NEW.launch_origin
               AND json_type(event.payload_json, '$.native_identity_attachment.native_handle') IS NOT NULL
               AND event.payload_json -> '$.native_identity_attachment.native_handle' IS
                   CASE WHEN NEW.native_handle_json IS NULL THEN 'null' ELSE NEW.native_handle_json END)
          )
    ) THEN RAISE(ABORT, 'service run native identity is immutable unless the exact event attachment is persisted') END;
    SELECT CASE WHEN (
      NEW.adapter_provider IS NOT OLD.adapter_provider
      OR NEW.adapter_contract_revision IS NOT OLD.adapter_contract_revision
      OR NEW.account_id IS NOT OLD.account_id
      OR NEW.profile_id IS NOT OLD.profile_id
      OR NEW.model_id IS NOT OLD.model_id
      OR NEW.capability_inventory_revision IS NOT OLD.capability_inventory_revision
      OR NEW.transport_generation IS NOT OLD.transport_generation
      OR NEW.native_session_id IS NOT OLD.native_session_id
      OR NEW.native_thread_id IS NOT OLD.native_thread_id
      OR NEW.native_turn_id IS NOT OLD.native_turn_id
      OR NEW.native_request_id IS NOT OLD.native_request_id
      OR NEW.native_tool_use_id IS NOT OLD.native_tool_use_id
      OR NEW.native_question_group_id IS NOT OLD.native_question_group_id
      OR NEW.launch_origin IS NOT OLD.launch_origin
      OR NEW.native_handle_json IS NOT OLD.native_handle_json
    ) AND NOT (
      OLD.adapter_provider IS NULL AND OLD.adapter_contract_revision IS NULL
      AND OLD.account_id IS NULL AND OLD.profile_id IS NULL AND OLD.model_id IS NULL
      AND OLD.capability_inventory_revision IS NULL AND OLD.transport_generation IS NULL
      AND OLD.native_session_id IS NULL AND OLD.native_thread_id IS NULL
      AND OLD.native_turn_id IS NULL AND OLD.native_request_id IS NULL
      AND OLD.native_tool_use_id IS NULL AND OLD.native_question_group_id IS NULL
      AND OLD.launch_origin IS NULL AND OLD.native_handle_json IS NULL
      AND NEW.adapter_provider IS NOT NULL
      AND ((OLD.state = 'queued' AND NEW.state = 'starting')
           OR (OLD.state = 'starting' AND NEW.state = 'running'))
      AND EXISTS (
          SELECT 1 FROM workbench_events event
          WHERE event.task_id = OLD.task_id AND event.event_id = NEW.last_event_id
            AND event.event_type = 'service_run.state_changed'
            AND json_type(event.payload_json, '$.native_identity_attachment') = 'object'
            AND json_extract(event.payload_json, '$.native_identity_attachment.adapter_provider') = NEW.adapter_provider
            AND json_extract(event.payload_json, '$.native_identity_attachment.adapter_contract_revision') = NEW.adapter_contract_revision
            AND json_extract(event.payload_json, '$.native_identity_attachment.account_id') = NEW.account_id
            AND json_extract(event.payload_json, '$.native_identity_attachment.profile_id') = NEW.profile_id
            AND json_extract(event.payload_json, '$.native_identity_attachment.model_id') = NEW.model_id
            AND json_extract(event.payload_json, '$.native_identity_attachment.capability_inventory_revision') = NEW.capability_inventory_revision
            AND json_extract(event.payload_json, '$.native_identity_attachment.transport_generation') = NEW.transport_generation
            AND json_extract(event.payload_json, '$.native_identity_attachment.native_session_id') = NEW.native_session_id
            AND json_extract(event.payload_json, '$.native_identity_attachment.native_thread_id') = NEW.native_thread_id
            AND json_type(event.payload_json, '$.native_identity_attachment.native_turn_id') IS NOT NULL
            AND json_extract(event.payload_json, '$.native_identity_attachment.native_turn_id') IS NEW.native_turn_id
            AND json_type(event.payload_json, '$.native_identity_attachment.native_request_id') IS NOT NULL
            AND json_extract(event.payload_json, '$.native_identity_attachment.native_request_id') IS NEW.native_request_id
            AND json_type(event.payload_json, '$.native_identity_attachment.native_tool_use_id') IS NOT NULL
            AND json_extract(event.payload_json, '$.native_identity_attachment.native_tool_use_id') IS NEW.native_tool_use_id
            AND json_type(event.payload_json, '$.native_identity_attachment.native_question_group_id') IS NOT NULL
            AND json_extract(event.payload_json, '$.native_identity_attachment.native_question_group_id') IS NEW.native_question_group_id
            AND json_extract(event.payload_json, '$.native_identity_attachment.launch_origin') = NEW.launch_origin
            AND json_type(event.payload_json, '$.native_identity_attachment.native_handle') IS NOT NULL
            AND event.payload_json -> '$.native_identity_attachment.native_handle' IS
                CASE WHEN NEW.native_handle_json IS NULL THEN 'null' ELSE NEW.native_handle_json END
      )
    ) THEN RAISE(ABORT, 'service run native identity is immutable after one complete attachment') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        JOIN workbench_events prior
          ON prior.task_id = OLD.task_id AND prior.event_id = OLD.last_event_id
        WHERE event.task_id = OLD.task_id
          AND event.branch_id = OLD.branch_id
          AND event.event_id = NEW.last_event_id
          AND event.sequence > prior.sequence
          AND event.created_at = NEW.updated_at
          AND (
              (event.event_type = 'service_run.receipt_recorded'
               AND OLD.state IN ('running','verifying')
               AND NEW.state = OLD.state
               AND NEW.output_validity = OLD.output_validity
               AND OLD.receipt_event_id IS NULL
               AND NEW.receipt_event_id = event.event_id
               AND NEW.started_at IS OLD.started_at
               AND NEW.completed_at IS OLD.completed_at
               AND json_extract(event.payload_json, '$.run_id') = OLD.run_id
               AND json_extract(event.payload_json, '$.expected_revision') = OLD.revision
               AND json_extract(event.payload_json, '$.expected_state') = OLD.state
               AND json_extract(event.payload_json, '$.resulting_revision') = NEW.revision
               AND json_extract(event.payload_json, '$.receipt.run_id') = OLD.run_id
               AND json_extract(event.payload_json, '$.receipt.run_revision') = OLD.revision
               AND json_extract(event.payload_json, '$.receipt.frame_version') = OLD.frame_version
               AND json_extract(event.payload_json, '$.receipt.input_manifest_checksum') =
                   json_extract(OLD.input_manifest_json, '$.checksum'))
              OR
              (event.event_type = 'service_run.state_changed'
               AND NEW.receipt_event_id IS OLD.receipt_event_id
               AND json_extract(event.payload_json, '$.run_id') = OLD.run_id
               AND json_extract(event.payload_json, '$.expected_revision') = OLD.revision
               AND json_extract(event.payload_json, '$.expected_state') = OLD.state
               AND json_extract(event.payload_json, '$.expected_output_validity') = OLD.output_validity
               AND json_extract(event.payload_json, '$.new_state') = NEW.state
               AND json_extract(event.payload_json, '$.resulting_output_validity') = NEW.output_validity
               AND json_type(event.payload_json, '$.reason_code') = 'text'
               AND json_type(event.payload_json, '$.native_identity_attachment') IS NOT NULL)
              OR
              (event.event_type = 'frame.change_confirmed'
               AND NEW.receipt_event_id IS OLD.receipt_event_id
               AND EXISTS (
                   SELECT 1 FROM json_each(event.payload_json, '$.impact.run_impacts') impact
                   WHERE json_extract(impact.value, '$.run_id') = OLD.run_id
                     AND json_extract(impact.value, '$.expected_revision') = OLD.revision
                     AND json_extract(impact.value, '$.expected_state') = OLD.state
                     AND json_extract(impact.value, '$.expected_output_validity') = OLD.output_validity
                     AND json_extract(impact.value, '$.expected_last_event_id') = OLD.last_event_id
                     AND json_extract(impact.value, '$.expected_receipt_event_id') IS OLD.receipt_event_id
                     AND json_extract(impact.value, '$.resulting_revision') = NEW.revision
                     AND json_extract(impact.value, '$.resulting_state') = NEW.state
                     AND json_extract(impact.value, '$.resulting_output_validity') = NEW.output_validity
               ))
          )
    ) THEN RAISE(ABORT, 'service run transition must match one exact later event') END;
    SELECT CASE WHEN NEW.last_event_id IN (
      SELECT event_id FROM workbench_events
      WHERE task_id = OLD.task_id AND event_type = 'frame.change_confirmed'
    ) AND EXISTS (
      SELECT 1
      FROM workbench_events event, json_each(event.payload_json, '$.impact.run_impacts') impact
      WHERE event.task_id = OLD.task_id AND event.event_id = NEW.last_event_id
        AND json_extract(impact.value, '$.run_id') = OLD.run_id
        AND json_extract(impact.value, '$.action') = 'pause_requested'
        AND (
          json_type(OLD.input_manifest_json, '$.node_ids') IS NOT 'array'
          OR json_array_length(OLD.input_manifest_json, '$.node_ids') <>
             (SELECT COUNT(*) FROM service_run_inputs input WHERE input.run_id = OLD.run_id)
          OR EXISTS (
              SELECT 1 FROM json_each(OLD.input_manifest_json, '$.node_ids') manifest_node
              WHERE NOT EXISTS (
                  SELECT 1 FROM service_run_inputs input
                  WHERE input.run_id = OLD.run_id
                    AND input.ordinal = CAST(manifest_node.key AS INTEGER)
                    AND input.node_id = CAST(manifest_node.value AS TEXT)
              )
          )
        )
    ) THEN RAISE(ABORT, 'pause correction requires a complete input manifest') END;
    SELECT CASE WHEN NEW.last_event_id IN (
      SELECT event_id FROM workbench_events
      WHERE task_id = OLD.task_id AND event_type = 'frame.change_confirmed'
    ) AND NOT EXISTS (
      SELECT 1
      FROM workbench_events event, json_each(event.payload_json, '$.impact.run_impacts') impact
      WHERE event.task_id = OLD.task_id AND event.event_id = NEW.last_event_id
        AND json_extract(impact.value, '$.run_id') = OLD.run_id
        AND (
          (json_extract(impact.value, '$.action') = 'pause_requested'
           AND json_extract(impact.value, '$.reason_code') = 'dependency_changed'
           AND OLD.state IN ('queued','starting','running','waiting_owner','paused_dependency','verifying','interrupted')
           AND json_type(OLD.input_manifest_json, '$.node_ids') = 'array'
           AND json_array_length(OLD.input_manifest_json, '$.node_ids') = (
               SELECT COUNT(*) FROM service_run_inputs input WHERE input.run_id = OLD.run_id
           )
           AND NOT EXISTS (
               SELECT 1 FROM json_each(OLD.input_manifest_json, '$.node_ids') manifest_node
               WHERE NOT EXISTS (
                   SELECT 1 FROM service_run_inputs input
                   WHERE input.run_id = OLD.run_id
                     AND input.ordinal = CAST(manifest_node.key AS INTEGER)
                     AND input.node_id = CAST(manifest_node.value AS TEXT)
               )
           )
           AND NEW.state = 'paused_dependency'
           AND NEW.output_validity = CASE
               WHEN OLD.receipt_event_id IS NULL THEN OLD.output_validity
               WHEN OLD.output_validity = 'current' THEN 'stale'
               ELSE OLD.output_validity END)
          OR
          (json_extract(impact.value, '$.action') = 'repairing'
           AND json_extract(impact.value, '$.reason_code') = 'missing_input_manifest'
           AND OLD.state NOT IN ('complete','failed','canceled')
           AND (
               json_type(OLD.input_manifest_json, '$.node_ids') IS NOT 'array'
               OR json_array_length(OLD.input_manifest_json, '$.node_ids') <>
                  (SELECT COUNT(*) FROM service_run_inputs input WHERE input.run_id = OLD.run_id)
               OR EXISTS (
                   SELECT 1 FROM json_each(OLD.input_manifest_json, '$.node_ids') manifest_node
                   WHERE NOT EXISTS (
                       SELECT 1 FROM service_run_inputs input
                       WHERE input.run_id = OLD.run_id
                         AND input.ordinal = CAST(manifest_node.key AS INTEGER)
                         AND input.node_id = CAST(manifest_node.value AS TEXT)
                   )
               )
           )
           AND NEW.state = 'repairing'
           AND NEW.output_validity = CASE
               WHEN OLD.receipt_event_id IS NULL THEN OLD.output_validity
               WHEN OLD.output_validity = 'current' THEN 'stale'
               ELSE OLD.output_validity END)
          OR
          (json_extract(impact.value, '$.action') = 'require_reverification'
           AND json_extract(impact.value, '$.reason_code') = 'reverification_required'
           AND OLD.state = 'complete' AND NEW.state = 'complete'
           AND OLD.output_validity IN ('current','stale')
           AND NEW.output_validity = 'reverification_required')
        )
    ) THEN RAISE(ABORT, 'service run correction action, reason, state, or validity is illegal') END;
    SELECT CASE WHEN NEW.last_event_id IN (
      SELECT event_id FROM workbench_events
      WHERE task_id = OLD.task_id AND event_type = 'service_run.state_changed'
    ) AND NOT EXISTS (
      SELECT 1 FROM workbench_events event
      WHERE event.task_id = OLD.task_id AND event.event_id = NEW.last_event_id
        AND (
          (json_extract(event.payload_json, '$.reason_code') = 'dispatch_started'
           AND OLD.state = 'queued' AND NEW.state = 'starting')
          OR (json_extract(event.payload_json, '$.reason_code') = 'provider_attached'
           AND OLD.state = 'starting' AND NEW.state = 'running')
          OR (json_extract(event.payload_json, '$.reason_code') = 'owner_input_required'
           AND OLD.state IN ('running','repairing') AND NEW.state = 'waiting_owner')
          OR (json_extract(event.payload_json, '$.reason_code') = 'owner_input_received'
           AND OLD.state = 'waiting_owner' AND NEW.state = 'running')
          OR (json_extract(event.payload_json, '$.reason_code') = 'dependency_changed'
           AND OLD.state IN ('running','waiting_owner','repairing') AND NEW.state = 'paused_dependency')
          OR (json_extract(event.payload_json, '$.reason_code') = 'dependency_restored'
           AND OLD.state = 'paused_dependency' AND NEW.state = 'starting')
          OR (json_extract(event.payload_json, '$.reason_code') = 'repair_started'
           AND OLD.state IN ('starting','running','waiting_owner','paused_dependency','interrupted')
           AND NEW.state = 'repairing')
          OR (json_extract(event.payload_json, '$.reason_code') = 'retry_started'
           AND OLD.state IN ('repairing','interrupted') AND NEW.state = 'starting')
          OR (json_extract(event.payload_json, '$.reason_code') = 'recovery_reconciled'
           AND OLD.state IN ('repairing','interrupted') AND NEW.state = 'running')
          OR (json_extract(event.payload_json, '$.reason_code') = 'verification_started'
           AND OLD.state IN ('running','repairing') AND NEW.state = 'verifying')
          OR (json_extract(event.payload_json, '$.reason_code') = 'service_interrupted'
           AND OLD.state IN ('starting','running','waiting_owner','repairing') AND NEW.state = 'interrupted')
          OR (json_extract(event.payload_json, '$.reason_code') = 'service_completed'
           AND OLD.state IN ('running','verifying') AND NEW.state = 'complete')
          OR (json_extract(event.payload_json, '$.reason_code') = 'service_failed'
           AND OLD.state IN ('starting','running','repairing','verifying','interrupted') AND NEW.state = 'failed')
          OR (json_extract(event.payload_json, '$.reason_code') = 'owner_canceled'
           AND OLD.state IN ('queued','starting','running','waiting_owner','paused_dependency','repairing','verifying','interrupted')
           AND NEW.state = 'canceled')
          OR (json_extract(event.payload_json, '$.reason_code') = 'output_staled'
           AND OLD.state = 'complete' AND NEW.state = 'complete'
           AND OLD.output_validity = 'current' AND NEW.output_validity = 'stale')
          OR (json_extract(event.payload_json, '$.reason_code') = 'reverification_required'
           AND OLD.state = 'complete' AND NEW.state = 'complete'
           AND OLD.output_validity IN ('current','stale')
           AND NEW.output_validity = 'reverification_required')
        )
    ) THEN RAISE(ABORT, 'service run reason-bound state transition is illegal') END;
    SELECT CASE WHEN NEW.last_event_id IN (
      SELECT event_id FROM workbench_events
      WHERE task_id = OLD.task_id AND event_type = 'service_run.state_changed'
    ) AND NOT (
      (OLD.state = 'complete' AND NEW.state = 'complete'
       AND NEW.output_validity IN ('stale','reverification_required'))
      OR NEW.output_validity = OLD.output_validity
    ) THEN RAISE(ABORT, 'service run output validity transition is illegal') END;
    SELECT CASE WHEN OLD.started_at IS NULL AND NEW.state IN ('starting','running')
      AND NEW.started_at IS NOT NEW.updated_at
    THEN RAISE(ABORT, 'service run start time must derive from its first start event') END;
    SELECT CASE WHEN NOT (OLD.started_at IS NULL AND NEW.state IN ('starting','running'))
      AND NEW.started_at IS NOT OLD.started_at
    THEN RAISE(ABORT, 'service run start time is immutable after first start') END;
    SELECT CASE WHEN OLD.completed_at IS NULL AND NEW.state IN ('complete','failed','canceled')
      AND NEW.completed_at IS NOT NEW.updated_at
    THEN RAISE(ABORT, 'service run terminal time must derive from its terminal event') END;
    SELECT CASE WHEN NOT (OLD.completed_at IS NULL AND NEW.state IN ('complete','failed','canceled'))
      AND NEW.completed_at IS NOT OLD.completed_at
    THEN RAISE(ABORT, 'service run terminal time is immutable after first terminal transition') END;
    SELECT CASE WHEN OLD.state <> 'complete' AND NEW.state = 'complete' AND NOT (
      OLD.receipt_event_id IS NOT NULL
      AND OLD.last_event_id = OLD.receipt_event_id
      AND EXISTS (
          SELECT 1 FROM workbench_events receipt
          WHERE receipt.task_id = OLD.task_id AND receipt.event_id = OLD.receipt_event_id
            AND receipt.event_type = 'service_run.receipt_recorded'
            AND json_extract(receipt.payload_json, '$.run_id') = OLD.run_id
            AND json_extract(receipt.payload_json, '$.resulting_revision') = OLD.revision
            AND json_extract(receipt.payload_json, '$.receipt.run_revision') + 1 = OLD.revision
            AND json_extract(receipt.payload_json, '$.receipt.frame_version') = OLD.frame_version
            AND json_extract(receipt.payload_json, '$.receipt.input_manifest_checksum') =
                json_extract(OLD.input_manifest_json, '$.checksum')
      )
    ) THEN RAISE(ABORT, 'service run completion requires its exact immediate receipt event') END;
END;

CREATE TRIGGER service_runs_task3_no_delete
BEFORE DELETE ON service_runs
BEGIN
    SELECT RAISE(ABORT, 'service runs cannot be deleted');
END;

CREATE TRIGGER workbench_tasks_task3_transition
BEFORE UPDATE ON workbench_tasks
WHEN NEW.id IS NOT OLD.id
  OR NEW.title IS NOT OLD.title
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.state IS NOT OLD.state
  OR NEW.active_branch_id IS NOT OLD.active_branch_id
  OR NEW.current_frame_version IS NOT OLD.current_frame_version
  OR NEW.updated_at IS NOT OLD.updated_at
  OR NEW.last_transition_event_id IS NOT OLD.last_transition_event_id
BEGIN
    SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.title IS NOT OLD.title OR NEW.created_at IS NOT OLD.created_at
        THEN RAISE(ABORT, 'task title and creation identity are immutable') END;
    SELECT CASE WHEN NEW.last_transition_event_id IS NULL OR NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        LEFT JOIN workbench_events prior
          ON prior.task_id = OLD.id AND prior.event_id = OLD.last_transition_event_id
        WHERE event.task_id = OLD.id
          AND event.event_id = NEW.last_transition_event_id
          AND (prior.event_id IS NULL OR event.sequence > prior.sequence)
          AND event.created_at = NEW.updated_at
          AND event.event_type IN (
              'task.created','frame.change_confirmed','branch.activated','decision.enqueued','decision.resolved',
              'decision.superseded','service_run.registered','service_run.state_changed'
          )
          AND (
              event.event_type <> 'task.created'
              OR (
                  OLD.last_transition_event_id IS NULL
                  AND NEW.state = OLD.state
                  AND NEW.active_branch_id = OLD.active_branch_id
                  AND NEW.current_frame_version = OLD.current_frame_version
                   AND NEW.updated_at = OLD.updated_at
                   AND json_extract(event.payload_json, '$.title') = NEW.title
                   AND json_extract(event.payload_json, '$.initial_branch_id') = NEW.active_branch_id
                   AND event.sequence = 1
                   AND event.frame_version = 0
                   AND event.branch_id = NEW.active_branch_id
                   AND event.payload_json = json_object(
                       'initial_branch_id',NEW.active_branch_id,
                       'title',NEW.title
                   )
              )
          )
          AND (
              event.event_type <> 'branch.activated'
              OR (
                  event.branch_id = OLD.active_branch_id
                  AND json_extract(event.payload_json, '$.source_branch_id') = OLD.active_branch_id
                  AND json_extract(event.payload_json, '$.target_branch_id') = NEW.active_branch_id
                  AND json_extract(event.payload_json, '$.target_frame_version') = NEW.current_frame_version
                  AND json_extract(event.payload_json, '$.resulting_task_state') = NEW.state
              )
          )
          AND (
              event.event_type NOT IN (
                  'frame.change_confirmed','branch.activated','decision.enqueued','decision.resolved',
                  'decision.superseded','service_run.registered','service_run.state_changed'
              )
              OR (
                  json_type(event.payload_json, '$.expected_task_cache') = 'object'
                  AND json_extract(event.payload_json, '$.expected_task_cache.expected_selected_branch_id') = OLD.active_branch_id
                  AND json_extract(event.payload_json, '$.expected_task_cache.expected_frame_version') = OLD.current_frame_version
                  AND json_extract(event.payload_json, '$.expected_task_cache.expected_state') = OLD.state
                  AND json_extract(event.payload_json, '$.expected_task_cache.expected_updated_at') = OLD.updated_at
                  AND json_extract(event.payload_json, '$.expected_task_cache.expected_last_transition_event_id') IS OLD.last_transition_event_id
                  AND json_extract(event.payload_json, '$.resulting_task_state') = NEW.state
              )
          )
          AND (
              event.event_type <> 'frame.change_confirmed'
              OR (
                  event.branch_id = OLD.active_branch_id
                  AND NEW.active_branch_id = OLD.active_branch_id
                  AND json_extract(event.payload_json, '$.confirmed.frame_version') = NEW.current_frame_version
              )
          )
          AND (
              event.event_type NOT IN (
                  'decision.enqueued','decision.resolved','decision.superseded',
                  'service_run.registered','service_run.state_changed'
              )
              OR (
                  event.branch_id = OLD.active_branch_id
                  AND NEW.active_branch_id = OLD.active_branch_id
                  AND NEW.current_frame_version = OLD.current_frame_version
              )
          )
    ) THEN RAISE(ABORT, 'task cache update must match one exact later transition event') END;
END;

CREATE TRIGGER workbench_branches_task3_validate_insert
BEFORE INSERT ON workbench_branches
WHEN NEW.last_transition_event_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM workbench_events event
        WHERE event.task_id = NEW.task_id
          AND event.event_id = NEW.last_transition_event_id
          AND event.created_at = NEW.created_at
          AND (
              (
                  NEW.branch_id = 'main'
                  AND NEW.parent_branch_id IS NULL
                  AND NEW.created_by_event_id IS NULL
                  AND event.event_type = 'task.created'
                  AND event.sequence = 1
                  AND event.frame_version = 0
                  AND event.branch_id = 'main'
                  AND json_extract(event.payload_json, '$.initial_branch_id') = NEW.branch_id
                  AND event.payload_json = json_object(
                      'initial_branch_id',NEW.branch_id,
                      'title',(
                          SELECT task.title FROM workbench_tasks task WHERE task.id = NEW.task_id
                      )
                  )
              )
              OR (
                  NEW.branch_id <> 'main'
                  AND NEW.created_by_event_id = event.event_id
                  AND event.event_type = 'branch.forked'
                  AND event.branch_id = NEW.parent_branch_id
                  AND event.frame_version = NEW.forked_from_frame_version
                  AND event.sequence > NEW.forked_from_sequence
                  AND json_extract(event.payload_json, '$.new_branch_id') = NEW.branch_id
                  AND json_extract(event.payload_json, '$.parent_branch_id') = NEW.parent_branch_id
                  AND json_extract(event.payload_json, '$.forked_from_sequence') = NEW.forked_from_sequence
                  AND json_extract(event.payload_json, '$.forked_from_frame_version') = NEW.forked_from_frame_version
                  AND event.payload_json = json_object(
                      'forked_from_frame_version',NEW.forked_from_frame_version,
                      'forked_from_sequence',NEW.forked_from_sequence,
                      'new_branch_id',NEW.branch_id,
                      'parent_branch_id',NEW.parent_branch_id
                  )
              )
          )
    ) THEN RAISE(ABORT, 'branch creation transition must match its exact creation event') END;
END;

CREATE TRIGGER workbench_branches_task3_transition
BEFORE UPDATE ON workbench_branches
BEGIN
    SELECT CASE WHEN NEW.task_id IS NOT OLD.task_id
      OR NEW.branch_id IS NOT OLD.branch_id
      OR NEW.parent_branch_id IS NOT OLD.parent_branch_id
      OR NEW.forked_from_sequence IS NOT OLD.forked_from_sequence
      OR NEW.forked_from_frame_version IS NOT OLD.forked_from_frame_version
      OR NEW.created_by_event_id IS NOT OLD.created_by_event_id
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.status IS NOT OLD.status
    THEN RAISE(ABORT, 'Task-3 branch identity, ancestry, status, and creation transition are immutable') END;
    SELECT CASE WHEN NEW.last_transition_event_id IS NOT OLD.last_transition_event_id AND NOT (
      OLD.last_transition_event_id IS NULL
      AND NEW.last_transition_event_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM workbench_events event
          WHERE event.task_id = NEW.task_id
            AND event.event_id = NEW.last_transition_event_id
            AND event.created_at = NEW.created_at
            AND (
                (
                    NEW.branch_id = 'main'
                    AND NEW.parent_branch_id IS NULL
                    AND NEW.created_by_event_id IS NULL
                    AND event.event_type = 'task.created'
                    AND event.sequence = 1
                    AND event.frame_version = 0
                    AND event.branch_id = 'main'
                    AND json_extract(event.payload_json, '$.initial_branch_id') = NEW.branch_id
                    AND event.payload_json = json_object(
                        'initial_branch_id',NEW.branch_id,
                        'title',(
                            SELECT task.title FROM workbench_tasks task WHERE task.id = NEW.task_id
                        )
                    )
                )
                OR (
                    NEW.branch_id <> 'main'
                    AND NEW.created_by_event_id = event.event_id
                    AND event.event_type = 'branch.forked'
                    AND event.branch_id = NEW.parent_branch_id
                    AND event.frame_version = NEW.forked_from_frame_version
                    AND event.sequence > NEW.forked_from_sequence
                    AND json_extract(event.payload_json, '$.new_branch_id') = NEW.branch_id
                    AND json_extract(event.payload_json, '$.parent_branch_id') = NEW.parent_branch_id
                    AND json_extract(event.payload_json, '$.forked_from_sequence') = NEW.forked_from_sequence
                    AND json_extract(event.payload_json, '$.forked_from_frame_version') = NEW.forked_from_frame_version
                    AND event.payload_json = json_object(
                        'forked_from_frame_version',NEW.forked_from_frame_version,
                        'forked_from_sequence',NEW.forked_from_sequence,
                        'new_branch_id',NEW.branch_id,
                        'parent_branch_id',NEW.parent_branch_id
                    )
                )
            )
      )
    ) THEN RAISE(ABORT, 'Task-3 branch creation transition is one-time and exact') END;
END;

CREATE TRIGGER frame_nodes_task3_validate_insert
BEFORE INSERT ON frame_nodes
BEGIN
    SELECT CASE WHEN NEW.status IN ('proposed','rejected')
        THEN RAISE(ABORT, 'production frame nodes must be confirmed or invalidated') END;
    SELECT CASE WHEN NEW.status NOT IN ('confirmed','invalidated')
        THEN RAISE(ABORT, 'production frame nodes must be confirmed or invalidated') END;
    SELECT CASE WHEN NEW.value_json <> json(NEW.value_json)
      OR json_type(NEW.value_json) IS NULL
      OR NEW.depends_on_json <> json(NEW.depends_on_json)
      OR json_type(NEW.depends_on_json) <> 'array'
      OR NEW.provenance_event_ids_json <> json(NEW.provenance_event_ids_json)
      OR json_type(NEW.provenance_event_ids_json) <> 'array'
      OR NEW.provenance_json <> json(NEW.provenance_json)
      OR json_type(NEW.provenance_json) <> 'object'
    THEN RAISE(ABORT, 'frame node JSON must be canonical with exact top-level types') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        JOIN frame_proposals proposal
          ON proposal.task_id = NEW.task_id
         AND proposal.branch_id = NEW.branch_id
         AND proposal.id = json_extract(event.payload_json, '$.proposal_id')
         AND proposal.status = 'accepted'
         AND proposal.decided_by_event_id = event.event_id
        JOIN json_each(event.payload_json, '$.confirmed.nodes') confirmed
        JOIN json_each(proposal.changes_json, '$.node_operations') operation
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.created_by_event_id
          AND event.event_type = 'frame.change_confirmed'
          AND event.frame_version = NEW.frame_version
          AND event.created_at = NEW.created_at
          AND NEW.updated_at = event.created_at
          AND json_extract(confirmed.value, '$.node_id') = NEW.node_id
          AND json_extract(confirmed.value, '$.node_key') = NEW.node_key
          AND json_extract(confirmed.value, '$.task_id') = NEW.task_id
          AND json_extract(confirmed.value, '$.branch_id') = NEW.branch_id
          AND json_extract(confirmed.value, '$.frame_version') = NEW.frame_version
          AND json_extract(confirmed.value, '$.kind') = NEW.kind
          AND json_extract(confirmed.value, '$.text') = NEW.text
          AND confirmed.value -> '$.value' = NEW.value_json
          AND json_extract(confirmed.value, '$.status') = NEW.status
          AND json_extract(confirmed.value, '$.supersedes_node_id') IS NEW.supersedes_node_id
          AND confirmed.value -> '$.depends_on' = NEW.depends_on_json
          AND confirmed.value -> '$.provenance_event_ids' = NEW.provenance_event_ids_json
          AND json_extract(operation.value, '$.new_node_id') = NEW.node_id
          AND json_extract(operation.value, '$.node_key') = NEW.node_key
          AND operation.value -> '$.provenance' = NEW.provenance_json
          AND json_extract(operation.value, '$.expected_current_node_id') IS NEW.supersedes_node_id
          AND NEW.provenance_event_ids_json = COALESCE((
              SELECT json_group_array(source_id)
              FROM (
                  SELECT DISTINCT source_id
                  FROM (
                      SELECT CAST(predecessor_source.value AS TEXT) AS source_id
                      FROM frame_nodes predecessor, json_each(predecessor.provenance_event_ids_json) predecessor_source
                      WHERE predecessor.task_id = NEW.task_id
                        AND predecessor.node_id = NEW.supersedes_node_id
                      UNION ALL
                      SELECT CAST(operation_source.value AS TEXT) AS source_id
                      FROM json_each(operation.value, '$.provenance.source_event_ids') operation_source
                  )
                  ORDER BY source_id
              )
          ), '[]')
          AND NEW.depends_on_json = COALESCE((
              SELECT json_group_array(target_node_id)
              FROM (
                  SELECT json_extract(edge.value, '$.to_node_id') AS target_node_id
                  FROM json_each(event.payload_json, '$.confirmed.edges') edge
                  WHERE json_extract(edge.value, '$.from_node_id') = NEW.node_id
                    AND json_extract(edge.value, '$.relation') = 'depends_on'
                  ORDER BY target_node_id
              )
          ), '[]')
          AND (
              (NEW.status = 'confirmed'
               AND NEW.invalidated_by_event_id IS NULL
               AND json_extract(operation.value, '$.operation_kind') = 'upsert'
               AND json_extract(operation.value, '$.kind') = NEW.kind
               AND json_extract(operation.value, '$.text') = NEW.text
               AND operation.value -> '$.value' = NEW.value_json
               AND json_type(operation.value, '$.depends_on_node_keys') = 'array'
               AND json_array_length(operation.value, '$.depends_on_node_keys') =
                   json_array_length(NEW.depends_on_json)
               AND NOT EXISTS (
                   SELECT 1 FROM json_each(operation.value, '$.depends_on_node_keys') dependency_key
                   WHERE NOT EXISTS (
                       SELECT 1
                       FROM json_each(event.payload_json, '$.confirmed.nodes') dependency_node,
                            json_each(event.payload_json, '$.confirmed.edges') dependency_edge
                       WHERE json_extract(dependency_node.value, '$.node_key') = dependency_key.value
                         AND json_extract(dependency_edge.value, '$.from_node_id') = NEW.node_id
                         AND json_extract(dependency_edge.value, '$.to_node_id') =
                             json_extract(dependency_node.value, '$.node_id')
                         AND json_extract(dependency_edge.value, '$.relation') = 'depends_on'
                   )
               ))
              OR
              (NEW.status = 'invalidated'
               AND NEW.invalidated_by_event_id = event.event_id
               AND json_extract(operation.value, '$.operation_kind') = 'invalidate'
               AND NEW.depends_on_json = '[]'
               AND json_extract(operation.value, '$.expected_current_node_id') = NEW.supersedes_node_id
               AND EXISTS (
                   SELECT 1 FROM frame_nodes predecessor
                   WHERE predecessor.task_id = NEW.task_id
                     AND predecessor.node_id = NEW.supersedes_node_id
                     AND predecessor.node_key = NEW.node_key
                     AND predecessor.kind = NEW.kind
                     AND predecessor.text = NEW.text
                     AND predecessor.value_json = NEW.value_json
               ))
          )
    ) THEN RAISE(ABORT, 'frame node must match the exact confirmation, proposal operation, and tombstone mapping') END;
END;

CREATE TRIGGER frame_edges_task3_validate_insert
BEFORE INSERT ON frame_edges
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event, json_each(event.payload_json, '$.confirmed.edges') edge
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.created_by_event_id
          AND event.event_type = 'frame.change_confirmed'
          AND event.frame_version = NEW.frame_version
          AND event.created_at = NEW.created_at
          AND json_extract(edge.value, '$.edge_id') = NEW.id
          AND json_extract(edge.value, '$.task_id') = NEW.task_id
          AND json_extract(edge.value, '$.branch_id') = NEW.branch_id
          AND json_extract(edge.value, '$.frame_version') = NEW.frame_version
          AND json_extract(edge.value, '$.from_node_id') = NEW.from_node_id
          AND json_extract(edge.value, '$.to_node_id') = NEW.to_node_id
          AND json_extract(edge.value, '$.relation') = NEW.edge_type
    ) THEN RAISE(ABORT, 'frame edge must match the exact confirmed full edge snapshot') END;
END;

CREATE TRIGGER evidence_invalidation_overlays_validate_insert
BEFORE INSERT ON evidence_invalidation_overlays
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM evidence_refs evidence
        JOIN workbench_events invalidation
          ON invalidation.task_id = NEW.task_id
         AND invalidation.event_id = NEW.invalidated_by_event_id
         AND invalidation.sequence = NEW.invalidated_by_event_sequence
        JOIN workbench_events observation
          ON observation.task_id = evidence.task_id
         AND observation.event_id = evidence.observed_head_event_id
         AND observation.sequence = evidence.observed_head_sequence
        WHERE evidence.task_id = NEW.task_id
          AND evidence.id = NEW.evidence_id
          AND invalidation.branch_id = NEW.invalidation_branch_id
          AND invalidation.event_type = 'frame.change_confirmed'
          AND invalidation.sequence > observation.sequence
          AND invalidation.created_at = NEW.invalidated_at
          AND EXISTS (
              SELECT 1 FROM json_each(invalidation.payload_json, '$.impact.evidence_impacts') impact
              WHERE json_extract(impact.value, '$.evidence_id') = NEW.evidence_id
                AND json_extract(impact.value, '$.invalidation_reason') = NEW.invalidation_reason
          )
          AND EXISTS (
              WITH RECURSIVE lineage(branch_id, sequence_cap) AS (
                  SELECT NEW.invalidation_branch_id, invalidation.sequence
                  UNION ALL
                  SELECT branch.parent_branch_id, min(lineage.sequence_cap, branch.forked_from_sequence)
                  FROM lineage
                  JOIN workbench_branches branch
                    ON branch.task_id = NEW.task_id AND branch.branch_id = lineage.branch_id
                  WHERE branch.parent_branch_id IS NOT NULL
              )
              SELECT 1 FROM lineage
              WHERE lineage.branch_id = observation.branch_id
                AND observation.sequence <= lineage.sequence_cap
          )
    ) THEN RAISE(ABORT, 'evidence invalidation overlay must match a later visible frame confirmation impact') END;
END;

CREATE TRIGGER evidence_invalidation_overlays_no_update
BEFORE UPDATE ON evidence_invalidation_overlays
BEGIN
    SELECT RAISE(ABORT, 'evidence invalidation overlays are immutable');
END;

CREATE TRIGGER evidence_invalidation_overlays_no_delete
BEFORE DELETE ON evidence_invalidation_overlays
BEGIN
    SELECT RAISE(ABORT, 'evidence invalidation overlays cannot be deleted');
END;
