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
    full_receipt TEXT
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
