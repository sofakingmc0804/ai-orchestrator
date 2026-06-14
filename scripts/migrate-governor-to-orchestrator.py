#!/usr/bin/env python3
"""
Migrate .ai-resource-governor state into orchestrator DB.

This script:
1. Reads governor's inventory.sqlite (worker_cards, job_classes, budget_probes)
2. Reads orchestrator's state.sqlite (services, capabilities, etc.)
3. Backs up both DBs
4. Merges governor tables into orchestrator DB
5. Creates symlinks for runtime compatibility

Usage:
    python scripts/migrate-governor-to-orchestrator.py --dry-run
    python scripts/migrate-governor-to-orchestrator.py --execute

Author: Hermes Agent
Date: 2026-06-10
Phase: 1 (Foundation)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Paths
GOVERNOR_ROOT = Path.home() / ".ai-resource-governor"
ORCHESTRATOR_ROOT = Path.home() / "dev" / "ai-orchestrator"

GOVERNOR_DB = GOVERNOR_ROOT / "inventory.sqlite"
ORCHESTRATOR_DB = ORCHESTRATOR_ROOT / ".runtime" / "orchestrator" / "state.sqlite"

BACKUP_DIR = ORCHESTRATOR_ROOT / ".runtime" / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    """Connect to SQLite DB read-only."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def connect_rw(path: Path) -> sqlite3.Connection:
    """Connect to SQLite DB read-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def backup_db(src: Path, dst: Path) -> None:
    """Copy DB file to backup location."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  Backed up: {src} -> {dst}")


def get_table_count(con: sqlite3.Connection, table: str) -> int:
    """Count rows in a table."""
    row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return row[0] if row else 0


