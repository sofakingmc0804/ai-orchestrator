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
