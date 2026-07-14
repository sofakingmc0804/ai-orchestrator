PRAGMA foreign_keys=ON;

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    root_path TEXT UNIQUE,
    domain TEXT,
    consequence_tier TEXT,
    policy_file_path TEXT,
    discovered_at TEXT,
    updated_at TEXT
);

CREATE TABLE intents (
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

CREATE TABLE repair_queue (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    failure_source TEXT,
    failure_detail TEXT,
    suggested_action TEXT,
    resolved INTEGER DEFAULT 0,
    resolved_at TEXT
);

INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-07-13T00:00:00+00:00');
INSERT INTO projects VALUES (
    'project-owner-sentinel',
    'Owner sentinel — preserve punctuation, whitespace, and Unicode',
    'C:\\Users\\Couch\\Owner Project',
    'intent research',
    'high',
    'C:\\Users\\Couch\\Owner Project\\AGENTS.md',
    '2026-07-01T12:34:56+00:00',
    '2026-07-02T01:02:03+00:00'
);
INSERT INTO intents VALUES (
    'intent-owner-sentinel',
    NULL,
    'owner_chat',
    'Preserve this exact owner value: café / Ω / line one\nline two',
    '{"nested":{"answer":42},"list":["a","b"],"truth":true}',
    'project-owner-sentinel',
    '{"selected":["three-pane","expandable"]}',
    'high',
    'in_progress',
    '2026-07-03T04:05:06+00:00',
    NULL
);
INSERT INTO repair_queue VALUES (
    'owner-existing-repair',
    '2026-07-04T05:06:07+00:00',
    'owner_source',
    'Do not overwrite this pre-existing repair record.',
    'Preserve and continue.',
    0,
    NULL
);
