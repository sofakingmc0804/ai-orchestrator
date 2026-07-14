from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.governance.worker_roster_builder import build_worker_roster, migrate_schema
from orchestrator.state.store import StateStore


JOB_CLASS_COLUMNS = {
    "job_class",
    "required_capabilities_json",
    "preferred_stats_json",
    "local_first",
    "approval_floor",
    "updated_at",
}


@pytest.mark.asyncio
async def test_fresh_schema_supports_worker_roster(tmp_path: Path) -> None:
    settings = Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )
    await StateStore(settings).initialize()

    with sqlite3.connect(settings.state_path) as connection:
        connection.row_factory = sqlite3.Row
        migrate_schema(connection)
        roster = build_worker_roster(connection)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(job_classes)")}

    assert roster["workers"]
    assert JOB_CLASS_COLUMNS <= columns


def test_worker_roster_migration_preserves_existing_job_classes(tmp_path: Path) -> None:
    state_path = tmp_path / "state.sqlite"
    with sqlite3.connect(state_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE job_classes (
                job_class TEXT PRIMARY KEY,
                required_capabilities_json TEXT,
                preferred_stats_json TEXT,
                local_first BOOLEAN,
                approval_floor TEXT
            );
            INSERT INTO job_classes VALUES ('existing', '[]', '{}', 1, 'owner');
            """
        )

        migrate_schema(connection)

        columns = {row["name"] for row in connection.execute("PRAGMA table_info(job_classes)")}
        existing = connection.execute(
            "SELECT job_class, approval_floor FROM job_classes WHERE job_class = 'existing'"
        ).fetchone()

    assert JOB_CLASS_COLUMNS <= columns
    assert dict(existing) == {"job_class": "existing", "approval_floor": "owner"}
