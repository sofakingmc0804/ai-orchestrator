#!/usr/bin/env python3
"""Off-hours maintenance for the orchestrator state database.

What it does (in order):
  1. PRAGMA integrity_check on a READ-ONLY connection.
  2. VACUUM INTO a dated, compacted backup under .runtime/backups/.
     VACUUM INTO only READS the source, so this is safe to run while the
     orchestrator server is live (WAL readers get a consistent snapshot).
  3. Verify the backup opens and passes its own integrity_check.
  4. Prune backups older than the retention window.
  5. Write a JSON receipt under .runtime/orchestrator/maintenance/.

Exit code is 0 on a clean run, 1 if integrity or the backup verification failed
(so a scheduler can surface the failure).

Env overrides:
  ORCHESTRATOR_HOME                      -> runtime home (default <repo>/.runtime/orchestrator)
  ORCHESTRATOR_STATE_DB                  -> explicit state DB path
  ORCHESTRATOR_DB_BACKUP_RETENTION_DAYS  -> backup retention (default 7)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.getenv("ORCHESTRATOR_HOME", REPO_ROOT / ".runtime" / "orchestrator"))
STATE_DB = Path(os.getenv("ORCHESTRATOR_STATE_DB", HOME / "state.sqlite"))
BACKUP_DIR = REPO_ROOT / ".runtime" / "backups"
RECEIPT_DIR = HOME / "maintenance"
RETENTION_DAYS = int(os.getenv("ORCHESTRATOR_DB_BACKUP_RETENTION_DAYS", "7"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _ro_connect(path: Path, timeout: float) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)


def _write_receipt(receipt: dict, started: datetime) -> Path:
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = RECEIPT_DIR / f"{_stamp(started)}-db-maintenance.json"
    receipt["receipt_path"] = str(path)
    path.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    started = _now()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    receipt: dict = {
        "event": "db_maintenance",
        "proof_kind": "live",
        "started_at": started.isoformat(),
        "state_db": str(STATE_DB),
        "steps": {},
    }

    if not STATE_DB.exists():
        receipt.update(state="skipped", healthy=True, error="state database not found", completed_at=_now().isoformat())
        _write_receipt(receipt, started)
        print(f"skipped: state database not found at {STATE_DB}")
        return 0

    receipt["source_bytes"] = STATE_DB.stat().st_size

    # 1) integrity check on the live DB (read-only)
    integrity = "unknown"
    integrity_err = None
    try:
        con = _ro_connect(STATE_DB, timeout=120)
        try:
            integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error as exc:
        integrity_err = str(exc)
    receipt["steps"]["integrity_check"] = {"result": integrity, "error": integrity_err}

    # 2) VACUUM INTO a dated, compacted backup (read-only on the source)
    backup_path = BACKUP_DIR / f"state-{_stamp(started)}.sqlite"
    vacuum_err = None
    try:
        con = _ro_connect(STATE_DB, timeout=600)
        try:
            con.execute("VACUUM INTO ?", (str(backup_path),))
        finally:
            con.close()
    except sqlite3.Error as exc:
        vacuum_err = str(exc)

    backup_ok = False
    if vacuum_err:
        receipt["steps"]["vacuum_into"] = {"ok": False, "error": vacuum_err}
    else:
        # 3) verify the backup opens and is internally consistent
        backup_bytes = backup_path.stat().st_size if backup_path.exists() else 0
        backup_integrity = "unknown"
        backup_err = None
        services_rows = None
        try:
            bc = _ro_connect(backup_path, timeout=120)
            try:
                backup_integrity = str(bc.execute("PRAGMA integrity_check").fetchone()[0])
                services_rows = int(bc.execute("SELECT count(*) FROM services").fetchone()[0])
            finally:
                bc.close()
        except sqlite3.Error as exc:
            backup_err = str(exc)
        backup_ok = backup_integrity == "ok" and backup_err is None
        receipt["steps"]["vacuum_into"] = {
            "ok": True,
            "backup": str(backup_path),
            "backup_bytes": backup_bytes,
            "reclaimed_bytes": receipt["source_bytes"] - backup_bytes,
            "backup_integrity": backup_integrity,
            "backup_error": backup_err,
            "services_rows": services_rows,
        }

    # 4) prune old backups
    cutoff = started - timedelta(days=RETENTION_DAYS)
    pruned: list[str] = []
    for path in BACKUP_DIR.glob("state-*.sqlite"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                path.unlink()
                pruned.append(path.name)
        except OSError:
            pass
    receipt["steps"]["prune"] = {"retention_days": RETENTION_DAYS, "pruned": pruned}

    healthy = integrity_err is None and integrity == "ok" and vacuum_err is None and backup_ok
    receipt["state"] = "produced" if healthy else "blocked_after_repair_attempt"
    receipt["healthy"] = healthy
    receipt["completed_at"] = _now().isoformat()
    _write_receipt(receipt, started)

    vac = receipt["steps"].get("vacuum_into", {})
    print(
        json.dumps(
            {
                "healthy": healthy,
                "integrity": integrity,
                "integrity_error": integrity_err,
                "backup": vac.get("backup"),
                "backup_integrity": vac.get("backup_integrity"),
                "source_mb": round(receipt["source_bytes"] / 1048576, 1),
                "backup_mb": round(vac.get("backup_bytes", 0) / 1048576, 1),
                "reclaimed_mb": round(vac.get("reclaimed_bytes", 0) / 1048576, 1),
                "pruned": pruned,
            },
            indent=2,
        )
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
