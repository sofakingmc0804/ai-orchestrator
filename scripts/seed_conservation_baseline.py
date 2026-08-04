"""
Synthetic conservation baseline test.
Exercises _conservation_prepare + _conservation_analyze + store.record_conservation_report
directly, bypassing the adapter. Seeds conservation_reports without a live adapter.
Run: PYTHONPATH="" .venv/Scripts/python.exe scripts/seed_conservation_baseline.py
"""
import asyncio
import json
import os
import sqlite3
import sys

os.environ["PYTHONPATH"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.usage.conservation_report import prepare_dispatch, analyze_dispatch
from orchestrator.state.store import StateStore
from orchestrator.config import Settings


async def seed_conservation():
    settings = Settings.load()
    store = StateStore(settings)
    await store.initialize()

    db_path = str(settings.state_path)
    conn = sqlite3.connect(db_path)
    before = conn.execute("SELECT COUNT(*) FROM conservation_reports").fetchone()[0]
    print(f"conservation_reports before: {before}")

    test_cases = [
        (
            "Summarize the key findings from the GovCon procurement sweep report. "
            "The report covers 4,155 opportunities across 12 states with NAICS codes "
            "matching brewery supply chain requirements.",
            "bulk_extraction",
        ),
        (
            "Debug the TypeError in pipeline/research_product_identifiers.py line 247 "
            "— the apply_research function appends to existing list instead of replacing "
            "stale data.",
            "deep_debugging",
        ),
        (
            "Review the PR for the capability-aware routing changes. Check if the "
            "web_search capability requirement is properly enforced for bulk_extraction "
            "job class.",
            "security_review",
        ),
        (
            "Write a function that migrates the old token_efficiency_score to the new "
            "conservation_score in worker_routing.py. The function should handle the "
            "case where no conservation data exists.",
            "simple_coding",
        ),
        (
            "Analyze the architecture of the translation engine. The system interviews "
            "Matt, routes to the most effective AI worker, dispatches in the background, "
            "and translates results back.",
            "architecture",
        ),
    ]

    reports_created = 0
    for i, (text, job_class) in enumerate(test_cases):
        pre = prepare_dispatch(text, job_class, None)
        print(f"\n[{i+1}] job_class={job_class}")
        print(f"  original_length={pre.get('original_length', len(text))}")
        print(f"  minimized_length={pre.get('minimized_length', len(text))}")

        simulated_response = (
            f"Completed {job_class} task. Key findings: the operation succeeded "
            f"with standard output. No issues detected."
        )

        post = analyze_dispatch(simulated_response, job_class)
        print(f"  conservation_score={post.get('conservation_score', 1.0)}")
        print(f"  waste_modes={post.get('waste_modes', [])}")

        report = {
            "id": f"cr_synthetic_{i+1}",
            "dispatch_id": f"dsp_synthetic_{i+1}",
            "job_class": job_class,
            "original_length": pre.get("original_length", len(text)),
            "minimized_length": pre.get("minimized_length", len(text)),
            "conservation_score": post.get("conservation_score", 1.0),
            "waste_modes": post.get("waste_modes", []),
            "notes": post.get("notes", "synthetic baseline seed"),
        }
        await store.record_conservation_report(report)
        reports_created += 1

    after = conn.execute("SELECT COUNT(*) FROM conservation_reports").fetchone()[0]
    print(f"\nconservation_reports after: {after}")

    rows = conn.execute(
        "SELECT id, job_class, conservation_score, waste_modes FROM conservation_reports"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]} | {r[1]} | score={r[2]} | waste={r[3]}")

    conn.close()
    return after


if __name__ == "__main__":
    result = asyncio.run(seed_conservation())
    print(f"\nSeeded {result} conservation reports")