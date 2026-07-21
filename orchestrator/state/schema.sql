PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY,
    name TEXT,
    service_group TEXT,
    adapter_name TEXT UNIQUE,
    protocol TEXT,
    install_path TEXT,
    version TEXT,
    health_state TEXT,
    detail TEXT,
    repair_action TEXT,
    optional INTEGER DEFAULT 0,
    last_probe_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS capabilities (
    id TEXT PRIMARY KEY,
    adapter_name TEXT REFERENCES services(adapter_name),
    capability_id TEXT,
    rating_instruction INTEGER,
    rating_quality INTEGER,
    latency_band TEXT,
    consequence_max TEXT,
    billing_class TEXT,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    root_path TEXT UNIQUE,
    domain TEXT,
    consequence_tier TEXT,
    policy_file_path TEXT,
    discovered_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    parent_intent_id TEXT REFERENCES intents(id),
    source TEXT,
    raw_text TEXT,
    parsed_payload TEXT,
    project_id TEXT REFERENCES projects(id),
    selections TEXT,
    consequence_tier TEXT,
    state TEXT,
    created_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS dispatches (
    id TEXT PRIMARY KEY,
    intent_id TEXT REFERENCES intents(id),
    adapter_name TEXT,
    envelope TEXT,
    state TEXT,
    started_at TEXT,
    completed_at TEXT,
    output_path TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS dispatch_attempts (
    id TEXT PRIMARY KEY,
    dispatch_id TEXT REFERENCES dispatches(id),
    intent_id TEXT REFERENCES intents(id),
    adapter_name TEXT,
    attempt_number INTEGER,
    state TEXT,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS receipts (
    dispatch_id TEXT PRIMARY KEY REFERENCES dispatches(id),
    service TEXT,
    capability TEXT,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_class TEXT,
    success INTEGER,
    output_summary TEXT,
    full_receipt TEXT,
    -- Phase 4 additions (2026-06-10)
    worker_id TEXT,
    job_class TEXT,
    routing_reasoning TEXT,
    budget_state_json TEXT,
    -- Integrity additions (2026-06-13): behavioral proof, not row-counts.
    -- proof_kind: 'live' (real adapter call this code path produced),
    -- 'imported' (hand-imported external proof), 'synthetic' (stub/no real call).
    raw_output TEXT,
    proof_kind TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS routing_decisions (
    intent_id TEXT REFERENCES intents(id),
    decided_at TEXT,
    chosen_adapter TEXT,
    candidates_considered TEXT,
    candidates_rejected TEXT,
    reasoning TEXT
);

CREATE TABLE IF NOT EXISTS standing_orders (
    id TEXT PRIMARY KEY,
    folder_path TEXT,
    policy_yaml TEXT,
    enabled INTEGER DEFAULT 0,
    last_fired_at TEXT
);

CREATE TABLE IF NOT EXISTS scheduler_tasks (
    id TEXT PRIMARY KEY,
    name TEXT,
    task_type TEXT,
    target_ref TEXT,
    payload TEXT,
    schedule_kind TEXT,
    interval_seconds INTEGER,
    enabled INTEGER DEFAULT 0,
    review_state TEXT DEFAULT 'not_required',
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_note TEXT,
    next_run_at TEXT,
    last_run_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS quota_ledger (
    provider TEXT,
    window_start TEXT,
    window_duration_seconds INTEGER,
    units_consumed INTEGER,
    units_limit INTEGER,
    last_dispatch_id TEXT REFERENCES dispatches(id)
);

CREATE TABLE IF NOT EXISTS repair_queue (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    failure_source TEXT,
    failure_detail TEXT,
    suggested_action TEXT,
    resolved INTEGER DEFAULT 0,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS working_memory (
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    last_activity_at TEXT,
    in_flight_intents TEXT,
    recent_outputs TEXT,
    pending_approvals TEXT,
    open_threads TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    severity TEXT,
    project_id TEXT REFERENCES projects(id),
    intent_id TEXT REFERENCES intents(id),
    title TEXT,
    body TEXT,
    actions TEXT,
    delivered_channels TEXT,
    acknowledged INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS discovery_log (
    id TEXT PRIMARY KEY,
    ran_at TEXT,
    scope TEXT,
    delta TEXT,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    ts TEXT,
    actor TEXT,
    action TEXT,
    target TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS selections (
    id TEXT PRIMARY KEY,
    added_at TEXT,
    kind TEXT,
    payload TEXT,
    grouped_with TEXT
);

CREATE TABLE IF NOT EXISTS delegated_work_items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    consumer TEXT NOT NULL,
    job_class TEXT NOT NULL,
    project_root TEXT,
    mode TEXT NOT NULL,
    operation_task_json TEXT NOT NULL,
    min_quality_score REAL NOT NULL,
    state TEXT NOT NULL,
    dispatch_id TEXT,
    output_path TEXT,
    receipt_path TEXT,
    validation_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_delegated_work_items_state_created
ON delegated_work_items(state, created_at);

CREATE TABLE IF NOT EXISTS discovered_work_items (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_line INTEGER,
    source_task_id TEXT,
    project_root TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    source_status TEXT NOT NULL,
    priority TEXT,
    state TEXT NOT NULL,
    delegated_work_id TEXT,
    error TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovered_work_items_state_updated
ON discovered_work_items(state, updated_at);

-- Worker cards (from governor, added 2026-06-10 during consolidation)
CREATE TABLE IF NOT EXISTS worker_cards (
    worker_id TEXT PRIMARY KEY,
    model_id TEXT,
    base_model TEXT,
    surface TEXT,
    provider_id TEXT,
    contract_type TEXT,
    salary_bucket TEXT,
    overtime_rule TEXT,
    budget_source_id TEXT,
    hardware_fit TEXT,
    context_window INTEGER,
    capabilities_json TEXT,
    modalities_json TEXT,
    tools_json TEXT,
    stats_json TEXT,
    best_jobs_json TEXT,
    avoid_jobs_json TEXT,
    badges_json TEXT,
    approval_required BOOLEAN,
    marginal_cost_json TEXT,
    source_evidence_json TEXT,
    dynamic_state_json TEXT,
    updated_at TEXT,
    last_verified TEXT
);

-- Job classes (from governor, added 2026-06-10 during consolidation)
CREATE TABLE IF NOT EXISTS job_classes (
    job_class TEXT PRIMARY KEY,
    required_capabilities_json TEXT,
    preferred_stats_json TEXT,
    local_first BOOLEAN,
    approval_floor TEXT
);

-- Budget probes (from governor, added 2026-06-10 during consolidation)
CREATE TABLE IF NOT EXISTS budget_probes (
    id TEXT PRIMARY KEY,
    provider_id TEXT,
    probe_type TEXT,
    remaining INTEGER,
    "limit" INTEGER,
    reset_at TEXT,
    probed_at TEXT,
    ok BOOLEAN,
    error TEXT
);

CREATE TABLE IF NOT EXISTS subscription_usage_snapshots (
    id TEXT PRIMARY KEY,
    service_id TEXT,
    account_id TEXT,
    profile_id TEXT,
    subscription_name TEXT,
    plan_name TEXT,
    source_type TEXT,
    source_command TEXT,
    tokens_limit INTEGER,
    tokens_used_total INTEGER,
    tokens_remaining INTEGER,
    tokens_used_by_app INTEGER,
    tokens_used_elsewhere INTEGER,
    reset_at TEXT,
    checked_at TEXT,
    ok BOOLEAN,
    confidence TEXT,
    status TEXT,
    error TEXT,
    usage_windows_json TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS api_budget_policies (
    id TEXT PRIMARY KEY,
    provider_id TEXT,
    account_id TEXT,
    policy_name TEXT,
    budget_limit_usd REAL,
    budget_window TEXT,
    current_spend_usd REAL,
    reset_at TEXT,
    enabled BOOLEAN DEFAULT 0,
    updated_at TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS token_usage (
    id TEXT PRIMARY KEY,
    dispatch_id TEXT REFERENCES dispatches(id),
    attempt_id TEXT REFERENCES dispatch_attempts(id),
    intent_id TEXT REFERENCES intents(id),
    adapter_name TEXT,
    provider TEXT,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    tokens_total INTEGER,
    success INTEGER,
    token_source TEXT,
    confidence TEXT,
    quota_provider TEXT,
    quota_remaining_before INTEGER,
    quota_remaining_after_estimate INTEGER,
    quota_limit INTEGER,
    quota_ratio_after_estimate REAL,
    quota_probe_type TEXT,
    quota_probe_ok INTEGER,
    raw_usage_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS operation_quality_scores (
    id TEXT PRIMARY KEY,
    dispatch_id TEXT REFERENCES dispatches(id),
    worker_id TEXT,
    operation_domain TEXT,
    validator_name TEXT,
    composite_score REAL,
    dimensional_scores_json TEXT,
    task_id TEXT,
    proof_kind TEXT DEFAULT 'live',
    validation_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS skill_hook_receipts (
    id TEXT PRIMARY KEY,
    plan_id TEXT,
    session_id TEXT,
    turn_id TEXT,
    hook_event_name TEXT,
    cwd TEXT,
    prompt TEXT,
    tool_name TEXT,
    decision TEXT,
    confidence REAL,
    selected_skills_json TEXT,
    interpreted_actions_json TEXT,
    authority_checks_json TEXT,
    confirmation_state TEXT,
    terminal_state_requirement TEXT,
    reason TEXT,
    raw_event_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS contract_runs (
    id TEXT PRIMARY KEY,
    plan_id TEXT,
    session_id TEXT,
    turn_id TEXT,
    prompt TEXT,
    axis_version TEXT,
    possibility_space_count INTEGER,
    terminal_state TEXT,
    numeric_result_json TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS possibility_items (
    id TEXT PRIMARY KEY,
    contract_run_id TEXT REFERENCES contract_runs(id),
    axis_tuple_json TEXT,
    requirement_level TEXT,
    proof_level_required TEXT,
    equivalent_key TEXT,
    satisfaction_action TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS equivalence_classes (
    id TEXT PRIMARY KEY,
    contract_run_id TEXT REFERENCES contract_runs(id),
    representative_id TEXT,
    member_ids_json TEXT,
    member_authority_surfaces_json TEXT,
    rule TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_items (
    id TEXT PRIMARY KEY,
    contract_run_id TEXT REFERENCES contract_runs(id),
    evidence_class TEXT,
    authority_surface TEXT,
    subject TEXT,
    verified INTEGER,
    detail TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS counterexamples (
    id TEXT PRIMARY KEY,
    contract_run_id TEXT REFERENCES contract_runs(id),
    axis_tuple_json TEXT,
    why_relevant TEXT,
    failed_predicate TEXT,
    required_resolution TEXT,
    consequence_if_ignored TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS contract_decisions (
    id TEXT PRIMARY KEY,
    contract_run_id TEXT REFERENCES contract_runs(id),
    hook_event_name TEXT,
    decision TEXT,
    numeric_result_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS gmail_response_runs (
    id TEXT PRIMARY KEY,
    gmail_profile_email TEXT,
    started_at TEXT,
    completed_at TEXT,
    config_json TEXT,
    processed_message_ids_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS gmail_response_processed_messages (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES gmail_response_runs(id),
    message_id TEXT,
    thread_id TEXT,
    sender TEXT,
    subject TEXT,
    timestamp TEXT,
    label_ids_json TEXT
);

CREATE TABLE IF NOT EXISTS gmail_relationship_profiles (
    relationship_key TEXT PRIMARY KEY,
    updated_at TEXT,
    participants_json TEXT,
    packet_json TEXT,
    evidence_message_ids_json TEXT
);

CREATE TABLE IF NOT EXISTS gmail_source_claims (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES gmail_response_runs(id),
    relationship_key TEXT,
    status TEXT,
    claim_text TEXT,
    evidence_message_ids_json TEXT,
    reason TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS gmail_response_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES gmail_response_runs(id),
    thread_id TEXT,
    latest_message_id TEXT,
    subject TEXT,
    recipients_json TEXT,
    urgency_bucket TEXT,
    response_state TEXT,
    response_needed_reason TEXT,
    risk_flags_json TEXT,
    next_action TEXT,
    confidence REAL,
    missing_authority_json TEXT,
    authority_evidence_json TEXT,
    safe_to_draft INTEGER,
    reply_message_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS gmail_draft_receipts (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES gmail_response_runs(id),
    draft_id TEXT,
    draft_status TEXT,
    source_thread_id TEXT,
    source_message_id TEXT,
    recipients_json TEXT,
    subject TEXT,
    body_hash TEXT,
    created_at TEXT,
    validation_result TEXT,
    source_packet_hash TEXT,
    reply_message_id TEXT
);

CREATE TABLE IF NOT EXISTS gmail_draft_preflights (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES gmail_response_runs(id),
    thread_id TEXT,
    latest_message_id TEXT,
    ask_summary TEXT,
    requested_topics_json TEXT,
    precedent_summary TEXT,
    precedent_message_ids_json TEXT,
    source_needs_json TEXT,
    discoveries_json TEXT,
    retrieval_steps_json TEXT,
    selected_claims_json TEXT,
    missing_facts_json TEXT,
    copy_brief TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS gmail_verified_discoveries (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES gmail_response_runs(id),
    relationship_key TEXT,
    thread_id TEXT,
    latest_message_id TEXT,
    topic TEXT,
    risk_flag TEXT,
    source_path TEXT,
    claim_text TEXT,
    status TEXT,
    discovered_at TEXT
);

CREATE TABLE IF NOT EXISTS gmail_learned_precedents (
    id TEXT PRIMARY KEY,
    relationship_key TEXT,
    topics_json TEXT,
    summary TEXT,
    evidence_message_ids_json TEXT,
    source TEXT,
    precedent_type TEXT DEFAULT 'approved',
    approval_state TEXT DEFAULT 'approved',
    created_at TEXT
);
