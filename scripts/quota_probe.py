#!/usr/bin/env python3
"""
Quota Probe — Live-read all AI subscription quotas.

Probes every reachable quota source and outputs a structured snapshot.
Designed to run at session start (auto-probe) and on a cron schedule (continuous monitoring).
Alerts at 20% remaining on any source.

Output: JSON snapshot to stdout. Non-zero exit if any source is in alert state.
Silent (empty stdout, exit 0) when all sources are healthy — for cron use.
"""

import json
import subprocess
import sys
import os
from datetime import datetime, timezone

def run_cmd(cmd: str, timeout: int = 30) -> tuple[str, int]:
    """Run a shell command, return (output, exit_code)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except Exception as e:
        return f"ERROR: {e}", -1

def probe_copilot() -> dict:
    """GitHub Copilot — monthly reset, 1,500 premium interactions."""
    out, code = run_cmd("gh api /copilot_internal/user 2>&1")
    if code != 0 or not out:
        return {"source": "GitHub Copilot", "status": "unreachable", "error": out[:200]}
    try:
        d = json.loads(out)
        pi = d.get("quota_snapshots", {}).get("premium_interactions", {})
        remaining = pi.get("remaining", 0)
        total = pi.get("entitlement", 1500)
        pct = (remaining / total * 100) if total else 0
        reset = d.get("quota_reset_date", "unknown")
        return {
            "source": "GitHub Copilot",
            "status": "ok" if pct > 20 else "ALERT",
            "remaining": remaining,
            "total": total,
            "percent_remaining": round(pct, 1),
            "reset_date": reset,
            "contract": "subscription_quota (monthly)",
            "probe": "gh api /copilot_internal/user"
        }
    except json.JSONDecodeError:
        return {"source": "GitHub Copilot", "status": "parse_error", "raw": out[:200]}

def probe_claude_code() -> dict:
    """Claude Code — weekly reset, Claude Max subscription.
    Uses /usage in-session, but we can check auth state and last known usage."""
    out, code = run_cmd("claude auth status 2>&1", timeout=10)
    if "loggedIn" not in out:
        return {"source": "Claude Code", "status": "unreachable", "error": out[:200]}
    try:
        d = json.loads(out)
        return {
            "source": "Claude Code",
            "status": "ok",
            "auth": "logged_in",
            "email": d.get("email", "?"),
            "subscription": d.get("subscriptionType", "?"),
            "remaining": "unknown (check /usage in-session)",
            "reset": "weekly (check in-session)",
            "contract": "subscription_unlimited (weekly window)",
            "probe": "claude auth status + /usage in-session"
        }
    except json.JSONDecodeError:
        return {"source": "Claude Code", "status": "ok", "auth": "logged_in", "raw": out[:200]}

def probe_codex() -> dict:
    """Codex CLI — ChatGPT subscription."""
    out, code = run_cmd("codex login status 2>&1", timeout=10)
    if "Logged in" not in out:
        return {"source": "Codex CLI", "status": "unreachable", "error": out[:200]}
    return {
        "source": "Codex CLI",
        "status": "ok",
        "auth": "logged_in (ChatGPT)",
        "remaining": "unknown (5-hour + weekly windows)",
        "reset": "rolling 5h windows + weekly",
        "contract": "subscription_unlimited (ChatGPT account)",
        "note": "SSL issues on this host prevent live dispatch — see audit",
        "probe": "codex login status"
    }

def probe_ollama_cloud() -> dict:
    """Ollama Cloud — flat-rate, monthly reset.
    No API to probe directly; check via Hermes config."""
    out, code = run_cmd("hermes config get model.provider 2>&1", timeout=10)
    if "ollama-cloud" in out:
        return {
            "source": "Ollama Cloud",
            "status": "ok",
            "remaining": "flat-rate (effectively unlimited)",
            "reset": "monthly",
            "contract": "subscription_quota (GPU-pressure flat)",
            "probe": "hermes config get model.provider"
        }
    return {"source": "Ollama Cloud", "status": "unreachable", "error": out[:200]}

def probe_chatgpt_desktop() -> dict:
    """ChatGPT Desktop — rolling usage, no API. Computer-use probe only."""
    # Check if the app is installed
    app_path = os.path.expanduser("~/AppData/Local/OpenAI")
    if os.path.exists(app_path):
        return {
            "source": "ChatGPT Desktop",
            "status": "installed",
            "remaining": "unknown (requires computer-use → usage tab)",
            "reset": "rolling",
            "contract": "subscription (ChatGPT Plus/Pro)",
            "probe": "computer-use → user menu → usage"
        }
    return {"source": "ChatGPT Desktop", "status": "not_installed"}

def probe_claude_desktop() -> dict:
    """Claude Desktop — weekly reset, no API. Computer-use probe only."""
    app_path = os.path.expanduser("~/AppData/Local/AnthropicClaude")
    if os.path.exists(app_path):
        return {
            "source": "Claude Desktop",
            "status": "installed",
            "remaining": "unknown (requires computer-use → settings → usage)",
            "reset": "weekly",
            "contract": "subscription (Claude Max/Pro)",
            "probe": "computer-use → user menu → settings → usage"
        }
    return {"source": "Claude Desktop", "status": "not_installed"}

def main():
    """Run all probes and output snapshot."""
    probes = [
        probe_copilot(),
        probe_claude_code(),
        probe_codex(),
        probe_ollama_cloud(),
        probe_chatgpt_desktop(),
        probe_claude_desktop(),
    ]
    
    snapshot = {
        "probe_time": datetime.now(timezone.utc).isoformat(),
        "sources": probes,
        "alerts": [p for p in probes if p.get("status") == "ALERT"],
    }
    
    # Check for alerts
    has_alerts = len(snapshot["alerts"]) > 0
    
    if "--json" in sys.argv:
        print(json.dumps(snapshot, indent=2))
    elif "--human" in sys.argv:
        # Human-readable (explicit request)
        print(f"Quota Snapshot — {snapshot['probe_time'][:19]}Z")
        print("=" * 60)
        for p in probes:
            status = p.get("status", "?")
            icon = "✅" if status in ("ok", "installed") else "⚠️" if status == "ALERT" else "❌"
            print(f"{icon} {p['source']}: {status}")
            if "remaining" in p:
                print(f"   Remaining: {p['remaining']}")
            if "reset" in p:
                print(f"   Reset: {p['reset']}")
            if "contract" in p:
                print(f"   Contract: {p['contract']}")
        if has_alerts:
            print(f"\n⚠️  {len(snapshot['alerts'])} ALERT(S) — quota below 20%")
    else:
        # Default: quiet mode (for cron) — only output if alerts
        if has_alerts:
            alert_msgs = []
            for a in snapshot["alerts"]:
                alert_msgs.append(f"⚠ {a['source']}: {a.get('percent_remaining', '?')}% remaining (resets {a.get('reset_date', '?')})")
            print(f"QUOTA ALERT: {'; '.join(alert_msgs)}")
        # Silent if no alerts
    
    sys.exit(1 if has_alerts else 0)

if __name__ == "__main__":
    main()