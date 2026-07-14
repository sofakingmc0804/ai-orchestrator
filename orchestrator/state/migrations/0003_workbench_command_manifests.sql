-- Command checksum contracts are frozen here for the writer/verifier that owns
-- canonicalization. SQLite enforces shape; the in-transaction verifier recomputes:
-- workbench.command.drafts.v1
-- {domain,task_id,command_id,drafts:[{ordinal,event_type,event_schema_version,
-- actor_kind,actor_id,branch_id,cause,caused_by,payload}]}
-- workbench.command.manifest.v1
-- {domain,task_id,command_id,target_branch_id,event_count,first_sequence,last_sequence,
-- first_event_id,last_event_id,starting_frame_version,expected_frame_version,
-- confirm_ordinal,drafts_checksum,created_at}

CREATE TABLE workbench_command_manifest_upgrade_guard (
    marker INTEGER NOT NULL
);

CREATE TRIGGER workbench_command_manifest_upgrade_requires_empty_events
BEFORE INSERT ON workbench_command_manifest_upgrade_guard
WHEN EXISTS (SELECT 1 FROM workbench_events LIMIT 1)
BEGIN
    SELECT RAISE(ABORT, 'migration_precondition:3:preexisting_workbench_events_without_manifests');
END;

INSERT INTO workbench_command_manifest_upgrade_guard(marker) VALUES (1);

DROP TRIGGER workbench_command_manifest_upgrade_requires_empty_events;

DROP TABLE workbench_command_manifest_upgrade_guard;

CREATE TABLE workbench_command_manifests (
    task_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    target_branch_id TEXT NOT NULL,
    event_count INTEGER NOT NULL CHECK (event_count > 0),
    first_sequence INTEGER NOT NULL CHECK (first_sequence > 0),
    last_sequence INTEGER NOT NULL CHECK (last_sequence > 0),
    first_event_id TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    starting_frame_version INTEGER NOT NULL CHECK (starting_frame_version >= 0),
    expected_frame_version INTEGER NOT NULL CHECK (expected_frame_version >= 0),
    confirm_ordinal INTEGER,
    drafts_checksum TEXT NOT NULL,
    manifest_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, command_id),
    CHECK (length(trim(task_id)) > 0),
    CHECK (length(trim(command_id)) > 0),
    CHECK (length(trim(target_branch_id)) > 0),
    CHECK (last_sequence = first_sequence + event_count - 1),
    CHECK (length(trim(first_event_id)) > 0),
    CHECK (length(trim(last_event_id)) > 0),
    CHECK (expected_frame_version = starting_frame_version),
    CHECK (confirm_ordinal IS NULL OR confirm_ordinal BETWEEN 1 AND event_count),
    CHECK (length(drafts_checksum) = 64 AND drafts_checksum NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(manifest_checksum) = 64 AND manifest_checksum NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(trim(created_at)) > 0),
    FOREIGN KEY (task_id) REFERENCES workbench_tasks(id),
    FOREIGN KEY (task_id, target_branch_id)
        REFERENCES workbench_branches(task_id, branch_id),
    FOREIGN KEY (task_id, first_event_id, first_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (task_id, last_event_id, last_sequence)
        REFERENCES workbench_events(task_id, event_id, sequence)
        DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TRIGGER workbench_command_manifests_contiguous_insert
BEFORE INSERT ON workbench_command_manifests
WHEN NEW.first_sequence <> COALESCE(
        (SELECT MAX(manifest.last_sequence) + 1
         FROM workbench_command_manifests manifest
         WHERE manifest.task_id = NEW.task_id),
        1
    )
    OR EXISTS (
        SELECT 1
        FROM workbench_command_manifests manifest
        WHERE manifest.task_id = NEW.task_id
          AND NEW.first_sequence <= manifest.last_sequence
          AND NEW.last_sequence >= manifest.first_sequence
    )
    OR EXISTS (
        SELECT 1
        FROM workbench_command_manifests manifest
        WHERE manifest.task_id = NEW.task_id
          AND (
              NEW.first_event_id IN (manifest.first_event_id, manifest.last_event_id)
              OR NEW.last_event_id IN (manifest.first_event_id, manifest.last_event_id)
          )
    )
BEGIN
    SELECT RAISE(ABORT, 'command manifest ranges must be task-contiguous, nonoverlapping, and use fresh endpoints');
END;

CREATE TRIGGER workbench_events_require_command_manifest
BEFORE INSERT ON workbench_events
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM workbench_command_manifests manifest
        WHERE manifest.task_id = NEW.task_id
          AND manifest.command_id = NEW.command_id
          AND manifest.target_branch_id = NEW.branch_id
          AND typeof(NEW.sequence) = 'integer'
          AND typeof(NEW.command_sequence) = 'integer'
          AND typeof(NEW.frame_version) = 'integer'
          AND typeof(NEW.event_schema_version) = 'integer'
          AND NEW.command_sequence BETWEEN 1 AND manifest.event_count
          AND NEW.sequence = manifest.first_sequence + NEW.command_sequence - 1
          AND (NEW.command_sequence <> 1 OR NEW.event_id = manifest.first_event_id)
          AND (NEW.command_sequence <> manifest.event_count OR NEW.event_id = manifest.last_event_id)
          AND (NEW.command_sequence <> 1 OR NEW.created_at = manifest.created_at)
    ) THEN RAISE(ABORT, 'event must match its exact command manifest task, branch, range, ordinal, endpoints, and first time') END;
END;

CREATE TRIGGER workbench_command_manifests_no_update
BEFORE UPDATE ON workbench_command_manifests
BEGIN
    SELECT RAISE(ABORT, 'workbench command manifests are immutable');
END;

CREATE TRIGGER workbench_command_manifests_no_delete
BEFORE DELETE ON workbench_command_manifests
BEGIN
    SELECT RAISE(ABORT, 'workbench command manifests cannot be deleted');
END;
