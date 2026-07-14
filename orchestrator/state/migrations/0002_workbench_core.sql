CREATE TABLE workbench_tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('intake','frame_pending','ready','running','waiting_owner','verifying','complete','repairing')),
    active_branch_id TEXT NOT NULL,
    current_frame_version INTEGER NOT NULL DEFAULT 0 CHECK (current_frame_version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (id, active_branch_id)
        REFERENCES workbench_branches(task_id, branch_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE workbench_branches (
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    parent_branch_id TEXT,
    forked_from_sequence INTEGER CHECK (forked_from_sequence IS NULL OR forked_from_sequence >= 0),
    forked_from_frame_version INTEGER CHECK (forked_from_frame_version IS NULL OR forked_from_frame_version >= 0),
    created_by_event_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('active','merged','abandoned')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, branch_id),
    FOREIGN KEY (task_id, parent_branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, created_by_event_id) REFERENCES workbench_events(task_id, event_id)
);

CREATE TABLE workbench_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_type TEXT NOT NULL,
    event_schema_version INTEGER NOT NULL CHECK (event_schema_version > 0),
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    cause TEXT,
    caused_by TEXT,
    command_id TEXT NOT NULL,
    command_sequence INTEGER NOT NULL CHECK (command_sequence > 0),
    frame_version INTEGER NOT NULL CHECK (frame_version >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    idempotency_key TEXT NOT NULL,
    prior_checksum TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, event_id),
    UNIQUE (task_id, event_id, sequence),
    UNIQUE (task_id, sequence),
    UNIQUE (task_id, idempotency_key),
    UNIQUE (task_id, command_id, command_sequence),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, caused_by) REFERENCES workbench_events(task_id, event_id)
);

CREATE INDEX idx_workbench_events_task_branch_sequence
ON workbench_events(task_id, branch_id, sequence);

CREATE TRIGGER workbench_events_cause_must_be_earlier
BEFORE INSERT ON workbench_events
WHEN NEW.caused_by IS NOT NULL
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM workbench_events predecessor
        WHERE predecessor.task_id = NEW.task_id
          AND predecessor.event_id = NEW.caused_by
          AND predecessor.sequence >= NEW.sequence
    ) THEN RAISE(ABORT, 'caused_by must reference an earlier event in the same task') END;
END;

CREATE TRIGGER workbench_events_no_update
BEFORE UPDATE ON workbench_events
BEGIN
    SELECT RAISE(ABORT, 'workbench_events is append-only');
END;

CREATE TRIGGER workbench_events_no_delete
BEFORE DELETE ON workbench_events
BEGIN
    SELECT RAISE(ABORT, 'workbench_events is append-only');
END;

CREATE TABLE projection_metadata (
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    projection_name TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    head_sequence INTEGER NOT NULL DEFAULT 0 CHECK (head_sequence >= 0),
    head_event_checksum TEXT,
    canonical_state_json TEXT NOT NULL CHECK (json_valid(canonical_state_json)),
    state_checksum TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, projection_name, branch_id),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id)
);

CREATE TABLE frame_nodes (
    -- node_id identifies one immutable revision; node_key identifies the logical
    -- node across revisions and inherited branch lineage.
    node_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    frame_version INTEGER NOT NULL CHECK (frame_version >= 0),
    node_key TEXT NOT NULL,
    supersedes_node_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('goal','success','constraint','assumption','alternative','authority','evidence_gap','decision','action')),
    text TEXT NOT NULL,
    value_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(value_json)),
    status TEXT NOT NULL CHECK (status IN ('proposed','confirmed','rejected','invalidated')),
    depends_on_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(depends_on_json)),
    provenance_event_ids_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(provenance_event_ids_json)),
    provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
    created_by_event_id TEXT,
    invalidated_by_event_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (task_id, node_id),
    UNIQUE (task_id, node_id, frame_version),
    UNIQUE (task_id, branch_id, frame_version, node_key),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, supersedes_node_id) REFERENCES frame_nodes(task_id, node_id),
    FOREIGN KEY (task_id, created_by_event_id) REFERENCES workbench_events(task_id, event_id),
    FOREIGN KEY (task_id, invalidated_by_event_id) REFERENCES workbench_events(task_id, event_id)
);

CREATE TRIGGER frame_nodes_supersedes_same_logical_node
BEFORE INSERT ON frame_nodes
WHEN NEW.supersedes_node_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM frame_nodes predecessor
        WHERE predecessor.task_id = NEW.task_id
          AND predecessor.node_id = NEW.supersedes_node_id
          AND predecessor.node_key = NEW.node_key
          AND predecessor.frame_version < NEW.frame_version
    ) THEN RAISE(ABORT, 'supersedes_node_id must be an earlier revision of the same node_key') END;
END;

CREATE TRIGGER frame_nodes_no_update
BEFORE UPDATE ON frame_nodes
BEGIN
    SELECT RAISE(ABORT, 'frame node revisions are immutable');
