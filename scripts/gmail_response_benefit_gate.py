from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_AUTOMATION_ID = "gmail-response-agent-draft-desk"
DEFAULT_STATE_DB = Path(".runtime/orchestrator/state.sqlite")
CONTROL_TABLE = "gmail_response_automation_control"
STALE_RESEARCH_KEY = "stale_research"
LANE_PRIORITY = ("current_watch", "draft_worker", "owner_review", "stale_research")

CONTROL_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {CONTROL_TABLE} (
    automation_id TEXT NOT NULL,
    control_key TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (automation_id, control_key)
);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(CONTROL_SCHEMA)
    return connection


def _load_control_state(connection: sqlite3.Connection, automation_id: str, control_key: str) -> tuple[dict[str, Any] | None, str | None]:
    row = connection.execute(
        f"SELECT state_json, updated_at FROM {CONTROL_TABLE} WHERE automation_id = ? AND control_key = ?",
        (automation_id, control_key),
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row["state_json"]), str(row["updated_at"])


def _save_control_state(
    connection: sqlite3.Connection,
    automation_id: str,
    control_key: str,
    state: dict[str, Any],
) -> str:
    updated_at = _iso_now()
    connection.execute(
        f"""
        INSERT INTO {CONTROL_TABLE}(automation_id, control_key, state_json, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(automation_id, control_key)
        DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at
        """,
        (automation_id, control_key, json.dumps(state, sort_keys=True), updated_at),
    )
    connection.commit()
    return updated_at


def _window_age_label(today: date, start: date, end: date) -> str:
    newer_days = (today - end).days
    older_days = (today - start).days
    return f"{newer_days}-{older_days} days old"


def _initial_stale_state(
    *,
    today: date,
    recent_window_days: int,
    history_window_days: int,
    tranche_days: int,
) -> dict[str, Any]:
    window_end = today - timedelta(days=recent_window_days)
    window_start = window_end - timedelta(days=tranche_days)
    oldest_boundary = today - timedelta(days=history_window_days)
    return {
        "cursor_version": 1,
        "status": "planned",
        "recent_window_days": recent_window_days,
        "history_window_days": history_window_days,
        "tranche_days": tranche_days,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "oldest_boundary": oldest_boundary.isoformat(),
        "cursor_index": 0,
        "last_completed_run_id": None,
        "last_completed_at": None,
    }


def _load_or_seed_stale_state(
    connection: sqlite3.Connection,
    automation_id: str,
    *,
    recent_window_days: int,
    history_window_days: int,
    tranche_days: int,
) -> tuple[dict[str, Any], str]:
    state, updated_at = _load_control_state(connection, automation_id, STALE_RESEARCH_KEY)
    if state is not None and updated_at is not None:
        return state, updated_at
    seeded = _initial_stale_state(
        today=_utc_now().date(),
        recent_window_days=recent_window_days,
        history_window_days=history_window_days,
        tranche_days=tranche_days,
    )
    saved_at = _save_control_state(connection, automation_id, STALE_RESEARCH_KEY, seeded)
    return seeded, saved_at


