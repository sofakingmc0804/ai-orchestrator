#!/usr/bin/env python3
"""Select a v2 model worker for a classified job."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path.home() / ".ai-resource-governor"
DB_PATH = ROOT / "inventory.sqlite"
RECEIPTS = ROOT / "receipts"
BUILDER = ROOT / "scripts" / "build-worker-roster-v2.py"
PYTHON = Path("C:/Python313/python.exe")

CONTRACT_RANK = {
    "local_resource": 0,
    "subscription_unlimited": 1,
    "subscription_quota": 2,
    "subscription_usage": 3,
    "metered_extra_cost": 90,
    "third_party_metered": 95,
    "unknown_cost": 99,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def refresh_governor() -> dict[str, Any]:
    result = subprocess.run(
        [str(PYTHON), str(BUILDER)],
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-2000:],
        "stderr_tail": (result.stderr or "")[-2000:],
    }


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def load_job(con: sqlite3.Connection, job_class: str) -> dict[str, Any]:
    row = con.execute("SELECT * FROM job_classes WHERE job_class=?", (job_class,)).fetchone()
    if not row:
        known = [r[0] for r in con.execute("SELECT job_class FROM job_classes ORDER BY job_class").fetchall()]
        raise SystemExit(f"Unknown job class: {job_class}. Known: {', '.join(known)}")
    return {
        "job_class": row["job_class"],
        "required_capabilities": json.loads(row["required_capabilities_json"]),
        "preferred_stats": json.loads(row["preferred_stats_json"]),
        "local_first": bool(row["local_first"]),
        "approval_floor": row["approval_floor"],
    }


def load_workers(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute("SELECT * FROM worker_cards").fetchall()
    workers = []
    for row in rows:
        workers.append(
            {
                "worker_id": row["worker_id"],
                "model_id": row["model_id"],
                "base_model": row["base_model"],
                "surface": row["surface"],
                "provider_id": row["provider_id"],
                "contract_type": row["contract_type"],
                "salary_bucket": row["salary_bucket"],
                "overtime_rule": row["overtime_rule"],
                "budget_source_id": row["budget_source_id"],
                "hardware_fit": row["hardware_fit"],
                "context_window": row["context_window"],
                "capabilities": json.loads(row["capabilities_json"]) if "capabilities_json" in row.keys() else [],
                "modalities": json.loads(row["modalities_json"]),
                "tools": json.loads(row["tools_json"]),
                "stats": json.loads(row["stats_json"]),
                "best_jobs": json.loads(row["best_jobs_json"]),
                "avoid_jobs": json.loads(row["avoid_jobs_json"]),
                "badges": json.loads(row["badges_json"]),
                "approval_required": bool(row["approval_required"]),
                "marginal_cost": json.loads(row["marginal_cost_json"]),
                "source_evidence": json.loads(row["source_evidence_json"]),
                "dynamic_state": json.loads(row["dynamic_state_json"]),
                "last_verified": row["last_verified"],
            }
        )
    return workers


def budget_allows(worker: dict[str, Any], approve_overtime: bool, reserve_percent: float) -> tuple[bool, str]:
    contract = worker["contract_type"]
    state = worker.get("dynamic_state", {})
    if contract in {"metered_extra_cost", "third_party_metered", "unknown_cost"}:
        if approve_overtime:
            return True, "overtime explicitly approved for this selection"
        return False, "overtime contract blocked without explicit approval"
    if contract == "subscription_quota":
        pct = state.get("percent_remaining")
        if pct is None:
            return False, "subscription quota unproved"
        if float(pct) < reserve_percent:
            return False, f"subscription reserve protected: {pct}% below {reserve_percent}%"
    if contract == "subscription_usage":
        status = state.get("refresh_status")
        if status not in {"ok", "usage_metrics_only"}:
            return False, "subscription usage state unproved"
    return True, "budget allowed"


def score_worker(worker: dict[str, Any], job: dict[str, Any], context_tokens: int, approve_overtime: bool, reserve_percent: float) -> tuple[int, list[str]]:
    reasons: list[str] = []
    required = set(job["required_capabilities"])
    caps = set()
    caps.update(worker.get("capabilities", []))
    caps.update(worker["tools"])
    caps.update(worker["modalities"])
    caps.update(worker["best_jobs"])
    # Worker card capabilities are folded into best_jobs/tools today; stats and job history carry the rest.
    if job["job_class"] in worker["best_jobs"]:
        reasons.append("direct job fit")
    missing = [cap for cap in required if cap not in caps and cap not in worker["best_jobs"] and worker["stats"].get(cap, 0) < 7]
    if missing:
        return -1000, [f"missing required capability: {', '.join(missing)}"]
    if worker["context_window"] and worker["context_window"] < context_tokens:
        return -1000, [f"context too small: {worker['context_window']} < {context_tokens}"]
    allowed, budget_reason = budget_allows(worker, approve_overtime, reserve_percent)
    if not allowed:
        return -1000, [budget_reason]
    reasons.append(budget_reason)
    score = 0
    score -= CONTRACT_RANK.get(worker["contract_type"], 99) * 10
    if job["job_class"] in worker["best_jobs"]:
        score += 80
    if job["job_class"] in worker["avoid_jobs"]:
        score -= 60
        reasons.append("job is in avoid list")
    for stat, weight in job["preferred_stats"].items():
        score += int(worker["stats"].get(stat, 0)) * int(weight)
    if job["local_first"] and worker["contract_type"] == "local_resource":
        score += 50
        reasons.append("local-first job")
    if worker["approval_required"] and not approve_overtime:
        score -= 500
    return score, reasons


def select(job_class: str, context_tokens: int, approve_overtime: bool, reserve_percent: float, refresh: bool) -> dict[str, Any]:
    refresh_result = refresh_governor() if refresh else {"ok": True, "skipped": True}
    con = connect()
    try:
        job = load_job(con, job_class)
        workers = load_workers(con)
    finally:
        con.close()
    scored = []
    rejected = []
    for worker in workers:
        score, reasons = score_worker(worker, job, context_tokens, approve_overtime, reserve_percent)
        item = {
            "worker_id": worker["worker_id"],
            "model_id": worker["model_id"],
            "surface": worker["surface"],
            "contract_type": worker["contract_type"],
            "salary_bucket": worker["salary_bucket"],
            "badges": worker["badges"],
            "budget_state": worker["dynamic_state"],
            "score": score,
            "reasons": reasons,
            "approval_required": worker["approval_required"],
            "overtime_rule": worker["overtime_rule"],
            "best_jobs": worker["best_jobs"],
            "avoid_jobs": worker["avoid_jobs"],
            "marginal_cost": worker["marginal_cost"],
        }
        if score <= -1000:
            rejected.append(item)
        else:
            scored.append(item)
    ranked = sorted(scored, key=lambda x: (-x["score"], CONTRACT_RANK.get(x["contract_type"], 99), x["worker_id"]))
    chosen = ranked[0] if ranked else None
    data = {
        "decision": "allow" if chosen else "deny",
        "job_class": job_class,
        "context_tokens": context_tokens,
        "chosen_worker": chosen,
        "nearest_alternatives": ranked[1:6],
        "sample_rejections": rejected[:6],
        "refresh": refresh_result,
        "created_at": utc_now(),
    }
    receipt = RECEIPTS / f"worker-select-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{job_class}.json"
    data["receipt_path"] = str(receipt)
    write_json(receipt, data)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select a v2 model worker for a job class.")
    parser.add_argument("--job-class", required=True)
    parser.add_argument("--context-tokens", type=int, default=0)
    parser.add_argument("--approve-overtime", action="store_true")
    parser.add_argument("--reserve-percent", type=float, default=20.0)
    parser.add_argument("--no-refresh", action="store_true")
    args = parser.parse_args(argv)
    data = select(args.job_class, args.context_tokens, args.approve_overtime, args.reserve_percent, refresh=not args.no_refresh)
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0 if data["decision"] == "allow" else 2


if __name__ == "__main__":
    raise SystemExit(main())