END;

CREATE TRIGGER frame_nodes_no_delete
BEFORE DELETE ON frame_nodes
BEGIN
    SELECT RAISE(ABORT, 'frame node revisions are immutable');
END;

CREATE INDEX idx_frame_nodes_task_branch_version
ON frame_nodes(task_id, branch_id, frame_version);

CREATE TABLE frame_edges (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    frame_version INTEGER NOT NULL CHECK (frame_version >= 0),
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL CHECK (edge_type IN ('depends_on','alternative_to','supports','contradicts')),
    created_by_event_id TEXT,
    created_at TEXT NOT NULL,
    CHECK (from_node_id <> to_node_id),
    UNIQUE (task_id, branch_id, frame_version, from_node_id, to_node_id, edge_type),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, from_node_id) REFERENCES frame_nodes(task_id, node_id),
    FOREIGN KEY (task_id, to_node_id) REFERENCES frame_nodes(task_id, node_id),
    FOREIGN KEY (task_id, created_by_event_id) REFERENCES workbench_events(task_id, event_id)
);

CREATE INDEX idx_frame_edges_from
ON frame_edges(task_id, branch_id, from_node_id, frame_version);

CREATE INDEX idx_frame_edges_to
ON frame_edges(task_id, branch_id, to_node_id, frame_version);

CREATE TABLE frame_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    base_version INTEGER NOT NULL CHECK (base_version >= 0),
    proposed_version INTEGER NOT NULL CHECK (proposed_version > base_version),
    expected_head_sequence INTEGER NOT NULL CHECK (expected_head_sequence >= 0),
    base_head_checksum TEXT NOT NULL,
    preview_state_checksum TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected','superseded')),
    changes_json TEXT NOT NULL CHECK (json_valid(changes_json)),
    impact_preview_json TEXT NOT NULL CHECK (json_valid(impact_preview_json)),
    provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
    created_by TEXT NOT NULL,
    created_by_event_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, created_by_event_id) REFERENCES workbench_events(task_id, event_id)
);

CREATE TABLE decision_requests (
    decision_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','active','resolved','superseded')),
    tier TEXT NOT NULL CHECK (tier IN ('routine','blocking','critical')),
    kind TEXT NOT NULL CHECK (kind IN (
        'intent_clarification',
        'tool_approval',
        'external_action_approval',
        'evidence_checkpoint',
        'service_team_override',
        'frame_interpretation_confirmation'
    )),
    queue_order INTEGER NOT NULL CHECK (queue_order >= 0),
    semantic_identity TEXT NOT NULL,
    question TEXT NOT NULL,
    affected_node_ids_json TEXT NOT NULL CHECK (json_valid(affected_node_ids_json)),
    options_json TEXT NOT NULL CHECK (json_valid(options_json)),
    free_form_allowed INTEGER NOT NULL DEFAULT 1 CHECK (free_form_allowed IN (0,1)),
    recommendation TEXT,
    changed_outcome TEXT NOT NULL,
    outcome_deltas_json TEXT NOT NULL CHECK (json_valid(outcome_deltas_json)),
    materiality_json TEXT NOT NULL CHECK (json_valid(materiality_json)),
    consequence_if_unresolved TEXT NOT NULL,
    consequence_json TEXT NOT NULL CHECK (json_valid(consequence_json)),
    provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
    default_option_json TEXT CHECK (default_option_json IS NULL OR json_valid(default_option_json)),
    created_by_event_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_text TEXT,
    resolution_json TEXT CHECK (resolution_json IS NULL OR json_valid(resolution_json)),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, created_by_event_id) REFERENCES workbench_events(task_id, event_id)
);

CREATE UNIQUE INDEX idx_decision_requests_active_identity
ON decision_requests(task_id, branch_id, semantic_identity)
WHERE state IN ('queued', 'active');

CREATE UNIQUE INDEX idx_decision_requests_one_active
ON decision_requests(task_id, branch_id)
WHERE state = 'active';

CREATE UNIQUE INDEX idx_decision_requests_open_queue_order
ON decision_requests(task_id, branch_id, queue_order)
WHERE state IN ('queued', 'active');

