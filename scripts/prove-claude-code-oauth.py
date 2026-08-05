#!/usr/bin/env python3
"""Prove Claude Code subscription OAuth and selected headless model aliases."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.hermes.claude_code import (
    CLAUDE_MODEL_ALIASES,
    build_headless_args,
    oauth_only_environment,
    parse_headless_output,
)


def resolve_claude() -> str | None:
    return shutil.which("claude") or (
        str(Path.home() / ".local" / "bin" / "claude.exe")
        if (Path.home() / ".local" / "bin" / "claude.exe").is_file()
        else None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", dest="models", help="Probe one model alias or full model id.")
    parser.add_argument("--all-models", action="store_true", help="Probe every supported CLI alias.")
    args = parser.parse_args()
    command = resolve_claude()
    if not command:
        print(json.dumps({"ok": False, "error": "claude executable not found"}, indent=2))
        return 2

    env = oauth_only_environment()
    auth = subprocess.run(
        [command, "auth", "status"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=env,
    )
    try:
        auth_payload = json.loads(auth.stdout or "{}")
    except json.JSONDecodeError:
        auth_payload = {}
    auth_ok = bool(
        auth.returncode == 0
        and isinstance(auth_payload, dict)
        and auth_payload.get("loggedIn")
        and auth_payload.get("authMethod") == "claude.ai"
    )
    selected = list(args.models or [])
    if args.all_models or not selected:
        selected = list(CLAUDE_MODEL_ALIASES) if args.all_models else ["sonnet"]

    results: list[dict[str, object]] = []
    for model in selected:
        try:
            completed = subprocess.run(
                [command, *build_headless_args("Return exactly HERMES_CLAUDE_MODEL_PROBE_OK.", model)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            results.append({"model": model, "ok": False, "error": str(exc)})
            continue
        text, payload = parse_headless_output(completed.stdout)
        model_usage = payload.get("modelUsage") if isinstance(payload, dict) and isinstance(payload.get("modelUsage"), dict) else {}
        canonical_models = sorted(
            {
                str(item.get("canonicalModel") or model_id)
                for model_id, item in model_usage.items()
                if isinstance(item, dict)
            }
        )
        results.append(
            {
                "model": model,
                "ok": bool(completed.returncode == 0 and "HERMES_CLAUDE_MODEL_PROBE_OK" in text),
                "returncode": completed.returncode,
                "response_marker": "HERMES_CLAUDE_MODEL_PROBE_OK" in text,
                "reported_usage": bool(isinstance(payload, dict) and payload.get("usage")),
                "canonical_models": canonical_models,
                "error": (completed.stderr or "").strip()[-500:] if completed.returncode else None,
            }
        )

    payload = {
        "ok": auth_ok and all(bool(item.get("ok")) for item in results),
        "auth": {
            "logged_in": auth_ok,
            "auth_method": auth_payload.get("authMethod") if isinstance(auth_payload, dict) else None,
            "api_key_source_present": bool(isinstance(auth_payload, dict) and auth_payload.get("apiKeySource")),
        },
        "models": results,
        "credential_policy": "Claude Code owns OAuth storage; verifier never reads or copies the credential file.",
    }
    receipt_dir = REPO_ROOT / ".runtime" / "orchestrator" / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"hermes-claude-code-oauth-proof-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    payload["receipt"] = str(receipt_path)
    receipt_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