def list_tables(con: sqlite3.Connection) -> list[str]:
    """List all tables in DB."""
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def migrate_worker_cards(gov_con: sqlite3.Connection, orch_con: sqlite3.Connection, dry_run: bool = False) -> int:
    """Migrate worker_cards from governor to orchestrator DB."""
    print("\nMigrating worker_cards...")

    rows = gov_con.execute("SELECT * FROM worker_cards").fetchall()
    count = len(rows)

    if dry_run:
        print(f"  Would migrate {count} worker cards")
        return count

    # Create table if not exists
    orch_con.executescript("""
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
            approval_required BOOLEAN,
            source_evidence_json TEXT,
            last_verified TEXT
        );
    """)

    # Insert rows (skip existing)
    inserted = 0
    for row in rows:
        try:
            orch_con.execute("""
                INSERT OR IGNORE INTO worker_cards (
                    worker_id, model_id, base_model, surface, provider_id,
                    contract_type, salary_bucket, overtime_rule, budget_source_id,
                    hardware_fit, context_window, capabilities_json, modalities_json,
                    tools_json, stats_json, best_jobs_json, avoid_jobs_json,
                    approval_required, source_evidence_json, last_verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["worker_id"], row["model_id"], row["base_model"], row["surface"],
                row["provider_id"], row["contract_type"], row["salary_bucket"],
                row["overtime_rule"], row["budget_source_id"], row["hardware_fit"],
                row["context_window"], row["capabilities_json"], row["modalities_json"],
                row["tools_json"], row["stats_json"], row["best_jobs_json"],
                row["avoid_jobs_json"], row["approval_required"], row["source_evidence_json"],
                row["last_verified"]
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # Already exists

    orch_con.commit()
    print(f"  Migrated {inserted}/{count} worker cards")
    return inserted


def migrate_job_classes(gov_con: sqlite3.Connection, orch_con: sqlite3.Connection, dry_run: bool = False) -> int:
    """Migrate job_classes from governor to orchestrator DB."""
    print("\nMigrating job_classes...")

    rows = gov_con.execute("SELECT * FROM job_classes").fetchall()
    count = len(rows)

    if dry_run:
        print(f"  Would migrate {count} job classes")
        return count

    # Create table if not exists
    orch_con.executescript("""
        CREATE TABLE IF NOT EXISTS job_classes (
            job_class TEXT PRIMARY KEY,
            required_capabilities_json TEXT,
            preferred_stats_json TEXT,
            local_first BOOLEAN,
            approval_floor TEXT
        );
    """)

    # Insert rows
    inserted = 0
    for row in rows:
        try:
            orch_con.execute("""
                INSERT OR REPLACE INTO job_classes (
                    job_class, required_capabilities_json, preferred_stats_json,
                    local_first, approval_floor
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                row["job_class"], row["required_capabilities_json"],
                row["preferred_stats_json"], row["local_first"], row["approval_floor"]
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"  Error migrating job_class {row['job_class']}: {e}")

    orch_con.commit()
    print(f"  Migrated {inserted}/{count} job classes")
    return inserted


def migrate_budget_probes(gov_con: sqlite3.Connection, orch_con: sqlite3.Connection, dry_run: bool = False) -> int:
    """Migrate budget_probes from governor to orchestrator DB."""
    print("\nMigrating budget_probes...")

    # Check if table exists in governor DB
    gov_tables = list_tables(gov_con)
    if "budget_probes" not in gov_tables:
        print("  budget_probes table not found in governor DB, skipping")
        return 0

    rows = gov_con.execute("SELECT * FROM budget_probes").fetchall()
    count = len(rows)

    if dry_run:
        print(f"  Would migrate {count} budget probes")
        return count

    # Create table if not exists
    orch_con.executescript("""
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
    """)

    # Insert rows
    inserted = 0
    for row in rows:
        try:
            orch_con.execute("""
                INSERT OR REPLACE INTO budget_probes (
                    id, provider_id, probe_type, remaining, "limit",
                    reset_at, probed_at, ok, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["id"], row["provider_id"], row["probe_type"],
                row["remaining"], row["limit"], row["reset_at"],
                row["probed_at"], row["ok"], row["error"]
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"  Error migrating budget_probe: {e}")

    orch_con.commit()
    print(f"  Migrated {inserted}/{count} budget probes")
    return inserted


def create_symlinks(governor_root: Path, orchestrator_runtime: Path, dry_run: bool = False) -> None:
    """Create symlinks in governor runtime pointing to orchestrator runtime."""
    print("\nCreating symlinks for backward compatibility...")

    links = [
        ("inventory.sqlite", orchestrator_runtime / "state.sqlite"),
        ("receipts", orchestrator_runtime / "receipts"),
        ("cache", orchestrator_runtime / "cache"),
    ]

    for link_name, target in links:
        link_path = governor_root / link_name

        if dry_run:
            print(f"  Would create symlink: {link_name} -> {target}")
            continue

        # Remove existing file/dir
        if link_path.exists() or link_path.is_symlink():
            if link_path.is_symlink() or link_path.is_file():
                link_path.unlink()
            elif link_path.is_dir():
                shutil.rmtree(link_path)

        # Create symlink
        try:
            link_path.symlink_to(target)
            print(f"  Created symlink: {link_name} -> {target}")
        except OSError as e:
            print(f"  Failed to create symlink {link_name}: {e}")


def print_summary(orch_con: sqlite3.Connection) -> None:
    """Print migration summary."""
    print("\nMigration Summary:")

    tables = list_tables(orch_con)
    for table in ["worker_cards", "job_classes", "budget_probes", "services", "capabilities", "dispatches"]:
        if table in tables:
            count = get_table_count(orch_con, table)
            print(f"  {table}: {count} rows")
        else:
            print(f"  {table}: (table not found)")


def main():
    parser = argparse.ArgumentParser(description="Migrate governor state to orchestrator DB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--execute", action="store_true", help="Execute the migration")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Error: Must specify --dry-run or --execute")
        sys.exit(1)

    dry_run = args.dry_run

    print("=" * 60)
    print("AI Orchestrator - Governor Migration")
    print(f"Timestamp: {utc_now()}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print("=" * 60)

    # Verify source DBs exist
    if not GOVERNOR_DB.exists():
        print(f"\n[FAIL] Governor DB not found: {GOVERNOR_DB}")
        sys.exit(1)

    if not ORCHESTRATOR_DB.exists():
        print(f"\n[FAIL] Orchestrator DB not found: {ORCHESTRATOR_DB}")
        print("   Run orchestrator first to create the DB, or create it manually")
        sys.exit(1)

    # Create backup directory
    if not dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nBackup directory: {BACKUP_DIR}")

    # Open connections
    print("\nConnecting to databases...")
    gov_con = connect_ro(GOVERNOR_DB)
    orch_con = connect_rw(ORCHESTRATOR_DB)

    print(f"  Governor DB: {GOVERNOR_DB}")
    print(f"  Orchestrator DB: {ORCHESTRATOR_DB}")

    # Backup DBs
    if not dry_run:
        print("\nCreating backups...")
        backup_db(GOVERNOR_DB, BACKUP_DIR / "governor_inventory.sqlite")
        backup_db(ORCHESTRATOR_DB, BACKUP_DIR / "orchestrator_state.sqlite")

    # Migrate tables
    gov_tables = list_tables(gov_con)
    print(f"\nGovernor DB tables: {', '.join(gov_tables)}")

    worker_count = 0
    job_count = 0
    probe_count = 0

    if "worker_cards" in gov_tables:
        worker_count = migrate_worker_cards(gov_con, orch_con, dry_run)

    if "job_classes" in gov_tables:
        job_count = migrate_job_classes(gov_con, orch_con, dry_run)

    if "budget_probes" in gov_tables:
        probe_count = migrate_budget_probes(gov_con, orch_con, dry_run)

    # Create symlinks
    if not dry_run:
        orchestrator_runtime = ORCHESTRATOR_ROOT / ".runtime" / "orchestrator"
        create_symlinks(GOVERNOR_ROOT, orchestrator_runtime, dry_run=False)

    # Print summary
    if not dry_run:
        print_summary(orch_con)

    # Close connections
    gov_con.close()
    orch_con.close()

    print("\n" + "=" * 60)
    if dry_run:
        print("DRY RUN COMPLETE - No changes made")
        print("   Run with --execute to perform migration")
    else:
        print("MIGRATION COMPLETE")
        print(f"   Backups saved to: {BACKUP_DIR}")
        print("\n   Next steps:")
        print("   1. Verify: orchestrator cli workers --limit 5")
        print("   2. Verify: orchestrator cli budget")
        print("   3. Test dispatch with job class: orchestrator cli route --job-class repo_coding --text 'test'")
    print("=" * 60)


if __name__ == "__main__":
    main()