CREATE TABLE service_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    service TEXT NOT NULL CHECK (service IN ('claude','codex','hermes','gemini','local')),
    role TEXT NOT NULL,
    frame_version INTEGER NOT NULL CHECK (frame_version >= 0),
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
    launch_origin TEXT CHECK (launch_origin IS NULL OR launch_origin IN ('governed','direct_unmanaged')),
    native_handle_json TEXT CHECK (native_handle_json IS NULL OR json_valid(native_handle_json)),
    branch_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','starting','running','waiting_owner','paused_dependency','repairing','verifying','interrupted','complete','failed','canceled')),
    receipt_event_id TEXT,
    input_manifest_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_manifest_json)),
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (task_id, run_id),
    UNIQUE (task_id, run_id, frame_version),
    UNIQUE (task_id, run_id, branch_id, frame_version),
    CHECK (
        (
            native_session_id IS NULL AND native_thread_id IS NULL AND native_turn_id IS NULL
            AND native_request_id IS NULL AND native_tool_use_id IS NULL AND native_question_group_id IS NULL
            AND native_handle_json IS NULL
        )
        OR (
            adapter_provider IS NOT NULL AND length(trim(adapter_provider)) > 0
            AND adapter_contract_revision IS NOT NULL AND length(trim(adapter_contract_revision)) > 0
            AND account_id IS NOT NULL AND length(trim(account_id)) > 0
            AND profile_id IS NOT NULL AND length(trim(profile_id)) > 0
            AND model_id IS NOT NULL AND length(trim(model_id)) > 0
            AND capability_inventory_revision IS NOT NULL AND length(trim(capability_inventory_revision)) > 0
            AND transport_generation IS NOT NULL AND length(trim(transport_generation)) > 0
            AND native_session_id IS NOT NULL AND length(trim(native_session_id)) > 0
            AND native_thread_id IS NOT NULL AND length(trim(native_thread_id)) > 0
            AND launch_origin IS NOT NULL AND length(trim(launch_origin)) > 0
        )
    ),
    CHECK (
        native_request_id IS NULL
        OR (native_turn_id IS NOT NULL AND length(trim(native_turn_id)) > 0)
    ),
    CHECK (
        native_tool_use_id IS NULL
        OR (native_request_id IS NOT NULL AND length(trim(native_request_id)) > 0)
    ),
    CHECK (
        native_question_group_id IS NULL
        OR (native_request_id IS NOT NULL AND length(trim(native_request_id)) > 0)
    ),
    CHECK (native_session_id IS NULL OR length(trim(native_session_id)) > 0),
    CHECK (native_thread_id IS NULL OR length(trim(native_thread_id)) > 0),
    CHECK (native_turn_id IS NULL OR length(trim(native_turn_id)) > 0),
    CHECK (native_request_id IS NULL OR length(trim(native_request_id)) > 0),
    CHECK (native_tool_use_id IS NULL OR length(trim(native_tool_use_id)) > 0),
    CHECK (native_question_group_id IS NULL OR length(trim(native_question_group_id)) > 0),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, receipt_event_id) REFERENCES workbench_events(task_id, event_id)
);

CREATE INDEX idx_service_runs_task_branch_state
ON service_runs(task_id, branch_id, state);

CREATE INDEX idx_service_runs_native_session_identity
ON service_runs(adapter_provider, account_id, profile_id, native_session_id)
WHERE native_session_id IS NOT NULL;

CREATE UNIQUE INDEX idx_service_runs_native_request_identity
ON service_runs(
    adapter_provider, account_id, profile_id, native_session_id, native_thread_id, native_turn_id,
    native_request_id
)
WHERE native_request_id IS NOT NULL;

CREATE UNIQUE INDEX idx_service_runs_native_tool_identity
ON service_runs(
    adapter_provider, account_id, profile_id, native_session_id, native_thread_id, native_turn_id,
    native_request_id, native_tool_use_id
)
WHERE native_tool_use_id IS NOT NULL;

CREATE UNIQUE INDEX idx_service_runs_native_question_identity
ON service_runs(
    adapter_provider, account_id, profile_id, native_session_id, native_thread_id, native_turn_id,
    native_request_id, native_question_group_id
)
WHERE native_question_group_id IS NOT NULL;

CREATE INDEX idx_service_runs_open_native_request
ON service_runs(
    adapter_provider, account_id, profile_id, model_id, native_thread_id, native_turn_id,
    native_request_id, state
)
WHERE native_request_id IS NOT NULL
  AND state IN ('starting','running','waiting_owner','paused_dependency','repairing','verifying','interrupted');

