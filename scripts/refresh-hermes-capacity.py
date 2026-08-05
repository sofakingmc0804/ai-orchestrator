#!/usr/bin/env python3
"""Refresh live Hermes capacity evidence and reconcile safe worker lanes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_HOME = Path(os.getenv("ORCHESTRATOR_HOME", str(REPO_ROOT / ".runtime" / "orchestrator"))).expanduser()
HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes"))).expanduser()
HERMES_PYTHON = Path(os.getenv("HERMES_DESKTOP_PYTHON", str(HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe")))
RUNTIME_ROSTER = ORCHESTRATOR_HOME / "worker_roster_v2.json"
SOURCE_ROSTER = REPO_ROOT / "data" / "rosters" / "worker_roster_v2.json"
RECEIPTS = ORCHESTRATOR_HOME / "receipts"
HERMES_ENV = HERMES_HOME / ".env"

sys.path.insert(0, str(REPO_ROOT))
from orchestrator.hermes.live_capacity import build_live_worker_card, merge_live_workers  # noqa: E402
from orchestrator.hermes.claude_code import CLAUDE_MODEL_ALIASES, oauth_only_environment  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def request_json(url: str, *, api_key: str | None = None, timeout: int = 30) -> tuple[int, Any, dict[str, str]]:
    headers = {"Accept": "application/json", "User-Agent": "ai-orchestrator-hermes-capacity/1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}, {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"error": body[:1000]}
        return exc.code, payload, {k.lower(): v for k, v in exc.headers.items()}
    except Exception as exc:
        return 0, {"error": str(exc)}, {}


def select_models(model_rows: list[dict[str, Any]], *, limit: int = 4, require_free_suffix: bool = False) -> list[dict[str, Any]]:
    rows = []
    for row in model_rows:
        model_id = str(row.get("id") or row.get("name") or "").strip()
        if not model_id:
            continue
        lowered = model_id.lower()
        if require_free_suffix and not lowered.endswith(":free"):
            continue
        if any(marker in lowered for marker in ("safety", "lyria", "image", "video")):
            continue
        priority = 50
        for index, marker in enumerate(("nemotron-3-ultra", "north-mini-code", "ling-3.0-flash", "laguna", "gemma", "deepseek", "qwen", "glm")):
            if marker in lowered:
                priority = index
                break
        rows.append((priority, lowered, row))
    rows.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in rows[:limit]]


def probe_openrouter(env: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    key = env.get("OPENROUTER_API_KEY", "")
    if not key:
        return {"status": "missing_key", "source": "openrouter_key_api"}, []
    status, models_payload, _ = request_json("https://openrouter.ai/api/v1/models", api_key=key)
    models = list(models_payload.get("data") or []) if isinstance(models_payload, dict) else []
    free = [row for row in models if str(row.get("id") or "").lower().endswith(":free") and str((row.get("pricing") or {}).get("prompt")) in {"0", "0.0"} and str((row.get("pricing") or {}).get("completion")) in {"0", "0.0"}]
    key_status, key_payload, _ = request_json("https://openrouter.ai/api/v1/key", api_key=key)
    key_data = key_payload.get("data") if isinstance(key_payload, dict) else {}
    ok = status == 200 and key_status == 200 and bool(free)
    quota = {
        "status": "ok" if ok else "failed",
        "source": "openrouter_key_api",
        "http_status": status,
        "key_http_status": key_status,
        "model_count": len(models),
        "free_model_count": len(free),
        "free_model_ids": [str(row.get("id")) for row in free[:20]],
        "usage_usd": key_data.get("usage") if isinstance(key_data, dict) else None,
        "limit_usd": key_data.get("limit") if isinstance(key_data, dict) else None,
        "limit_remaining_usd": key_data.get("limit_remaining") if isinstance(key_data, dict) else None,
        "remaining_budget_state": "free_models_available" if free else "unproved",
    }
    workers = [
        build_live_worker_card(
            provider_id="openrouter",
            model_id=str(row.get("id")),
            contract_type="subscription_usage",
            quota_state=quota,
            context_tokens=int(row.get("context_length") or 0),
        )
        for row in select_models(free, require_free_suffix=True)
    ]
    return quota, workers


def run_hermes_catalog() -> dict[str, Any]:
    code = '''
import json
from hermes_cli.model_switch import list_authenticated_providers
from hermes_cli.models import check_nous_free_tier, get_pricing_for_provider
from agent.account_usage import fetch_account_usage, nous_credits_lines
rows = list_authenticated_providers(current_provider="openrouter", current_model="z-ai/glm-5.2", refresh=True, for_picker=True, max_models=None)
out = {"providers": rows, "nous_free_tier": check_nous_free_tier(force_fresh=True), "nous_pricing": get_pricing_for_provider("nous") or {}, "nous_credits_lines": nous_credits_lines(timeout=20)}
snap = fetch_account_usage("openai-codex")
out["openai-codex_usage"] = None if snap is None else {"source": snap.source, "windows": [{"label": w.label, "used_percent": w.used_percent, "reset_at": w.reset_at, "detail": w.detail} for w in snap.windows], "details": list(snap.details), "unavailable_reason": snap.unavailable_reason}
print(json.dumps(out, default=str))
'''
    if not HERMES_PYTHON.is_file():
        return {"error": f"Hermes Python not found at {HERMES_PYTHON}"}
    env = dict(os.environ)
    env["HERMES_HOME"] = str(HERMES_HOME)
    env["PYTHONPATH"] = str(HERMES_HOME / "hermes-agent")
    result = subprocess.run([str(HERMES_PYTHON), "-c", code], capture_output=True, text=True, timeout=180, env=env, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return {"error": (result.stderr or result.stdout)[-2000:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "Hermes catalog helper returned malformed JSON", "output_tail": result.stdout[-2000:]}


def probe_hermes_provider(catalog: dict[str, Any], provider_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [row for row in catalog.get("providers", []) if str(row.get("slug") or "").lower() == provider_id]
    models = [str(model) for model in (rows[0].get("models") if rows else []) or []]
    if provider_id == "nous":
        pricing = catalog.get("nous_pricing") or {}
        free_models = [model for model in models if str((pricing.get(model) or {}).get("prompt")) in {"0", "0.0"} and str((pricing.get(model) or {}).get("completion")) in {"0", "0.0"}]
        lines = [str(line) for line in catalog.get("nous_credits_lines") or []]
        access_depleted = any("access depleted" in line.lower() for line in lines)
        quota = {
            "status": "degraded" if access_depleted else "ok",
            "source": "nous_account_usage",
            "model_count": len(models),
            "free_model_count": len(free_models),
            "free_model_ids": free_models[:20],
            "remaining_budget_state": "access_depleted" if access_depleted else "free_tier_present",
            "credits_lines": lines[:12],
        }
        workers = [
            build_live_worker_card(
                provider_id="nous",
                model_id=model.get("id"),
                contract_type="subscription_usage",
                quota_state=quota,
            )
            for model in select_models([{"id": model} for model in free_models], limit=4)
        ]
        return quota, workers
    if provider_id == "openai-codex":
        usage = catalog.get("openai-codex_usage") or {}
        windows = usage.get("windows") or []
        used = float(windows[0].get("used_percent")) if windows and windows[0].get("used_percent") is not None else None
        remaining = round(100.0 - used, 2) if used is not None else None
        quota = {
            "status": "ok" if remaining is not None else "unknown",
            "source": "codex_usage_api",
            "model_count": len(models),
            "percent_remaining": remaining,
            "remaining_budget_state": f"{remaining}%_remaining" if remaining is not None else "unproved",
            "windows": windows[:4],
            "details": list(usage.get("details") or []),
        }
        workers = [
            build_live_worker_card(
                provider_id="openai-codex",
                model_id=model.get("id"),
                contract_type="subscription_quota",
                quota_state=quota,
            )
            for model in select_models([{"id": model} for model in models], limit=5)
        ]
        return quota, workers
    return {"status": "unknown", "source": f"{provider_id}_catalog"}, []


def probe_opencode(env: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    key = env.get("OPENCODE_ZEN_API_KEY") or env.get("OPENCODE_API_KEY")
    if not key:
        return {"status": "missing_key", "source": "opencode_zen_models"}, []
    status, payload, headers = request_json("https://opencode.ai/zen/v1/models", api_key=key)
    models = list(payload.get("data") or []) if isinstance(payload, dict) else []
    quota = {
        "status": "model_catalog_ok" if status == 200 else "failed",
        "source": "opencode_zen_models",
        "http_status": status,
        "model_count": len(models),
        "remaining_budget_state": "billing_unproved",
        "billing_note": "Provider documentation describes Zen as billed per request; no included quota endpoint was exposed.",
        "rate_limit_headers_present": any(key.startswith("x-ratelimit") for key in headers),
    }
    workers = [
        build_live_worker_card(
            provider_id="opencode-zen",
            model_id=str(row.get("id")),
            contract_type="third_party_metered",
            quota_state=quota,
            context_tokens=int(row.get("context_length") or 0),
        )
        for row in select_models(models, limit=4)
        if str(row.get("id") or "").lower().endswith("-free")
    ]
    return quota, workers


def probe_lmstudio() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    status, payload, _ = request_json("http://127.0.0.1:1234/v1/models")
    models = list(payload.get("data") or []) if isinstance(payload, dict) else []
    quota = {"status": "ok" if status == 200 and models else "degraded", "source": "lmstudio_local_models", "http_status": status, "model_count": len(models), "remaining_budget_state": "local_hardware_only"}
    workers = [
        build_live_worker_card(
            provider_id="lm-studio",
            model_id=str(row.get("id")),
            contract_type="local_resource",
            quota_state=quota,
            context_tokens=int(row.get("max_context_length") or 0),
            capabilities=["embeddings", "embed_text"] if "embed" in str(row.get("id") or "").lower() else None,
        )
        for row in models
    ]
    return quota, workers


def probe_claude_code() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prove Claude.ai OAuth and expose the CLI's supported model aliases."""

    command = shutil.which("claude")
    fallback = Path.home() / ".local" / "bin" / "claude.exe"
    if not command and fallback.is_file():
        command = str(fallback)
    if not command:
        return {"status": "missing_cli", "source": "claude_code_oauth_status"}, []

    try:
        result = subprocess.run(
            [command, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=oauth_only_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unreachable", "source": "claude_code_oauth_status", "error": str(exc)}, []

    try:
        status = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        status = {}
    logged_in = bool(isinstance(status, dict) and status.get("loggedIn") and status.get("authMethod") == "claude.ai")
    account = str(status.get("email") or "") if isinstance(status, dict) else ""
    quota = {
        "status": "ok" if result.returncode == 0 and logged_in else "failed",
        "source": "claude_code_oauth_status",
        "auth_method": "claude.ai",
        "account_id": account or "authenticated-claude-account",
        "model_aliases": list(CLAUDE_MODEL_ALIASES),
        "remaining_budget_state": "subscription_window_unproved",
        "usage_note": "Auth status proves the provider-owned OAuth session; Claude Code usage windows remain provider-owned.",
    }
    if not logged_in:
        quota["error"] = "Claude Code is not logged in through Claude.ai OAuth in an OAuth-only environment."
        return quota, []

    workers = [
        build_live_worker_card(
            provider_id="claude-max",
            model_id=model,
            contract_type="subscription_quota",
            quota_state=quota,
            capabilities=["reasoning", "coding", "agentic", "tools", "model_selection", "headless_print", "subscription_oauth"],
        )
        for model in CLAUDE_MODEL_ALIASES
    ]
    return quota, workers


def probe_omniroute() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    status, payload, _ = request_json("http://127.0.0.1:20128/v1/models")
    models = list(payload.get("data") or []) if isinstance(payload, dict) else []
    quota = {
        "status": "gateway_live" if status == 200 and models else "degraded",
        "source": "omniroute_local_models",
        "http_status": status,
        "model_count": len(models),
        "remaining_budget_state": "upstream_billing_unproved",
        "billing_note": "OmniRoute is a local gateway; its upstream provider cost/quota is not admitted by this refresh.",
    }
    workers = [
        build_live_worker_card(
            provider_id="omniroute",
            model_id=str(row.get("id")),
            contract_type="unknown_cost",
            quota_state=quota,
            context_tokens=int(row.get("context_length") or 0),
            capabilities=["chat", "reasoning", "structured_output", "coding", "tools"],
        )
        for row in select_models(models, limit=3)
    ]
    return quota, workers


def upsert_worker_cards(workers: list[dict[str, Any]]) -> None:
    db_path = ORCHESTRATOR_HOME / "state.sqlite"
    if not db_path.is_file():
        return
    con = sqlite3.connect(db_path, timeout=30)
    try:
        for worker in workers:
            con.execute(
                """
                INSERT INTO worker_cards(worker_id,model_id,base_model,surface,provider_id,contract_type,salary_bucket,overtime_rule,
                  budget_source_id,hardware_fit,context_window,capabilities_json,modalities_json,tools_json,stats_json,best_jobs_json,avoid_jobs_json,
                  badges_json,approval_required,marginal_cost_json,source_evidence_json,dynamic_state_json,last_verified,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET
                  model_id=excluded.model_id, base_model=excluded.base_model, surface=excluded.surface, provider_id=excluded.provider_id,
                  contract_type=excluded.contract_type, salary_bucket=excluded.salary_bucket, overtime_rule=excluded.overtime_rule,
                  budget_source_id=excluded.budget_source_id, hardware_fit=excluded.hardware_fit, context_window=excluded.context_window,
                  capabilities_json=excluded.capabilities_json, modalities_json=excluded.modalities_json, tools_json=excluded.tools_json,
                  stats_json=excluded.stats_json, best_jobs_json=excluded.best_jobs_json, avoid_jobs_json=excluded.avoid_jobs_json,
                  badges_json=excluded.badges_json, approval_required=excluded.approval_required, marginal_cost_json=excluded.marginal_cost_json,
                  source_evidence_json=excluded.source_evidence_json, dynamic_state_json=excluded.dynamic_state_json,
                  last_verified=excluded.last_verified, updated_at=excluded.updated_at
                """,
                (
                    worker["worker_id"], worker["model_id"], worker["base_model"], worker["surface"], worker["provider_id"], worker["contract_type"],
                    worker["salary_bucket"], worker["overtime_rule"], worker["budget_source_id"], worker["hardware_fit"], worker["context_window"],
                    json.dumps(worker["capabilities"], sort_keys=True), json.dumps(worker["modalities"], sort_keys=True), json.dumps(worker["tools"], sort_keys=True),
                    json.dumps(worker["stats"], sort_keys=True), json.dumps(worker["best_jobs"], sort_keys=True), json.dumps(worker["avoid_jobs"], sort_keys=True),
                    json.dumps(worker["badges"], sort_keys=True), int(worker["approval_required"]), json.dumps(worker["marginal_cost"], sort_keys=True),
                    json.dumps(worker["source_evidence"], sort_keys=True), json.dumps(worker["dynamic_state"], sort_keys=True), worker["last_verified"], utc_now(),
                ),
            )
        con.commit()
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-source-roster", action="store_true", help="Update only the active runtime roster.")
    args = parser.parse_args()
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    env = read_env_file(HERMES_ENV)
    provider_results: dict[str, Any] = {}
    live_workers: list[dict[str, Any]] = []

    for provider, probe in (
        ("openrouter", lambda: probe_openrouter(env)),
        ("opencode-zen", lambda: probe_opencode(env)),
        ("claude-max", probe_claude_code),
        ("lm-studio", probe_lmstudio),
        ("omniroute", probe_omniroute),
    ):
        quota, workers = probe()
        provider_results[provider] = {"quota": quota, "worker_count": len(workers), "worker_ids": [worker["worker_id"] for worker in workers]}
        live_workers.extend(workers)
        write_json(RECEIPTS / f"hermes-capacity-{provider}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json", {"provider": provider, "quota": quota, "worker_ids": [worker["worker_id"] for worker in workers], "created_at": utc_now()})

    catalog = run_hermes_catalog()
    if "error" not in catalog:
        for provider in ("nous", "openai-codex"):
            quota, workers = probe_hermes_provider(catalog, provider)
            provider_results[provider] = {"quota": quota, "worker_count": len(workers), "worker_ids": [worker["worker_id"] for worker in workers]}
            live_workers.extend(workers)
            write_json(RECEIPTS / f"hermes-capacity-{provider}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json", {"provider": provider, "quota": quota, "worker_ids": [worker["worker_id"] for worker in workers], "created_at": utc_now()})
    else:
        provider_results["hermes-native-catalog"] = catalog

    targets = [RUNTIME_ROSTER]
    if not args.no_source_roster:
        targets.append(SOURCE_ROSTER)
    roster_counts: dict[str, int] = {}
    for target in targets:
        existing = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {"version": 2, "workers": []}
        merged = merge_live_workers(existing, live_workers)
        write_json(target, merged)
        roster_counts[str(target)] = len(merged.get("workers") or [])
    upsert_worker_cards(live_workers)
    final = {
        "ok": bool(live_workers),
        "terminal_state": "built" if live_workers else "continuation_required",
        "provider_results": provider_results,
        "live_worker_count": len(live_workers),
        "roster_counts": roster_counts,
        "database": str(ORCHESTRATOR_HOME / "state.sqlite"),
        "created_at": utc_now(),
        "credential_policy": "Provider-owned auth/env stores were read; no credentials copied.",
    }
    final_path = RECEIPTS / f"hermes-capacity-refresh-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(final_path, final)
    print(json.dumps({"receipt": str(final_path), **final}, indent=2, sort_keys=True))
    return 0 if final["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
