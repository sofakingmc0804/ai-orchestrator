#!/usr/bin/env python3
"""
Migration: Add surface capability awareness to worker routing.

What this does:
1. Updates bulk_extraction job class: adds web_search, web_extract to required_capabilities
2. Adds web_search, web_extract, terminal, code_execution capabilities to surfaces that have them:
   - github-copilot  → web_search, web_extract (delegates via Hermes which has web tools)
   - codex-chatgpt   → terminal, code_execution (agentic coding with shell access)
   - claude-max      → terminal, code_execution (claude-code-cli adapter)
   - ollama-local    → no changes (text generation only)
   - ollama-cloud    → no changes (text generation only)

Run with: PYTHONPATH="" .venv/Scripts/python.exe scripts/migrate_surface_capabilities.py
"""
from __future__ import annotations
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".runtime" / "orchestrator" / "state.sqlite"

# Required capabilities to ADD per job class (merged with existing)
JOB_CLASS_REQUIRED_CAPS: dict[str, list[str]] = {
    # bulk_extraction: drop 'classification' (legacy embedding hint), require web tools instead
    "bulk_extraction": ["web_search", "web_extract"],
    # repo_coding and agentic_repair: require terminal/code_execution so agentic surfaces win
    "repo_coding":     ["code_execution", "coding", "terminal", "tools"],
    "agentic_repair":  ["agentic", "code_execution", "coding", "terminal", "tools"],
}
# Alias kept for backward compat in function call below
JOB_CLASS_EXTRA_CAPS = JOB_CLASS_REQUIRED_CAPS

# Capabilities to ADD to worker cards per surface
SURFACE_EXTRA_CAPS: dict[str, list[str]] = {
    "github-copilot": ["web_search", "web_extract"],
    "codex-chatgpt":  ["terminal", "code_execution"],
    "claude-max":     ["terminal", "code_execution"],
}

# best_jobs to ADD per surface (so these workers appear in bulk_extraction candidates)
SURFACE_EXTRA_BEST_JOBS: dict[str, list[str]] = {
    "github-copilot": ["bulk_extraction"],
}


def patch_job_class(cur: sqlite3.Cursor, job_class: str, extra_caps: list[str]) -> None:
    row = cur.execute(
        "SELECT required_capabilities_json FROM job_classes WHERE job_class = ?",
        (job_class,),
    ).fetchone()
    if not row:
        print(f"  SKIP: job_class {job_class!r} not found in DB")
        return
    existing = json.loads(row[0] or "[]")
    merged = sorted(set(existing) | set(extra_caps))
    cur.execute(
        "UPDATE job_classes SET required_capabilities_json = ? WHERE job_class = ?",
        (json.dumps(merged), job_class),
    )
    print(f"  job_classes[{job_class}]: {existing} -> {merged}")


def patch_worker_cards(
    cur: sqlite3.Cursor,
    surface: str,
    extra_caps: list[str],
    extra_best_jobs: list[str],
) -> None:
    rows = cur.execute(
        "SELECT worker_id, capabilities_json, best_jobs_json FROM worker_cards WHERE surface = ?",
        (surface,),
    ).fetchall()
    if not rows:
        print(f"  SKIP: no worker_cards for surface {surface!r}")
        return
    for worker_id, caps_json, best_jobs_json in rows:
        existing_caps = json.loads(caps_json or "[]")
        merged_caps = sorted(set(existing_caps) | set(extra_caps))
        existing_best = json.loads(best_jobs_json or "[]")
        merged_best = sorted(set(existing_best) | set(extra_best_jobs))
        cur.execute(
            "UPDATE worker_cards SET capabilities_json = ?, best_jobs_json = ? WHERE worker_id = ?",
            (json.dumps(merged_caps), json.dumps(merged_best), worker_id),
        )
    print(f"  worker_cards[{surface}]: {len(rows)} cards updated +caps={extra_caps} +best_jobs={extra_best_jobs}")


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    print(f"DB: {DB_PATH}")
    print()

    print("=== Patching job_classes ===")
    for job_class, extra_caps in JOB_CLASS_EXTRA_CAPS.items():
        patch_job_class(cur, job_class, extra_caps)
    print()

    print("=== Patching worker_cards ===")
    for surface, extra_caps in SURFACE_EXTRA_CAPS.items():
        extra_best = SURFACE_EXTRA_BEST_JOBS.get(surface, [])
        patch_worker_cards(cur, surface, extra_caps, extra_best)
    print()

    con.commit()
    con.close()
    print("Done. Migration committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