CREATE TABLE service_run_inputs (
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    input_frame_version INTEGER NOT NULL CHECK (input_frame_version >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (run_id, node_id),
    UNIQUE (run_id, ordinal),
    FOREIGN KEY (task_id, run_id) REFERENCES service_runs(task_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (task_id, run_id, input_frame_version)
        REFERENCES service_runs(task_id, run_id, frame_version),
    FOREIGN KEY (task_id, node_id) REFERENCES frame_nodes(task_id, node_id)
);

CREATE INDEX idx_service_run_inputs_node
ON service_run_inputs(node_id, run_id);

CREATE TABLE evidence_refs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    success_node_id TEXT NOT NULL,
    success_node_frame_version INTEGER NOT NULL CHECK (success_node_frame_version >= 0),
    frame_version INTEGER NOT NULL CHECK (frame_version >= 0),
    observed_head_event_id TEXT NOT NULL,
    observed_head_sequence INTEGER NOT NULL CHECK (observed_head_sequence >= 0),
    predicate_id TEXT NOT NULL,
    predicate_text TEXT,
    predicate_json TEXT CHECK (predicate_json IS NULL OR json_valid(predicate_json)),
    expected_outcome TEXT NOT NULL,
    authority_kind TEXT NOT NULL,
    authority_locator TEXT NOT NULL,
    verifier_kind TEXT NOT NULL,
    verifier_identity TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('pass','fail','conflict','inconclusive')),
    observed_at TEXT NOT NULL,
    valid_until TEXT,
    observed_value_checksum TEXT NOT NULL,
    content_checksum TEXT,
    source_event_id TEXT,
    source_event_sequence INTEGER CHECK (source_event_sequence IS NULL OR source_event_sequence > 0),
    producing_run_id TEXT,
    invalidated_at TEXT,
    invalidated_event_id TEXT,
    invalidated_event_sequence INTEGER CHECK (invalidated_event_sequence IS NULL OR invalidated_event_sequence > 0),
    invalidation_reason TEXT,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    CHECK (predicate_text IS NOT NULL OR predicate_json IS NOT NULL),
    CHECK (
        (source_event_id IS NULL AND source_event_sequence IS NULL)
        OR (source_event_id IS NOT NULL AND source_event_sequence IS NOT NULL)
    ),
    CHECK (
        (invalidated_at IS NULL AND invalidated_event_id IS NULL AND invalidated_event_sequence IS NULL
            AND invalidation_reason IS NULL)
        OR (invalidated_at IS NOT NULL AND invalidated_event_id IS NOT NULL
            AND invalidated_event_sequence IS NOT NULL AND invalidation_reason IS NOT NULL)
    ),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, success_node_id, success_node_frame_version)
        REFERENCES frame_nodes(task_id, node_id, frame_version),
    FOREIGN KEY (task_id, observed_head_event_id, observed_head_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence),
    FOREIGN KEY (task_id, source_event_id, source_event_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence),
    FOREIGN KEY (task_id, producing_run_id, branch_id, frame_version)
        REFERENCES service_runs(task_id, run_id, branch_id, frame_version),
    FOREIGN KEY (task_id, invalidated_event_id, invalidated_event_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence)
);

CREATE TRIGGER evidence_refs_validate_insert
BEFORE INSERT ON evidence_refs
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        WITH RECURSIVE lineage(branch_id, sequence_cap, frame_cap) AS (
            SELECT NEW.branch_id, NEW.observed_head_sequence, NEW.frame_version
            UNION ALL
            SELECT child.parent_branch_id,
                   min(lineage.sequence_cap, child.forked_from_sequence),
                   min(lineage.frame_cap, child.forked_from_frame_version)
            FROM lineage
            JOIN workbench_branches child
              ON child.task_id = NEW.task_id AND child.branch_id = lineage.branch_id
            WHERE child.parent_branch_id IS NOT NULL
              AND child.forked_from_sequence IS NOT NULL
              AND child.forked_from_frame_version IS NOT NULL
        )
        SELECT 1
        FROM workbench_events head
        JOIN lineage ON lineage.branch_id = head.branch_id
        WHERE head.task_id = NEW.task_id
          AND head.event_id = NEW.observed_head_event_id
          AND head.sequence = NEW.observed_head_sequence
          AND head.frame_version = NEW.frame_version
          AND head.sequence <= lineage.sequence_cap
          AND head.frame_version <= lineage.frame_cap
    ) THEN RAISE(ABORT, 'observed head must be an exact visible event at the observation frame') END;

    SELECT CASE WHEN NOT EXISTS (
        WITH RECURSIVE lineage(branch_id, sequence_cap, frame_cap) AS (
            SELECT NEW.branch_id, NEW.observed_head_sequence, NEW.frame_version
            UNION ALL
            SELECT child.parent_branch_id,
                   min(lineage.sequence_cap, child.forked_from_sequence),
                   min(lineage.frame_cap, child.forked_from_frame_version)
            FROM lineage
            JOIN workbench_branches child
              ON child.task_id = NEW.task_id AND child.branch_id = lineage.branch_id
            WHERE child.parent_branch_id IS NOT NULL
              AND child.forked_from_sequence IS NOT NULL
              AND child.forked_from_frame_version IS NOT NULL
        )
        SELECT 1
        FROM frame_nodes node
        JOIN workbench_events created
          ON created.task_id = node.task_id
         AND created.event_id = node.created_by_event_id
         AND created.branch_id = node.branch_id
        JOIN lineage ON lineage.branch_id = node.branch_id
        WHERE node.task_id = NEW.task_id
          AND node.node_id = NEW.success_node_id
          AND node.frame_version = NEW.success_node_frame_version
          AND node.kind = 'success'
          AND node.frame_version <= lineage.frame_cap
          AND created.sequence <= lineage.sequence_cap
          AND created.sequence <= NEW.observed_head_sequence
          AND created.frame_version = node.frame_version
          AND created.frame_version <= lineage.frame_cap
    ) THEN RAISE(ABORT, 'success evidence must reference a visible anchored success-node revision') END;

    SELECT CASE WHEN NEW.source_event_id IS NOT NULL AND NOT EXISTS (
        WITH RECURSIVE lineage(branch_id, sequence_cap, frame_cap) AS (
            SELECT NEW.branch_id, NEW.observed_head_sequence, NEW.frame_version
            UNION ALL
            SELECT child.parent_branch_id,
                   min(lineage.sequence_cap, child.forked_from_sequence),
                   min(lineage.frame_cap, child.forked_from_frame_version)
            FROM lineage
            JOIN workbench_branches child
              ON child.task_id = NEW.task_id AND child.branch_id = lineage.branch_id
            WHERE child.parent_branch_id IS NOT NULL
              AND child.forked_from_sequence IS NOT NULL
              AND child.forked_from_frame_version IS NOT NULL
        )
        SELECT 1
        FROM workbench_events source
        JOIN lineage ON lineage.branch_id = source.branch_id
        WHERE source.task_id = NEW.task_id
          AND source.event_id = NEW.source_event_id
          AND source.sequence = NEW.source_event_sequence
          AND source.sequence <= NEW.observed_head_sequence
          AND source.sequence <= lineage.sequence_cap
          AND source.frame_version <= lineage.frame_cap
    ) THEN RAISE(ABORT, 'source event must be visible at the observation head') END;

    SELECT CASE WHEN NEW.invalidated_event_id IS NOT NULL AND NOT EXISTS (
        WITH RECURSIVE lineage(branch_id, sequence_cap, frame_cap) AS (
            SELECT NEW.branch_id, 9223372036854775807, 9223372036854775807
            UNION ALL
            SELECT child.parent_branch_id,
                   min(lineage.sequence_cap, child.forked_from_sequence),
                   min(lineage.frame_cap, child.forked_from_frame_version)
            FROM lineage
            JOIN workbench_branches child
              ON child.task_id = NEW.task_id AND child.branch_id = lineage.branch_id
            WHERE child.parent_branch_id IS NOT NULL
              AND child.forked_from_sequence IS NOT NULL
              AND child.forked_from_frame_version IS NOT NULL
        )
        SELECT 1
        FROM workbench_events invalidation
        JOIN lineage ON lineage.branch_id = invalidation.branch_id
        WHERE invalidation.task_id = NEW.task_id
          AND invalidation.event_id = NEW.invalidated_event_id
          AND invalidation.sequence = NEW.invalidated_event_sequence
          AND invalidation.sequence > NEW.observed_head_sequence
          AND invalidation.sequence <= lineage.sequence_cap
          AND invalidation.frame_version <= lineage.frame_cap
    ) THEN RAISE(ABORT, 'invalidation event must be later and visible on the evidence branch') END;