def _latest_run(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT
            runs.id,
            runs.started_at,
            runs.completed_at,
            coverage.coverage_status,
            coverage.coverage_reasons_json,
            coverage.inbox_messages_total,
            coverage.inbox_messages_unread,
            coverage.inbox_threads_total,
            coverage.inbox_threads_unread,
            coverage.indexed_thread_count
        FROM gmail_response_runs AS runs
        LEFT JOIN gmail_response_run_coverage AS coverage ON coverage.run_id = runs.id
        ORDER BY COALESCE(runs.completed_at, runs.started_at, runs.created_at) DESC
        LIMIT 1
        """
    ).fetchone()


def _recent_draft_count(connection: sqlite3.Connection, recent_run_limit: int) -> int:
    row = connection.execute(
        """
        WITH recent_runs AS (
            SELECT id
            FROM gmail_response_runs
            ORDER BY COALESCE(completed_at, started_at, created_at) DESC
            LIMIT ?
        )
        SELECT COUNT(*) AS draft_count
        FROM gmail_draft_receipts
        WHERE run_id IN (SELECT id FROM recent_runs)
          AND draft_status = 'created_in_gmail'
        """,
        (recent_run_limit,),
    ).fetchone()
    return int(row["draft_count"]) if row is not None else 0


def _latest_candidate_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    latest = _latest_run(connection)
    if latest is None:
        return {}
    rows = connection.execute(
        """
        SELECT candidate_state, COUNT(*) AS state_count
        FROM gmail_inbox_thread_index
        WHERE run_id = ?
        GROUP BY candidate_state
        """,
        (latest["id"],),
    ).fetchall()
    return {str(row["candidate_state"]): int(row["state_count"]) for row in rows}


def _hours_since(moment: datetime | None) -> float | None:
    if moment is None:
        return None
    delta = _utc_now() - moment
    return round(delta.total_seconds() / 3600, 2)


def _lane_plan(*, should_run: bool, mode: str, reason: str, signals: list[str], backlog_count: int = 0) -> dict[str, Any]:
    return {
        "should_run": should_run,
        "mode": mode,
        "reason": reason,
        "signals": signals,
        "backlog_count": backlog_count,
    }


def _value_plan(*, should_run: bool, mode: str, reason: str, signals: list[str], expected_value_score: float, expected_cost_score: float) -> dict[str, Any]:
    return {
        "should_run": should_run,
        "mode": mode,
        "reason": reason,
        "signals": signals,
        "expected_value_score": round(expected_value_score, 2),
        "expected_cost_score": round(expected_cost_score, 2),
        "benefit_margin": round(expected_value_score - expected_cost_score, 2),
    }


def build_plan(
    *,
    db_path: Path,
    automation_id: str,
    recent_window_days: int,
    history_window_days: int,
    tranche_days: int,
    current_probe_cooldown_hours: int,
    stale_research_cooldown_hours: int,
    recent_run_limit: int,
) -> dict[str, Any]:
    connection = _connect(db_path)
    stale_state, stale_updated_at = _load_or_seed_stale_state(
        connection,
        automation_id,
        recent_window_days=recent_window_days,
        history_window_days=history_window_days,
        tranche_days=tranche_days,
    )
    latest = _latest_run(connection)
    recent_draft_count = _recent_draft_count(connection, recent_run_limit=recent_run_limit)
    latest_state_counts = _latest_candidate_state_counts(connection)

    latest_completed_at = None
    latest_run_id = None
    latest_coverage_status = None
    coverage_reasons: list[str] = []
    if latest is not None:
        latest_run_id = str(latest["id"])
        latest_completed_at = _parse_timestamp(str(latest["completed_at"] or latest["started_at"] or ""))
        latest_coverage_status = str(latest["coverage_status"] or "")
        if latest["coverage_reasons_json"]:
            coverage_reasons = list(json.loads(str(latest["coverage_reasons_json"])))

    stale_updated = _parse_timestamp(stale_updated_at)
    current_probe_due = latest_completed_at is None or (_hours_since(latest_completed_at) or 0) >= current_probe_cooldown_hours
    stale_research_due = stale_updated is None or (_hours_since(stale_updated) or 0) >= stale_research_cooldown_hours

    today = _utc_now().date()
    window_start = date.fromisoformat(str(stale_state["window_start"]))
    window_end = date.fromisoformat(str(stale_state["window_end"]))
    oldest_boundary = date.fromisoformat(str(stale_state["oldest_boundary"]))

    if window_start <= oldest_boundary:
        stale_state["status"] = "exhausted"

    draft_backlog_count = latest_state_counts.get("safe_draft", 0) + latest_state_counts.get("needs_reply_existing_draft", 0)
    owner_review_count = latest_state_counts.get("owner_review", 0)
    continuation_open = latest_coverage_status == "partial_coverage"

    lanes = {
        "current_watch": _lane_plan(
            should_run=continuation_open or current_probe_due,
            mode="resume_continuation" if continuation_open else "cheap_current_delta_probe",
            reason=(
                "Resume unfinished coverage before any new broad search."
                if continuation_open
                else "Run only a cheap Gmail delta probe, then stop unless new inbound response work exists."
            ),
            signals=(
                ["latest run ended in partial_coverage"]
                if continuation_open
                else ["current delta probe cooldown elapsed"] if current_probe_due else ["current delta probe cooldown still active"]
            ),
        ),
        "draft_worker": _lane_plan(
            should_run=(not continuation_open) and draft_backlog_count > 0,
            mode="review_existing_candidate",
            reason="Resolve live draft candidates before scanning more mail.",
            signals=(
                ["latest run still has draftable or duplicate-draft candidates"]
                if draft_backlog_count > 0
                else ["no draftable backlog in latest indexed run"]
            ),
            backlog_count=draft_backlog_count,
        ),
        "owner_review": _lane_plan(
            should_run=(not continuation_open) and owner_review_count > 0,
            mode="escalate_owner_review_backlog",
            reason="Surface risky or non-delegable threads without spending another discovery cycle.",
            signals=(
                ["latest indexed run produced owner-review backlog"]
                if owner_review_count > 0
                else ["no owner-review backlog in latest indexed run"]
            ),
            backlog_count=owner_review_count,
        ),
        "stale_research": _lane_plan(
            should_run=(not continuation_open) and draft_backlog_count == 0 and recent_draft_count == 0 and stale_state.get("status") != "exhausted" and stale_research_due,
            mode="stale_research",
            reason="Use the next unscanned stale window instead of another low-yield delta pass.",
            signals=(
                [
                    "recent scheduled runs created zero drafts",
                    "stale research tranche is due",
                ]
                if stale_state.get("status") != "exhausted" and stale_research_due
                else (
                    ["historical stale-research window is exhausted"]
                    if stale_state.get("status") == "exhausted"
                    else ["stale research cooldown still active"]
                )
            ),
        ),
    }

    single_engine = _value_plan(
        should_run=False,
        mode="skip",
        reason="No single Gmail automation branch currently clears the benefit-over-cost bar.",
        signals=["no productive branch is due"],
        expected_value_score=0.0,
        expected_cost_score=2.5,
    )
    if draft_backlog_count > 0:
        expected_value = float(draft_backlog_count * 5)
        expected_cost = 2.5
        single_engine = _value_plan(
            should_run=expected_value >= expected_cost,
            mode="draft_backlog",
            reason="Work existing safe-draft backlog before doing any new mailbox discovery.",
            signals=["safe-draft backlog already exists in local state"],
            expected_value_score=expected_value,
            expected_cost_score=expected_cost,
        )
    elif stale_state.get("status") != "exhausted" and stale_research_due and recent_draft_count == 0:
        expected_value = 4.0
        expected_cost = 2.5
        single_engine = _value_plan(
            should_run=expected_value >= expected_cost,
            mode="stale_research_tranche",
            reason="Use one non-overlapping stale tranche when recent current-mail runs produced no drafts.",
            signals=["stale tranche is due", "recent runs created zero drafts"],
            expected_value_score=expected_value,
            expected_cost_score=expected_cost,
        )
    elif current_probe_due:
        expected_value = 1.0
        expected_cost = 2.5
        single_engine = _value_plan(
            should_run=expected_value >= expected_cost,
            mode="bounded_current_probe",
            reason="Do not authorise a current-mail probe unless it can plausibly surface real response work above lane cost.",
            signals=["current probe cooldown elapsed"],
            expected_value_score=expected_value,
            expected_cost_score=expected_cost,
        )

    selected_lane = "none"
    for lane_name in LANE_PRIORITY:
        if lanes[lane_name]["should_run"]:
            selected_lane = lane_name
            break

    should_run = selected_lane != "none"
    recommended_mode = lanes[selected_lane]["mode"] if should_run else "skip"
    reason = lanes[selected_lane]["reason"] if should_run else "Keep the automation paused until a lane has a concrete benefit signal."
    signals = lanes[selected_lane]["signals"] if should_run else ["no lane is currently due"]

    plan = {
        "generated_at": _iso_now(),
        "automation_id": automation_id,
        "state_db": str(db_path),
        "should_run": should_run,
        "recommended_mode": recommended_mode,
        "selected_lane": selected_lane,
        "reason": reason,
        "signals": signals,
        "recent_runs": {
            "latest_run_id": latest_run_id,
            "latest_completed_at": latest_completed_at.isoformat() if latest_completed_at else None,
            "latest_coverage_status": latest_coverage_status,
            "latest_coverage_reasons": coverage_reasons,
            "latest_candidate_state_counts": latest_state_counts,
            "created_drafts_in_last_runs": recent_draft_count,
            "recent_run_limit": recent_run_limit,
            "hours_since_latest_run": _hours_since(latest_completed_at),
        },
        "probe_policy": {
            "current_probe_cooldown_hours": current_probe_cooldown_hours,
            "stale_research_cooldown_hours": stale_research_cooldown_hours,
            "current_probe_due": current_probe_due,
            "stale_research_due": stale_research_due,
        },
        "stale_research": {
            "status": stale_state["status"],
            "cursor_index": stale_state["cursor_index"],
            "window_start": stale_state["window_start"],
            "window_end": stale_state["window_end"],
            "window_label": _window_age_label(today, window_start, window_end),
            "oldest_boundary": stale_state["oldest_boundary"],
            "last_completed_run_id": stale_state.get("last_completed_run_id"),
            "last_completed_at": stale_state.get("last_completed_at"),
            "updated_at": stale_updated_at,
        },
        "lanes": lanes,
        "single_engine": single_engine,
    }
    connection.close()
    return plan


def complete_stale_window(
    *,
    db_path: Path,
    automation_id: str,
    run_id: str,
    recent_window_days: int,
    history_window_days: int,
    tranche_days: int,
) -> dict[str, Any]:
    connection = _connect(db_path)
    stale_state, _ = _load_or_seed_stale_state(
        connection,
        automation_id,
        recent_window_days=recent_window_days,
        history_window_days=history_window_days,
        tranche_days=tranche_days,
    )

    current_start = date.fromisoformat(str(stale_state["window_start"]))
    current_end = date.fromisoformat(str(stale_state["window_end"]))
    oldest_boundary = date.fromisoformat(str(stale_state["oldest_boundary"]))
    next_end = current_start
    next_start = next_end - timedelta(days=int(stale_state["tranche_days"]))

    stale_state["last_completed_run_id"] = run_id
    stale_state["last_completed_at"] = _iso_now()
    stale_state["cursor_index"] = int(stale_state["cursor_index"]) + 1

    if next_start <= oldest_boundary:
        stale_state["status"] = "exhausted"
    else:
        stale_state["status"] = "planned"
        stale_state["window_start"] = next_start.isoformat()
        stale_state["window_end"] = next_end.isoformat()

    updated_at = _save_control_state(connection, automation_id, STALE_RESEARCH_KEY, stale_state)
    connection.close()
    return {
        "automation_id": automation_id,
        "completed_run_id": run_id,
        "completed_window_start": current_start.isoformat(),
        "completed_window_end": current_end.isoformat(),
        "next_status": stale_state["status"],
        "next_window_start": stale_state.get("window_start"),
        "next_window_end": stale_state.get("window_end"),
        "updated_at": updated_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benefit gate and stale-window cursor for Gmail response automation.")
    parser.add_argument("command", choices=("plan", "complete-stale-window"))
    parser.add_argument("--state-db", default=str(DEFAULT_STATE_DB))
    parser.add_argument("--automation-id", default=DEFAULT_AUTOMATION_ID)
    parser.add_argument("--recent-window-days", type=int, default=30)
    parser.add_argument("--history-window-days", type=int, default=365)
    parser.add_argument("--tranche-days", type=int, default=30)
    parser.add_argument("--current-probe-cooldown-hours", type=int, default=24)
    parser.add_argument("--stale-research-cooldown-hours", type=int, default=168)
    parser.add_argument("--recent-run-limit", type=int, default=5)
    parser.add_argument("--run-id")
    args = parser.parse_args()

    db_path = Path(args.state_db).resolve()
    if not db_path.exists():
        raise SystemExit(f"State database not found: {db_path}")

    if args.command == "plan":
        payload = build_plan(
            db_path=db_path,
            automation_id=args.automation_id,
            recent_window_days=args.recent_window_days,
            history_window_days=args.history_window_days,
            tranche_days=args.tranche_days,
            current_probe_cooldown_hours=args.current_probe_cooldown_hours,
            stale_research_cooldown_hours=args.stale_research_cooldown_hours,
            recent_run_limit=args.recent_run_limit,
        )
    else:
        if not args.run_id:
            raise SystemExit("--run-id is required for complete-stale-window")
        payload = complete_stale_window(
            db_path=db_path,
            automation_id=args.automation_id,
            run_id=args.run_id,
            recent_window_days=args.recent_window_days,
            history_window_days=args.history_window_days,
            tranche_days=args.tranche_days,
        )

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
