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
    UNIQUE (service, native_session_id),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, receipt_event_id) REFERENCES workbench_events(task_id, event_id)
);

CREATE INDEX idx_service_runs_task_branch_state
ON service_runs(task_id, branch_id, state);

CREATE UNIQUE INDEX idx_service_runs_native_request_identity
ON service_runs(adapter_provider, account_id, profile_id, native_thread_id, native_turn_id, native_request_id)
WHERE native_request_id IS NOT NULL;

CREATE UNIQUE INDEX idx_service_runs_native_tool_identity
ON service_runs(adapter_provider, account_id, profile_id, native_thread_id, native_tool_use_id)
WHERE native_tool_use_id IS NOT NULL;

CREATE UNIQUE INDEX idx_service_runs_native_question_identity
ON service_runs(adapter_provider, account_id, profile_id, native_thread_id, native_question_group_id)
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
    frame_version INTEGER NOT NULL CHECK (frame_version >= 0),
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
    producing_run_id TEXT,
    invalidated_at TEXT,
    invalidated_event_id TEXT,
    invalidation_reason TEXT,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    CHECK (predicate_text IS NOT NULL OR predicate_json IS NOT NULL),
    FOREIGN KEY (task_id, branch_id) REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, success_node_id) REFERENCES frame_nodes(task_id, node_id),
    FOREIGN KEY (task_id, source_event_id) REFERENCES workbench_events(task_id, event_id),
    FOREIGN KEY (task_id, producing_run_id) REFERENCES service_runs(task_id, run_id),
    FOREIGN KEY (task_id, invalidated_event_id) REFERENCES workbench_events(task_id, event_id)
);

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

CREATE TABLE quarantined_components (
    id TEXT PRIMARY KEY,
    component_type TEXT NOT NULL,
    component_id TEXT NOT NULL,
    behavior TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('candidate','shadow_readonly','active','released')),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    manifest_checksum TEXT NOT NULL,
    staged_at TEXT NOT NULL,
    staged_event_id TEXT REFERENCES workbench_events(event_id),
    replacement_bootstrap_receipt_id TEXT,
    activated_at TEXT,
    activated_event_id TEXT REFERENCES workbench_events(event_id),
    preserved_read INTEGER NOT NULL CHECK (preserved_read IN (0,1)),
    legacy_data_store INTEGER NOT NULL DEFAULT 0 CHECK (legacy_data_store IN (0,1)),
    baseline_row_count INTEGER CHECK (baseline_row_count IS NULL OR baseline_row_count >= 0),
    baseline_content_digest TEXT,
    validated_backup_path TEXT,
    validated_backup_checksum TEXT,
    released_at TEXT,
    release_reason TEXT,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    CHECK (
        state <> 'active' OR (
            replacement_bootstrap_receipt_id IS NOT NULL
            AND activated_at IS NOT NULL
            AND activated_event_id IS NOT NULL
        )
    ),
    CHECK (state <> 'released' OR (released_at IS NOT NULL AND release_reason IS NOT NULL)),
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
    )
);

CREATE UNIQUE INDEX idx_quarantined_components_nonreleased
ON quarantined_components(component_type, component_id)
WHERE state <> 'released';

CREATE TRIGGER quarantined_components_no_direct_activation
BEFORE INSERT ON quarantined_components
WHEN NEW.state = 'active'
BEGIN
    SELECT RAISE(ABORT, 'component must be staged before activation');
END;

CREATE TRIGGER quarantined_components_activation_transition
BEFORE UPDATE OF state ON quarantined_components
WHEN NEW.state = 'active' AND OLD.state NOT IN ('candidate','shadow_readonly')
BEGIN
    SELECT RAISE(ABORT, 'only a staged component can be activated');
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