END;

CREATE TRIGGER evidence_refs_only_complete_invalidation_update
BEFORE UPDATE ON evidence_refs
WHEN OLD.invalidated_at IS NOT NULL
  OR NEW.invalidated_at IS NULL
  OR NEW.invalidated_event_id IS NULL
  OR NEW.invalidated_event_sequence IS NULL
  OR NEW.invalidation_reason IS NULL
  OR NEW.id IS NOT OLD.id
  OR NEW.task_id IS NOT OLD.task_id
  OR NEW.branch_id IS NOT OLD.branch_id
  OR NEW.success_node_id IS NOT OLD.success_node_id
  OR NEW.success_node_frame_version IS NOT OLD.success_node_frame_version
  OR NEW.frame_version IS NOT OLD.frame_version
  OR NEW.observed_head_event_id IS NOT OLD.observed_head_event_id
  OR NEW.observed_head_sequence IS NOT OLD.observed_head_sequence
  OR NEW.predicate_id IS NOT OLD.predicate_id
  OR NEW.predicate_text IS NOT OLD.predicate_text
  OR NEW.predicate_json IS NOT OLD.predicate_json
  OR NEW.expected_outcome IS NOT OLD.expected_outcome
  OR NEW.authority_kind IS NOT OLD.authority_kind
  OR NEW.authority_locator IS NOT OLD.authority_locator
  OR NEW.verifier_kind IS NOT OLD.verifier_kind
  OR NEW.verifier_identity IS NOT OLD.verifier_identity
  OR NEW.verification_status IS NOT OLD.verification_status
  OR NEW.observed_at IS NOT OLD.observed_at
  OR NEW.valid_until IS NOT OLD.valid_until
  OR NEW.observed_value_checksum IS NOT OLD.observed_value_checksum
  OR NEW.content_checksum IS NOT OLD.content_checksum
  OR NEW.source_event_id IS NOT OLD.source_event_id
  OR NEW.source_event_sequence IS NOT OLD.source_event_sequence
  OR NEW.producing_run_id IS NOT OLD.producing_run_id
  OR NEW.metadata_json IS NOT OLD.metadata_json
BEGIN
    SELECT RAISE(ABORT, 'evidence is immutable except for one complete invalidation transition');
END;

