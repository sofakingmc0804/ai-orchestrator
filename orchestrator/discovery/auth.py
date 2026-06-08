from __future__ import annotations

import json
import subprocess
from typing import Any


def _run(args: list[str], timeout: int = 20) -> dict[str, Any]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}


def probe_auth_and_quota() -> dict[str, Any]:
    result: dict[str, Any] = {
        "github_copilot": _run(["gh", "api", "/copilot_internal/user"]),
        "gh_auth": _run(["gh", "auth", "status"]),
        "hermes_auth": _run(["hermes", "auth", "list"]),
        "hermes_status": _run(["hermes", "status"]),
        "claude_auth": _run(["claude", "auth", "status"]),
        "codex_auth": _run(["codex.cmd", "login", "status"]),
    }
    copilot = result["github_copilot"]
    if copilot.get("ok") and copilot.get("stdout"):
        try:
            payload = json.loads(copilot["stdout"])
            premium = payload.get("quota_snapshots", {}).get("premium_interactions", {})
            result["github_copilot_summary"] = {
                "plan": payload.get("copilot_plan"),
                "remaining": premium.get("remaining"),
                "entitlement": premium.get("entitlement"),
                "percent_remaining": premium.get("percent_remaining"),
                "reset_at": payload.get("quota_reset_date_utc"),
            }
        except json.JSONDecodeError:
            result["github_copilot_summary"] = {"parse_error": True}
    return result


def quota_snapshots(auth_state: dict[str, Any]) -> list[dict[str, Any]]:
    summary = auth_state.get("github_copilot_summary")
    if not isinstance(summary, dict):
        return []
    remaining = summary.get("remaining")
    limit = summary.get("entitlement")
    if not isinstance(remaining, int) or not isinstance(limit, int):
        return []
    return [
        {
            "provider": "github_copilot",
            "units_consumed": max(limit - remaining, 0),
            "units_limit": limit,
            "reset_at": summary.get("reset_at"),
            "remaining": remaining,
            "percent_remaining": summary.get("percent_remaining"),
        }
    ]