CREATE TRIGGER evidence_refs_validate_invalidation_update
BEFORE UPDATE OF invalidated_at, invalidated_event_id, invalidated_event_sequence, invalidation_reason
ON evidence_refs
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        WITH RECURSIVE lineage(branch_id, sequence_cap, frame_cap) AS (
            SELECT NEW.branch_id, 9223372036854775807, 9223372036854775807
            UNION ALL
            SELECT child.parent_branch_id,
                   min(lineage.sequence_cap, child.forked_from_sequence),
                   min(lineage.frame_cap, child.forked_from_frame_version)
            FROM lineage
            JOIN workbench_branches child
              ON child.task_id = NEW.task_id AND child.branch_id = lineage.branch_id
            WHERE child.parent_branch_id IS NOT NULL
              AND child.forked_from_sequence IS NOT NULL
              AND child.forked_from_frame_version IS NOT NULL
        )
        SELECT 1
        FROM workbench_events invalidation
        JOIN lineage ON lineage.branch_id = invalidation.branch_id
        WHERE invalidation.task_id = NEW.task_id
          AND invalidation.event_id = NEW.invalidated_event_id
          AND invalidation.sequence = NEW.invalidated_event_sequence
          AND invalidation.sequence > NEW.observed_head_sequence
          AND invalidation.sequence <= lineage.sequence_cap
          AND invalidation.frame_version <= lineage.frame_cap
    ) THEN RAISE(ABORT, 'invalidation event must be later and visible on the evidence branch') END;
END;

CREATE TRIGGER evidence_refs_no_delete
BEFORE DELETE ON evidence_refs
BEGIN
    SELECT RAISE(ABORT, 'evidence records cannot be deleted');
END;

CREATE INDEX idx_evidence_refs_current_success
ON evidence_refs(task_id, branch_id, success_node_id, verification_status, valid_until, observed_at)
WHERE invalidated_at IS NULL;

CREATE INDEX idx_evidence_refs_predicate
ON evidence_refs(task_id, branch_id, predicate_id, observed_at);

CREATE TABLE learning_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    proposal_kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected','quarantined')),
    proposal_json TEXT NOT NULL CHECK (json_valid(proposal_json)),
    evidence_ref_ids_json TEXT NOT NULL CHECK (json_valid(evidence_ref_ids_json)),
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE replacement_bootstrap_proofs (
    proof_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    component_type TEXT NOT NULL,
    component_id TEXT NOT NULL,
    replacement_build_id TEXT NOT NULL,
    proof_kind TEXT NOT NULL CHECK (proof_kind = 'live_bootstrap'),
    status TEXT NOT NULL CHECK (status = 'succeeded'),
    authority_kind TEXT NOT NULL,
    authority_locator TEXT NOT NULL,
    verifier_kind TEXT NOT NULL,
    verifier_identity TEXT NOT NULL,
    proof_event_id TEXT NOT NULL,
    proof_event_sequence INTEGER NOT NULL CHECK (proof_event_sequence > 0),
    proof_json TEXT NOT NULL CHECK (json_valid(proof_json)),
    proof_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (length(trim(proof_id)) > 0),
    CHECK (length(trim(receipt_id)) > 0),
    CHECK (length(trim(component_type)) > 0),
    CHECK (length(trim(component_id)) > 0),
    CHECK (length(trim(replacement_build_id)) > 0),
    CHECK (length(trim(authority_kind)) > 0),
    CHECK (length(trim(authority_locator)) > 0),
    CHECK (length(trim(verifier_kind)) > 0),
    CHECK (length(trim(verifier_identity)) > 0),
    CHECK (length(trim(proof_checksum)) > 0),
    UNIQUE (task_id, proof_event_id),
    UNIQUE (
        proof_id, receipt_id, task_id, branch_id, component_type, component_id, replacement_build_id
    ),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, proof_event_id, proof_event_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence)
);

CREATE TRIGGER replacement_bootstrap_proofs_validate_insert
BEFORE INSERT ON replacement_bootstrap_proofs
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.proof_event_id
          AND event.sequence = NEW.proof_event_sequence
          AND event.event_type = 'component.replacement_bootstrap_succeeded'
          AND json_extract(event.payload_json, '$.receipt_id') = NEW.receipt_id
          AND json_extract(event.payload_json, '$.component_type') = NEW.component_type
          AND json_extract(event.payload_json, '$.component_id') = NEW.component_id
          AND json_extract(event.payload_json, '$.replacement_build_id') = NEW.replacement_build_id
          AND json_extract(event.payload_json, '$.proof_id') = NEW.proof_id
          AND json_extract(event.payload_json, '$.proof_kind') = NEW.proof_kind
          AND json_extract(event.payload_json, '$.status') = NEW.status
          AND json_extract(event.payload_json, '$.proof_checksum') = NEW.proof_checksum
          AND json_extract(event.payload_json, '$.authority_kind') = NEW.authority_kind
          AND json_extract(event.payload_json, '$.authority_locator') = NEW.authority_locator
          AND json_extract(event.payload_json, '$.verifier_kind') = NEW.verifier_kind
          AND json_extract(event.payload_json, '$.verifier_identity') = NEW.verifier_identity
    ) THEN RAISE(ABORT, 'replacement proof must match a successful typed bootstrap event') END;
END;

CREATE TRIGGER replacement_bootstrap_proofs_no_update
BEFORE UPDATE ON replacement_bootstrap_proofs
BEGIN
    SELECT RAISE(ABORT, 'replacement bootstrap proofs are immutable');
END;

CREATE TRIGGER replacement_bootstrap_proofs_no_delete
BEFORE DELETE ON replacement_bootstrap_proofs
BEGIN
    SELECT RAISE(ABORT, 'replacement bootstrap proofs cannot be deleted');
END;

CREATE TABLE quarantined_components (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workbench_tasks(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    component_type TEXT NOT NULL,
    component_id TEXT NOT NULL,
    replacement_build_id TEXT NOT NULL,
    behavior TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('candidate','shadow_readonly','active','released')),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    manifest_checksum TEXT NOT NULL,
    staged_at TEXT NOT NULL,
    staged_event_id TEXT NOT NULL,
    replacement_bootstrap_proof_id TEXT,
    replacement_bootstrap_receipt_id TEXT,
    activated_at TEXT,
    activated_event_id TEXT,
    activated_event_sequence INTEGER CHECK (activated_event_sequence IS NULL OR activated_event_sequence > 0),
    preserved_read INTEGER NOT NULL CHECK (preserved_read IN (0,1)),
    legacy_data_store INTEGER NOT NULL DEFAULT 0 CHECK (legacy_data_store IN (0,1)),
    baseline_row_count INTEGER CHECK (baseline_row_count IS NULL OR baseline_row_count >= 0),
    baseline_content_digest TEXT,
    validated_backup_path TEXT,
    validated_backup_checksum TEXT,
    released_at TEXT,
    release_reason TEXT,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    UNIQUE (task_id, staged_event_id),
    UNIQUE (replacement_bootstrap_proof_id),
    UNIQUE (task_id, activated_event_id),
    CHECK (
        (state IN ('candidate','shadow_readonly')
            AND replacement_bootstrap_proof_id IS NULL
            AND replacement_bootstrap_receipt_id IS NULL
            AND activated_at IS NULL
            AND activated_event_id IS NULL
            AND activated_event_sequence IS NULL)
        OR (state IN ('active','released')
            AND replacement_bootstrap_proof_id IS NOT NULL
            AND replacement_bootstrap_receipt_id IS NOT NULL
            AND activated_at IS NOT NULL
            AND activated_event_id IS NOT NULL
            AND activated_event_sequence IS NOT NULL)
    ),
    CHECK (
        (state <> 'released' AND released_at IS NULL AND release_reason IS NULL)
        OR (state = 'released' AND released_at IS NOT NULL AND release_reason IS NOT NULL)
    ),
    CHECK (
        baseline_row_count IS NULL OR (
            baseline_content_digest IS NOT NULL
            AND validated_backup_path IS NOT NULL
            AND validated_backup_checksum IS NOT NULL
        )
    ),
    CHECK (
        legacy_data_store = 0 OR (
            baseline_row_count IS NOT NULL
            AND baseline_content_digest IS NOT NULL
            AND validated_backup_path IS NOT NULL
            AND validated_backup_checksum IS NOT NULL
        )
    ),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, staged_event_id) REFERENCES workbench_events(task_id, event_id),
    FOREIGN KEY (task_id, activated_event_id, activated_event_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence),
    FOREIGN KEY (
        replacement_bootstrap_proof_id, replacement_bootstrap_receipt_id, task_id, branch_id,
        component_type, component_id, replacement_build_id
    ) REFERENCES replacement_bootstrap_proofs(
        proof_id, receipt_id, task_id, branch_id, component_type, component_id, replacement_build_id
    )
);

CREATE UNIQUE INDEX idx_quarantined_components_nonreleased
ON quarantined_components(task_id, component_type, component_id)
WHERE state <> 'released';

CREATE TRIGGER quarantined_components_candidate_insert_only
BEFORE INSERT ON quarantined_components
WHEN NEW.state <> 'candidate'
BEGIN
    SELECT RAISE(ABORT, 'quarantine components must begin as candidates');
END;

CREATE TRIGGER quarantined_components_validate_stage
BEFORE INSERT ON quarantined_components
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.staged_event_id
          AND event.event_type = 'component.quarantine_staged'
          AND json_extract(event.payload_json, '$.component_type') = NEW.component_type
          AND json_extract(event.payload_json, '$.component_id') = NEW.component_id
          AND json_extract(event.payload_json, '$.replacement_build_id') = NEW.replacement_build_id
          AND json_extract(event.payload_json, '$.manifest_checksum') = NEW.manifest_checksum
    ) THEN RAISE(ABORT, 'quarantine stage must match a typed component staging event') END;
END;

CREATE TRIGGER quarantined_components_monotonic_transition
BEFORE UPDATE OF state ON quarantined_components
WHEN NEW.state <> OLD.state AND NOT (
    (OLD.state = 'candidate' AND NEW.state = 'shadow_readonly')
    OR (OLD.state = 'shadow_readonly' AND NEW.state = 'active')
    OR (OLD.state = 'active' AND NEW.state = 'released')
)
BEGIN
    SELECT RAISE(ABORT, 'quarantine state transitions must be monotonic and staged');
END;

CREATE TRIGGER quarantined_components_validate_activation
BEFORE UPDATE OF state ON quarantined_components
WHEN OLD.state = 'shadow_readonly' AND NEW.state = 'active'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_events event
        JOIN replacement_bootstrap_proofs proof
          ON proof.proof_id = NEW.replacement_bootstrap_proof_id
         AND proof.receipt_id = NEW.replacement_bootstrap_receipt_id
         AND proof.task_id = NEW.task_id
         AND proof.branch_id = NEW.branch_id
         AND proof.component_type = NEW.component_type
         AND proof.component_id = NEW.component_id
         AND proof.replacement_build_id = NEW.replacement_build_id
        WHERE event.task_id = NEW.task_id
          AND event.branch_id = NEW.branch_id
          AND event.event_id = NEW.activated_event_id
          AND event.sequence = NEW.activated_event_sequence
          AND event.event_type = 'component.quarantine_activated'
          AND event.sequence > proof.proof_event_sequence
          AND json_extract(event.payload_json, '$.proof_id') = proof.proof_id
          AND json_extract(event.payload_json, '$.proof_checksum') = proof.proof_checksum
          AND json_extract(event.payload_json, '$.receipt_id') = NEW.replacement_bootstrap_receipt_id
          AND json_extract(event.payload_json, '$.component_type') = NEW.component_type
          AND json_extract(event.payload_json, '$.component_id') = NEW.component_id
          AND json_extract(event.payload_json, '$.replacement_build_id') = NEW.replacement_build_id
    ) THEN RAISE(ABORT, 'activation must match a typed component activation event') END;
END;

CREATE TRIGGER quarantined_components_freeze_after_activation
BEFORE UPDATE ON quarantined_components
WHEN OLD.state IN ('active','released') AND (
       NEW.id IS NOT OLD.id
    OR NEW.task_id IS NOT OLD.task_id
    OR NEW.branch_id IS NOT OLD.branch_id
    OR NEW.component_type IS NOT OLD.component_type
    OR NEW.component_id IS NOT OLD.component_id
    OR NEW.replacement_build_id IS NOT OLD.replacement_build_id
    OR NEW.behavior IS NOT OLD.behavior
    OR NEW.manifest_json IS NOT OLD.manifest_json
    OR NEW.manifest_checksum IS NOT OLD.manifest_checksum
    OR NEW.staged_at IS NOT OLD.staged_at
    OR NEW.staged_event_id IS NOT OLD.staged_event_id
    OR NEW.replacement_bootstrap_proof_id IS NOT OLD.replacement_bootstrap_proof_id
    OR NEW.replacement_bootstrap_receipt_id IS NOT OLD.replacement_bootstrap_receipt_id
    OR NEW.activated_at IS NOT OLD.activated_at
    OR NEW.activated_event_id IS NOT OLD.activated_event_id
    OR NEW.activated_event_sequence IS NOT OLD.activated_event_sequence
    OR NEW.preserved_read IS NOT OLD.preserved_read
    OR NEW.legacy_data_store IS NOT OLD.legacy_data_store
    OR NEW.baseline_row_count IS NOT OLD.baseline_row_count
    OR NEW.baseline_content_digest IS NOT OLD.baseline_content_digest
    OR NEW.validated_backup_path IS NOT OLD.validated_backup_path
    OR NEW.validated_backup_checksum IS NOT OLD.validated_backup_checksum
)
BEGIN
    SELECT RAISE(ABORT, 'activated component identity and proof are frozen');
END;

CREATE TRIGGER schema_migrations_checksum_required
BEFORE INSERT ON schema_migrations
WHEN NEW.version >= 2 AND (NEW.checksum IS NULL OR length(NEW.checksum) <> 64)
BEGIN
    SELECT RAISE(ABORT, 'numbered migrations require a sha256 checksum');
END;

CREATE TRIGGER schema_migrations_fields_immutable
BEFORE UPDATE ON schema_migrations
WHEN OLD.version >= 2
BEGIN
    SELECT RAISE(ABORT, 'applied numbered migration fields are immutable');
END;

CREATE TRIGGER schema_migrations_no_delete
BEFORE DELETE ON schema_migrations
WHEN OLD.version >= 2
BEGIN
    SELECT RAISE(ABORT, 'applied numbered migrations cannot be deleted');
END;
