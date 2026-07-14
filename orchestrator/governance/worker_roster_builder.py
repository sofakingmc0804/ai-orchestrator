#!/usr/bin/env python3
"""Build the v2 model workforce roster and refresh live budget probes.

This script is intentionally dependency-free. It upgrades the local governor
database, writes a worker roster, records budget probe receipts, resolves the
Hermes CLI/Desktop model split, and emits a compact governed Hermes catalog.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings

SETTINGS = Settings.load()
ROOT = SETTINGS.home
LEGACY_ROOT = Path.home() / ".ai-resource-governor"
DB_PATH = SETTINGS.state_path
ROSTER_PATH = ROOT / "worker_roster_v2.json"
HERMES_CATALOG_PATH = ROOT / "hermes-model-catalog.compact.json"
RECEIPTS = ROOT / "receipts"
REPAIR_QUEUE = ROOT / "repair-queue.jsonl"
CLI_HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"
DESKTOP_HERMES_HOME = Path.home() / "AppData" / "Local" / "hermes"
DESKTOP_HERMES_CONFIG = DESKTOP_HERMES_HOME / "config.yaml"
DESKTOP_HERMES_CACHE = DESKTOP_HERMES_HOME / "cache" / "model_catalog.json"
DESKTOP_GOVERNED_CATALOG = DESKTOP_HERMES_HOME / "cache" / "model_catalog.governed.json"
PROVIDER_MODELS_CACHE = DESKTOP_HERMES_HOME / "provider_models_cache.json"
OLLAMA_CLOUD_CACHE = DESKTOP_HERMES_HOME / "ollama_cloud_models_cache.json"
SOURCE_ROSTER = ROOT / "model-roster.json" if (ROOT / "model-roster.json").exists() else LEGACY_ROOT / "model-roster.json"
NOUS_FREE_DEFAULT = "stepfun/step-3.7-flash:free"
NOUS_PROVED_FREE_MODELS = {"stepfun/step-3.7-flash:free", "nvidia/nemotron-3-ultra:free"}


JOB_CLASSES: dict[str, dict[str, Any]] = {
    "routing_triage": {
        "required_capabilities": ["classification", "routing"],
        "preferred_stats": {"speed": 8, "structured_output": 7, "cost_pressure": 9},
        "local_first": True,
        "approval_floor": "local_resource",
    },
    "bulk_extraction": {
        "required_capabilities": ["classification"],
        "preferred_stats": {"speed": 7, "structured_output": 8, "cost_pressure": 9},
        "local_first": True,
        "approval_floor": "local_resource",
    },
    "schema_validation": {
        "required_capabilities": ["validation"],
        "preferred_stats": {"structured_output": 9, "cost_pressure": 8},
        "local_first": True,
        "approval_floor": "local_resource",
    },
    "simple_coding": {
        "required_capabilities": ["coding"],
        "preferred_stats": {"coding": 7, "speed": 7},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "repo_coding": {
        "required_capabilities": ["coding", "tools"],
        "preferred_stats": {"coding": 8, "agentic_loop": 7, "stability": 7},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "agentic_repair": {
        "required_capabilities": ["agentic", "tools", "coding"],
        "preferred_stats": {"agentic_loop": 9, "debugging": 8, "stability": 7},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "deep_debugging": {
        "required_capabilities": ["reasoning", "coding"],
        "preferred_stats": {"debugging": 9, "architecture": 7, "long_context": 7},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "architecture": {
        "required_capabilities": ["reasoning"],
        "preferred_stats": {"architecture": 9, "long_context": 8, "stability": 8},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "security_review": {
        "required_capabilities": ["reasoning", "code_review"],
        "preferred_stats": {"debugging": 9, "structured_output": 8, "stability": 8},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "long_context_synthesis": {
        "required_capabilities": ["reasoning"],
        "preferred_stats": {"long_context": 9, "structured_output": 7},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "visual_reasoning": {
        "required_capabilities": ["vision"],
        "preferred_stats": {"structured_output": 7, "speed": 6},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "ocr_document_intake": {
        "required_capabilities": ["ocr", "document_intake"],
        "preferred_stats": {"speed": 7, "structured_output": 8, "cost_pressure": 9},
        "local_first": True,
        "approval_floor": "local_resource",
    },
    "public_content": {
        "required_capabilities": ["drafting"],
        "preferred_stats": {"public_writing_fit": 8, "stability": 7},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
    "business_sensitive": {
        "required_capabilities": ["reasoning", "structured_output"],
        "preferred_stats": {"stability": 9, "structured_output": 9},
        "local_first": False,
        "approval_floor": "subscription_quota",
    },
}


CONTRACTS: dict[str, dict[str, Any]] = {
    "local_resource": {
        "approval_required": False,
        "overtime_rule": "No cash overtime; blocked only by hardware/time failure.",
        "salary_bucket": "Dell local compute",
    },
    "subscription_unlimited": {
        "approval_required": False,
        "overtime_rule": "Allowed while logged in and within account policy.",
        "salary_bucket": "monthly subscription",
    },
    "subscription_quota": {
        "approval_required": False,
        "overtime_rule": "Allowed above reserve; below reserve requires urgent consequence and value reason.",
        "salary_bucket": "monthly allowance or premium quota",
    },
    "subscription_usage": {
        "approval_required": False,
        "overtime_rule": "Allowed only when live usage source proves included balance or plan allowance.",
        "salary_bucket": "included usage allowance",
    },
    "metered_extra_cost": {
        "approval_required": True,
        "overtime_rule": "Blocked unless this run has explicit approval for paid API use.",
        "salary_bucket": "direct API billing",
    },
    "third_party_metered": {
        "approval_required": True,
        "overtime_rule": "Blocked unless the third-party meter is approved and balance is proved.",
        "salary_bucket": "marketplace billing",
    },
    "unknown_cost": {
        "approval_required": True,
        "overtime_rule": "Blocked until classified.",
        "salary_bucket": "unproved",
    },
}


PROVIDER_CONTRACTS = {
    "ollama-local": "local_resource",
    "ollama-cloud": "subscription_usage",
    "github-copilot": "subscription_quota",
    "codex-chatgpt": "subscription_unlimited",
    "claude-max": "subscription_quota",
    "gemini-oauth": "subscription_quota",
    "openrouter": "third_party_metered",
    "openai-api": "metered_extra_cost",
    "anthropic-api": "metered_extra_cost",
    "nous": "subscription_usage",
    "unknown": "unknown_cost",
}


OPENAI_PRICES = {
    "gpt-5.5": {"input_per_mtok": 5.00, "cached_input_per_mtok": 0.50, "output_per_mtok": 30.00},
    "gpt-5.5-pro": {"input_per_mtok": 30.00, "cached_input_per_mtok": None, "output_per_mtok": 180.00},
    "gpt-5.4": {"input_per_mtok": 2.50, "cached_input_per_mtok": 0.25, "output_per_mtok": 15.00},
    "gpt-5.4-mini": {"input_per_mtok": 0.75, "cached_input_per_mtok": 0.075, "output_per_mtok": 4.50},
    "gpt-5.4-nano": {"input_per_mtok": 0.15, "cached_input_per_mtok": 0.015, "output_per_mtok": 0.60},
    "gpt-5.3-codex": {"input_per_mtok": None, "cached_input_per_mtok": None, "output_per_mtok": None},
    "gpt-4.1": {"input_per_mtok": None, "cached_input_per_mtok": None, "output_per_mtok": None},
    "gpt-4o": {"input_per_mtok": None, "cached_input_per_mtok": None, "output_per_mtok": None},
    "gpt-4o-mini": {"input_per_mtok": None, "cached_input_per_mtok": None, "output_per_mtok": None},
}


ANTHROPIC_PRICES = {
    "claude-opus-4.8": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-opus-4.7": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-opus-4.6": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-sonnet-4.6": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-haiku-4.5": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-").lower()[:100] or "item"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def add_repair(surface: str, failure: str, next_action: str) -> dict[str, Any]:
    if REPAIR_QUEUE.exists():
        key = (surface, failure[:2000], next_action)
        for raw in REPAIR_QUEUE.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                existing = json.loads(raw)
            except Exception:
                continue
            if (
                existing.get("status") == "open"
                and existing.get("surface") == key[0]
                and existing.get("failure") == key[1]
                and existing.get("next_action") == key[2]
            ):
                return existing
    entry = {
        "repair_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{slug(surface)}",
        "surface": surface,
        "failure": failure[:2000],
        "next_action": next_action,
        "status": "open",
        "created_at": utc_now(),
    }
    REPAIR_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with REPAIR_QUEUE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def run_command(command: list[str], timeout: int = 30, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": 124, "stdout": exc.stdout or "", "stderr": f"timeout after {timeout}s"}
    except FileNotFoundError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}


def desktop_hermes_env() -> dict[str, str]:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(DESKTOP_HERMES_HOME)
    return env


def load_env_files() -> dict[str, str]:
    env = dict(os.environ)
    for path in (ROOT / ".env", Path.home() / ".hermes" / ".env", DESKTOP_HERMES_HOME / ".env"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    return env


def connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def load_live_operation_quality_scores(db_path: Path | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    path = db_path or DB_PATH
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT worker_id, operation_domain, COUNT(*) AS sample_count,
                   AVG(composite_score) AS composite_score,
                   MAX(created_at) AS latest_created_at
            FROM operation_quality_scores
            WHERE proof_kind = 'live'
            GROUP BY worker_id, operation_domain
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        worker_id = str(row["worker_id"] or "")
        domain = str(row["operation_domain"] or "")
        if not worker_id or not domain:
            continue
        grouped.setdefault(worker_id, {})[domain] = {
            "worker_id": worker_id,
            "operation_domain": domain,
            "sample_count": int(row["sample_count"] or 0),
            "composite_score": round(float(row["composite_score"] or 0.0), 4),
            "latest_created_at": row["latest_created_at"],
        }
    return grouped


def migrate_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS worker_cards (
            worker_id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL,
            base_model TEXT NOT NULL,
            surface TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            contract_type TEXT NOT NULL,
            salary_bucket TEXT NOT NULL,
            overtime_rule TEXT NOT NULL,
            budget_source_id TEXT,
            hardware_fit TEXT NOT NULL,
            context_window INTEGER,
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            modalities_json TEXT NOT NULL,
            tools_json TEXT NOT NULL,
            stats_json TEXT NOT NULL,
            best_jobs_json TEXT NOT NULL,
            avoid_jobs_json TEXT NOT NULL,
            badges_json TEXT NOT NULL,
            approval_required INTEGER NOT NULL,
            marginal_cost_json TEXT NOT NULL,
            source_evidence_json TEXT NOT NULL,
            dynamic_state_json TEXT NOT NULL,
            last_verified TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_classes (
            job_class TEXT PRIMARY KEY,
            required_capabilities_json TEXT NOT NULL,
            preferred_stats_json TEXT NOT NULL,
            local_first INTEGER NOT NULL,
            approval_floor TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budget_sources (
            source_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            method TEXT NOT NULL,
            endpoint TEXT,
            refresh_status TEXT NOT NULL,
            remaining REAL,
            entitlement REAL,
            percent_remaining REAL,
            reset_at TEXT,
            current_cost_usd REAL,
            current_usage_json TEXT NOT NULL,
            last_receipt_path TEXT,
            last_verified TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approval_rules (
            rule_id TEXT PRIMARY KEY,
            contract_type TEXT NOT NULL,
            provider_id TEXT,
            worker_id TEXT,
            approval_required INTEGER NOT NULL,
            condition TEXT NOT NULL,
            action TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            receipt_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hermes_config_state (
            state_id TEXT PRIMARY KEY,
            cli_config_path TEXT NOT NULL,
            desktop_config_path TEXT NOT NULL,
            cli_model_json TEXT NOT NULL,
            desktop_model_json TEXT NOT NULL,
            resolution_action TEXT NOT NULL,
            receipt_path TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    existing_cols = {row["name"] for row in con.execute("PRAGMA table_info(worker_cards)").fetchall()}
    if "capabilities_json" not in existing_cols:
        con.execute("ALTER TABLE worker_cards ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '[]'")
    job_class_cols = {row["name"] for row in con.execute("PRAGMA table_info(job_classes)").fetchall()}
    if "updated_at" not in job_class_cols:
        con.execute("ALTER TABLE job_classes ADD COLUMN updated_at TEXT")
    con.commit()


def upsert_budget_source(con: sqlite3.Connection, row: dict[str, Any]) -> None:
    now = utc_now()
    con.execute(
        """
        INSERT INTO budget_sources(source_id,provider_id,method,endpoint,refresh_status,remaining,entitlement,
          percent_remaining,reset_at,current_cost_usd,current_usage_json,last_receipt_path,last_verified,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id) DO UPDATE SET
          provider_id=excluded.provider_id,
          method=excluded.method,
          endpoint=excluded.endpoint,
          refresh_status=excluded.refresh_status,
          remaining=excluded.remaining,
          entitlement=excluded.entitlement,
          percent_remaining=excluded.percent_remaining,
          reset_at=excluded.reset_at,
          current_cost_usd=excluded.current_cost_usd,
          current_usage_json=excluded.current_usage_json,
          last_receipt_path=excluded.last_receipt_path,
          last_verified=excluded.last_verified,
          updated_at=excluded.updated_at
        """,
        (
            row["source_id"],
            row["provider_id"],
            row["method"],
            row.get("endpoint"),
            row["refresh_status"],
            row.get("remaining"),
            row.get("entitlement"),
            row.get("percent_remaining"),
            row.get("reset_at"),
            row.get("current_cost_usd"),
            json.dumps(row.get("current_usage", {}), sort_keys=True),
            row.get("last_receipt_path"),
            row.get("last_verified", now),
            now,
        ),
    )
    con.commit()


def write_usage_snapshot(con: sqlite3.Connection, provider_id: str, source_id: str, metrics: dict[str, Any], receipt_path: Path) -> None:
    created = utc_now()
    snapshot_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{slug(provider_id)}"
    con.execute(
        "INSERT INTO usage_snapshots(snapshot_id,provider_id,source_id,metrics_json,receipt_path,created_at) VALUES(?,?,?,?,?,?)",
        (snapshot_id, provider_id, source_id, json.dumps(metrics, sort_keys=True), str(receipt_path), created),
    )
    con.commit()


def url_json(url: str, headers: dict[str, str], timeout: int = 30) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"error": body[:1000]}
        return exc.code, parsed
    except Exception as exc:
        return 0, {"error": str(exc)}


def probe_copilot(con: sqlite3.Connection) -> dict[str, Any]:
    source_id = "github-copilot-premium-interactions"
    result = run_command(["gh", "api", "/copilot_internal/user"], timeout=30)
    receipt = RECEIPTS / f"budget-copilot-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if result["returncode"] != 0:
        data = {"ok": False, "surface": "github-copilot", "error": result["stderr"] or result["stdout"], "created_at": utc_now()}
        write_json(receipt, data)
        add_repair("github-copilot", data["error"], "rerun gh auth status and gh api /copilot_internal/user")
        upsert_budget_source(
            con,
            {
                "source_id": source_id,
                "provider_id": "github-copilot",
                "method": "gh api",
                "endpoint": "/copilot_internal/user",
                "refresh_status": "failed",
                "current_usage": {"error": "command_failed"},
                "last_receipt_path": str(receipt),
                "last_verified": utc_now(),
            },
        )
        return data
    payload = json.loads(result["stdout"])
    premium = payload.get("quota_snapshots", {}).get("premium_interactions", {})
    data = {
        "ok": True,
        "surface": "github-copilot",
        "plan": payload.get("copilot_plan"),
        "remaining": premium.get("remaining"),
        "entitlement": premium.get("entitlement"),
        "percent_remaining": premium.get("percent_remaining"),
        "reset_at": payload.get("quota_reset_date_utc"),
        "created_at": utc_now(),
    }
    write_json(receipt, data)
    upsert_budget_source(
        con,
        {
            "source_id": source_id,
            "provider_id": "github-copilot",
            "method": "gh api",
            "endpoint": "/copilot_internal/user",
            "refresh_status": "ok",
            "remaining": premium.get("remaining"),
            "entitlement": premium.get("entitlement"),
            "percent_remaining": premium.get("percent_remaining"),
            "reset_at": payload.get("quota_reset_date_utc"),
            "current_usage": {"plan": payload.get("copilot_plan")},
            "last_receipt_path": str(receipt),
            "last_verified": utc_now(),
        },
    )
    write_usage_snapshot(con, "github-copilot", source_id, data, receipt)
    return data


def probe_openai(con: sqlite3.Connection, env: dict[str, str]) -> dict[str, Any]:
    source_id = "openai-api-costs"
    key = env.get("OPENAI_ADMIN_KEY") or env.get("OPENAI_API_KEY")
    start = int(datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    end = int(datetime.now(timezone.utc).timestamp())
    endpoint = f"https://api.openai.com/v1/organization/costs?{urllib.parse.urlencode({'start_time': start, 'end_time': end, 'bucket_width': '1d', 'limit': 31})}"
    receipt = RECEIPTS / f"budget-openai-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if not key:
        data = {"ok": False, "surface": "openai-api", "status": "unproved", "error": "OPENAI_ADMIN_KEY or OPENAI_API_KEY not found", "created_at": utc_now()}
        write_json(receipt, data)
        add_repair("openai-api", data["error"], "set an admin-capable OpenAI key in governor env and rerun build-worker-roster-v2.py")
        status = "unproved"
        current = {"error": "missing_key"}
        cost = None
    else:
        status_code, payload = url_json(endpoint, {"Authorization": f"Bearer {key}", "OpenAI-Beta": "admin=v1"})
        data = {"ok": status_code == 200, "surface": "openai-api", "http_status": status_code, "created_at": utc_now()}
        if status_code == 200:
            total = extract_openai_cost(payload)
            data.update({"month_to_date_cost_usd": total, "bucket_count": len(payload.get("data", []))})
            current = {"month_to_date_cost_usd": total, "bucket_count": len(payload.get("data", []))}
            cost = total
            status = "ok"
        else:
            safe_error = payload.get("error", payload)
            data["error"] = safe_error
            current = {"error": safe_error}
            cost = None
            status = "failed"
            add_repair("openai-api", f"cost endpoint returned HTTP {status_code}", "verify OpenAI admin key organization access and rerun")
        write_json(receipt, data)
    upsert_budget_source(
        con,
        {
            "source_id": source_id,
            "provider_id": "openai-api",
            "method": "https",
            "endpoint": "GET /v1/organization/costs",
            "refresh_status": status,
            "current_cost_usd": cost,
            "current_usage": current,
            "last_receipt_path": str(receipt),
            "last_verified": utc_now(),
        },
    )
    write_usage_snapshot(con, "openai-api", source_id, data, receipt)
    return data


def extract_openai_cost(payload: dict[str, Any]) -> float:
    total = 0.0
    for bucket in payload.get("data", []):
        for result in bucket.get("results", []):
            amount = result.get("amount")
            if isinstance(amount, dict):
                value = amount.get("value")
            else:
                value = result.get("amount")
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue
    return round(total, 6)


def probe_anthropic(con: sqlite3.Connection, env: dict[str, str]) -> dict[str, Any]:
    source_id = "anthropic-api-cost-report"
    key = env.get("ANTHROPIC_ADMIN_KEY") or env.get("ANTHROPIC_ADMIN_API_KEY") or env.get("ANTHROPIC_API_KEY")
    start_dt = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_dt = datetime.now(timezone.utc)
    params = urllib.parse.urlencode(
        {
            "starting_at": start_dt.isoformat().replace("+00:00", "Z"),
            "ending_at": end_dt.isoformat().replace("+00:00", "Z"),
            "bucket_width": "1d",
            "group_by[]": "description",
            "limit": 31,
        }
    )
    endpoint = f"https://api.anthropic.com/v1/organizations/cost_report?{params}"
    receipt = RECEIPTS / f"budget-anthropic-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if not key:
        data = {"ok": False, "surface": "anthropic-api", "status": "unproved", "error": "ANTHROPIC_ADMIN_KEY not found", "created_at": utc_now()}
        write_json(receipt, data)
        add_repair("anthropic-api", data["error"], "set Anthropic Admin API key or export Console cost CSV and rerun")
        status = "unproved"
        current = {"error": "missing_key"}
        cost = None
    else:
        status_code, payload = url_json(endpoint, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        data = {"ok": status_code == 200, "surface": "anthropic-api", "http_status": status_code, "created_at": utc_now()}
        if status_code == 200:
            total = extract_anthropic_cost(payload)
            data.update({"month_to_date_cost_usd": total, "bucket_count": len(payload.get("data", []))})
            current = {"month_to_date_cost_usd": total, "bucket_count": len(payload.get("data", []))}
            cost = total
            status = "ok"
        else:
            safe_error = payload.get("error", payload)
            data["error"] = safe_error
            current = {"error": safe_error}
            cost = None
            status = "failed"
            add_repair("anthropic-api", f"cost endpoint returned HTTP {status_code}", "verify Anthropic Admin API access; individual accounts may require Console export")
        write_json(receipt, data)
    upsert_budget_source(
        con,
        {
            "source_id": source_id,
            "provider_id": "anthropic-api",
            "method": "https",
            "endpoint": "GET /v1/organizations/cost_report",
            "refresh_status": status,
            "current_cost_usd": cost,
            "current_usage": current,
            "last_receipt_path": str(receipt),
            "last_verified": utc_now(),
        },
    )
    write_usage_snapshot(con, "anthropic-api", source_id, data, receipt)
    return data


def extract_anthropic_cost(payload: dict[str, Any]) -> float:
    cents = 0.0
    for bucket in payload.get("data", []):
        for result in bucket.get("results", []):
            try:
                cents += float(result.get("amount", 0))
            except (TypeError, ValueError):
                continue
    return round(cents / 100.0, 6)


def probe_ollama_cloud(con: sqlite3.Connection) -> dict[str, Any]:
    source_id = "ollama-cloud-usage-model"
    models = read_json(OLLAMA_CLOUD_CACHE, {}).get("models", [])
    receipt = RECEIPTS / f"budget-ollama-cloud-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    data = {
        "ok": bool(models),
        "surface": "ollama-cloud",
        "status": "usage_metrics_only" if models else "unproved",
        "model_count": len(models),
        "usage_unit": "provider GPU-time / plan pressure, not fixed token allowance",
        "remaining_budget_state": "not_exposed_by_local_api",
        "created_at": utc_now(),
    }
    if not models:
        add_repair("ollama-cloud", "ollama cloud model cache missing or empty", "open Ollama account usage page or refresh cloud cache from authenticated Ollama client")
    write_json(receipt, data)
    upsert_budget_source(
        con,
        {
            "source_id": source_id,
            "provider_id": "ollama-cloud",
            "method": "local cache plus per-response metrics",
            "endpoint": str(OLLAMA_CLOUD_CACHE),
            "refresh_status": "ok" if models else "unproved",
            "current_usage": data,
            "last_receipt_path": str(receipt),
            "last_verified": utc_now(),
        },
    )
    write_usage_snapshot(con, "ollama-cloud", source_id, data, receipt)
    return data


def probe_hermes_nous(con: sqlite3.Connection) -> dict[str, Any]:
    source_id = "hermes-nous-portal-status"
    result = run_command(["hermes", "portal", "status"], timeout=30, env=desktop_hermes_env())
    stdout = result["stdout"]
    logged_in = "Auth:" in stdout and "✓ logged in" in stdout
    receipt = RECEIPTS / f"budget-hermes-nous-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    data = {
        "ok": result["returncode"] == 0,
        "surface": "hermes-nous",
        "nous_logged_in": logged_in,
        "remaining_budget_state": "unproved" if not logged_in else "portal_login_present_budget_probe_needed",
        "created_at": utc_now(),
    }
    if result["returncode"] != 0:
        data["error"] = result["stderr"] or result["stdout"]
        add_repair("hermes", data["error"], "repair Hermes Desktop HERMES_HOME portal status and rerun roster build")
    elif not logged_in:
        add_repair("hermes-nous", "Nous Portal is not logged in", "run hermes auth add nous --type oauth, then rerun roster build")
    write_json(receipt, data)
    upsert_budget_source(
        con,
        {
            "source_id": source_id,
            "provider_id": "nous",
            "method": "hermes portal status with Desktop HERMES_HOME",
            "endpoint": "hermes portal status",
            "refresh_status": "ok" if logged_in else "unproved",
            "current_usage": data,
            "last_receipt_path": str(receipt),
            "last_verified": utc_now(),
        },
    )
    write_usage_snapshot(con, "nous", source_id, data, receipt)
    return data


def model_block_from_yaml(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    out: dict[str, Any] = {}
    in_model = False
    for line in lines:
        line = line.lstrip("\ufeff")
        if line.startswith("model:"):
            in_model = True
            continue
        if in_model and line and not line.startswith((" ", "\t")):
            break
        if in_model and ":" in line:
            key, value = line.strip().split(":", 1)
            value = value.strip().strip('"').strip("'")
            out[key] = value
    return out


def replace_model_block(original: str, new_block: str) -> str:
    pattern = re.compile(r"(?ms)^\ufeff?model:\n(?:^[ \t].*\n?)*")
    if pattern.search(original):
        return pattern.sub(new_block.rstrip() + "\n", original, count=1)
    return new_block.rstrip() + "\n" + original


def resolve_hermes_split(con: sqlite3.Connection, apply: bool) -> dict[str, Any]:
    receipt = RECEIPTS / f"hermes-config-resolution-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    cli_text = CLI_HERMES_CONFIG.read_text(encoding="utf-8", errors="replace") if CLI_HERMES_CONFIG.exists() else ""
    desktop_text = DESKTOP_HERMES_CONFIG.read_text(encoding="utf-8", errors="replace") if DESKTOP_HERMES_CONFIG.exists() else ""
    cli_model = model_block_from_yaml(cli_text)
    desktop_model = model_block_from_yaml(desktop_text)
    same = {
        "provider": cli_model.get("provider") == desktop_model.get("provider"),
        "default": cli_model.get("default") == desktop_model.get("default"),
        "base_url": cli_model.get("base_url") == desktop_model.get("base_url"),
    }
    action = "already_aligned" if all(same.values()) else "desktop_cli_split_preserved_desktop_is_runtime_authority"
    backup = None
    desktop_provider = (desktop_model.get("provider") or "").strip().lower()
    desktop_default = (desktop_model.get("default") or desktop_model.get("name") or "").strip()
    if apply and desktop_provider == "nous" and desktop_default not in NOUS_PROVED_FREE_MODELS:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = DESKTOP_HERMES_CONFIG.with_suffix(f".config.yaml.backup-{stamp}")
        shutil.copy2(DESKTOP_HERMES_CONFIG, backup)
        free_block = "\n".join(
            [
                "model:",
                "  provider: nous",
                f"  name: {NOUS_FREE_DEFAULT}",
                f"  default: {NOUS_FREE_DEFAULT}",
                "  base_url: ''",
                "  api_key: ''",
                "  api_mode: chat_completions",
                "  max_tokens: 128",
                "  ollama_num_ctx: 65536",
            ]
        )
        DESKTOP_HERMES_CONFIG.write_text(replace_model_block(desktop_text, free_block), encoding="utf-8")
        desktop_model = model_block_from_yaml(DESKTOP_HERMES_CONFIG.read_text(encoding="utf-8", errors="replace"))
        action = "desktop_nous_default_replaced_with_proved_free_model"
    elif not cli_model or not desktop_text:
        action = "blocked_missing_config"
        add_repair("hermes-config", "CLI or Desktop Hermes config missing", "verify Hermes installation paths and rerun roster build")
    data = {
        "ok": action != "blocked_missing_config",
        "cli_config": str(CLI_HERMES_CONFIG),
        "desktop_config": str(DESKTOP_HERMES_CONFIG),
        "cli_model": cli_model,
        "desktop_model": desktop_model,
        "initial_match": same,
        "resolution_action": action,
        "backup_path": str(backup) if backup else None,
        "created_at": utc_now(),
    }
    write_json(receipt, data)
    con.execute(
        """
        INSERT INTO hermes_config_state(state_id,cli_config_path,desktop_config_path,cli_model_json,desktop_model_json,
          resolution_action,receipt_path,updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(state_id) DO UPDATE SET
          cli_config_path=excluded.cli_config_path,
          desktop_config_path=excluded.desktop_config_path,
          cli_model_json=excluded.cli_model_json,
          desktop_model_json=excluded.desktop_model_json,
          resolution_action=excluded.resolution_action,
          receipt_path=excluded.receipt_path,
          updated_at=excluded.updated_at
        """,
        (
            "default",
            str(CLI_HERMES_CONFIG),
            str(DESKTOP_HERMES_CONFIG),
            json.dumps(cli_model, sort_keys=True),
            json.dumps(desktop_model, sort_keys=True),
            action,
            str(receipt),
            utc_now(),
        ),
    )
    con.commit()
    return data


def normalize_model_name(model_id: str) -> str:
    name = model_id.split("@", 1)[0]
    name = name.replace(":cloud", "").replace(":latest", "")
    name = name.replace("anthropic/", "").replace("openai/", "").replace("google/", "")
    name = name.replace("-20250514", "").replace("-20250929", "").replace("-20251001", "").replace("-20250805", "")
    return name


def infer_modalities(capabilities: list[str]) -> list[str]:
    modalities = ["text"]
    if "vision" in capabilities or "ocr" in capabilities:
        modalities.append("image")
    if "audio" in capabilities:
        modalities.append("audio")
    return modalities


def infer_tools(capabilities: list[str], runtime: str, provider_id: str) -> list[str]:
    tools = ["chat"]
    if "tools" in capabilities or provider_id in {"codex-chatgpt", "github-copilot"}:
        tools.extend(["tools", "agent"])
    if "coding" in capabilities:
        tools.append("edit")
    if runtime == "ollama":
        tools.append("openai_compatible_api")
    return sorted(set(tools))


def infer_stats(model: dict[str, Any], contract: str) -> dict[str, int]:
    caps = set(model.get("capabilities", []))
    fit = set(model.get("task_fit", []))
    model_id = model.get("model_id", "")
    latency = model.get("latency", "medium")
    speed_map = {"very_fast": 10, "fast": 8, "medium": 6, "slow": 4}
    stats = {
        "coding": 3,
        "agentic_loop": 2,
        "debugging": 3,
        "architecture": 3,
        "speed": speed_map.get(latency, 5),
        "structured_output": 5,
        "long_context": 4,
        "public_writing_fit": 4,
        "cost_pressure": 10 if contract == "local_resource" else 6 if contract.startswith("subscription") else 1,
        "stability": 6,
    }
    if "coding" in caps or "code_review" in caps:
        stats["coding"] = 8
        stats["debugging"] = 7
    if "agentic" in caps:
        stats["agentic_loop"] = 8
    if "reasoning" in caps or "thinking" in caps:
        stats["architecture"] = max(stats["architecture"], 7)
        stats["debugging"] = max(stats["debugging"], 7)
    if "vision" in caps or "ocr" in caps:
        stats["structured_output"] = max(stats["structured_output"], 7)
    if "classification" in caps or "routing" in caps:
        stats["structured_output"] = max(stats["structured_output"], 7)
    if any(x in fit for x in ("drafting", "general")):
        stats["public_writing_fit"] = max(stats["public_writing_fit"], 6)
    ctx = int(model.get("context_tokens") or 0)
    if ctx >= 200000:
        stats["long_context"] = 9
    elif ctx >= 100000:
        stats["long_context"] = 7
    if "gpt-5.3-codex" in model_id:
        stats.update({"coding": 10, "agentic_loop": 10, "debugging": 9, "architecture": 8, "stability": 8, "speed": 7})
    elif "sonnet" in model_id:
        stats.update({"coding": 9, "agentic_loop": 8, "debugging": 8, "architecture": 8, "stability": 8, "speed": max(stats["speed"], 6)})
    elif "opus" in model_id or "gpt-5.5-pro" in model_id:
        stats.update({"coding": 7, "agentic_loop": 5, "architecture": 10, "debugging": 9, "stability": 8, "speed": min(stats["speed"], 5), "cost_pressure": min(stats["cost_pressure"], 3)})
    elif any(x in model_id for x in ("gpt-5.5", "gpt-5.4")):
        stats.update({"architecture": max(stats["architecture"], 8), "debugging": max(stats["debugging"], 8), "stability": 8})
    elif any(x in model_id for x in ("haiku", "mini", "nano", "flash")):
        stats["speed"] = max(stats["speed"], 8)
        stats["cost_pressure"] = max(stats["cost_pressure"], 7 if contract != "metered_extra_cost" else 4)
    return stats


def infer_jobs(model: dict[str, Any], stats: dict[str, int], contract: str) -> tuple[list[str], list[str]]:
    caps = set(model.get("capabilities", []))
    fit = set(model.get("task_fit", []))
    best: list[str] = []
    avoid: list[str] = []
    if "ocr" in caps:
        best.append("ocr_document_intake")
    if "embedding" in caps:
        best.extend(["bulk_extraction", "schema_validation"])
    if "classification" in caps or "routing" in caps:
        best.append("routing_triage")
    if "coding" in caps:
        best.extend(["simple_coding", "repo_coding"])
    if "agentic" in caps:
        best.append("agentic_repair")
    if "reasoning" in caps:
        best.extend(["deep_debugging", "architecture"])
    if "vision" in caps:
        best.append("visual_reasoning")
    if "drafting" in caps or "drafting" in fit:
        best.append("public_content")
    if not best:
        best = list(fit) or ["routing_triage"]
    if contract in {"metered_extra_cost", "third_party_metered"}:
        avoid.extend(["bulk_extraction", "routing_triage", "schema_validation"])
    if contract == "local_resource" and stats["speed"] < 6:
        avoid.extend(["deep_debugging", "architecture", "agentic_repair"])
    if "embedding" in caps:
        avoid.extend(["public_content", "architecture"])
    return sorted(set(best)), sorted(set(avoid))


def badges_for(model: dict[str, Any], contract: str, stats: dict[str, int], approval_required: bool) -> list[str]:
    badges = []
    if contract == "local_resource":
        badges.extend(["EMPLOYEE", "LOCAL_SAFE"])
    elif contract.startswith("subscription"):
        badges.append("EMPLOYEE")
    else:
        badges.append("OVERTIME")
    if approval_required:
        badges.append("APPROVAL")
    if stats.get("speed", 0) >= 8:
        badges.append("FAST")
    if stats.get("coding", 0) >= 7:
        badges.append("CODING")
    if stats.get("long_context", 0) >= 8:
        badges.append("LONG_CTX")
    if "vision" in model.get("capabilities", []) or "ocr" in model.get("capabilities", []):
        badges.append("VISION")
    return sorted(set(badges))


def marginal_cost_for(model_id: str, provider_id: str, contract: str) -> dict[str, Any]:
    base = normalize_model_name(model_id)
    if provider_id == "openai-api":
        prices = OPENAI_PRICES.get(base, {})
        return {"unit": "usd_per_mtok", **prices}
    if provider_id == "anthropic-api":
        prices = ANTHROPIC_PRICES.get(base, {})
        return {"unit": "usd_per_mtok", **prices}
    if provider_id == "ollama-cloud":
        return {"unit": "provider_gpu_time_or_usage_level", "input_per_mtok": None, "output_per_mtok": None}
    if contract == "local_resource":
        return {"unit": "local_hardware_time", "input_per_mtok": 0, "output_per_mtok": 0}
    return {"unit": "provider_specific", "input_per_mtok": None, "output_per_mtok": None}


def worker_from_model(model: dict[str, Any], budget_states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    provider_id = model.get("provider_id", "unknown")
    contract = PROVIDER_CONTRACTS.get(provider_id, "unknown_cost")
    contract_info = CONTRACTS[contract]
    capabilities = list(model.get("capabilities", []))
    stats = infer_stats(model, contract)
    best, avoid = infer_jobs(model, stats, contract)
    approval_required = bool(contract_info["approval_required"])
    worker_id = f"{model['model_id']}@{provider_id}"
    budget_source_id = provider_budget_source(provider_id)
    dynamic = budget_states.get(provider_id, {"remaining_budget_state": "unproved"})
    return {
        "worker_id": worker_id,
        "model_id": model["model_id"],
        "base_model": normalize_model_name(model["model_id"]),
        "surface": provider_id,
        "provider": provider_id,
        "provider_id": provider_id,
        "contract_type": contract,
        "salary_bucket": contract_info["salary_bucket"],
        "overtime_rule": contract_info["overtime_rule"],
        "budget_source_id": budget_source_id,
        "marginal_cost": marginal_cost_for(model["model_id"], provider_id, contract),
        "remaining_budget_source": budget_source_id,
        "hardware_fit": "local_limited_vram" if contract == "local_resource" else "cloud_only",
        "context_window": int(model.get("context_tokens") or 0),
        "modalities": infer_modalities(capabilities),
        "capabilities": sorted(set(capabilities)),
        "tools": infer_tools(capabilities, model.get("runtime", ""), provider_id),
        "stats": stats,
        "best_jobs": best,
        "avoid_jobs": avoid,
        "badges": badges_for(model, contract, stats, approval_required),
        "approval_required": approval_required,
        "source_evidence": ["local_roster", "local_probe"],
        "dynamic_state": dynamic,
        "last_verified": utc_now(),
    }


def provider_budget_source(provider_id: str) -> str:
    return {
        "github-copilot": "github-copilot-premium-interactions",
        "openai-api": "openai-api-costs",
        "anthropic-api": "anthropic-api-cost-report",
        "ollama-cloud": "ollama-cloud-usage-model",
        "nous": "hermes-nous-portal-status",
        "claude-max": "claude-cli-account-status",
        "codex-chatgpt": "codex-login-status",
        "ollama-local": "local-hardware",
    }.get(provider_id, f"{provider_id}-budget")


def synthetic_models_from_provider_cache() -> list[dict[str, Any]]:
    cache = read_json(PROVIDER_MODELS_CACHE, {})
    models: list[dict[str, Any]] = []
    for mid in cache.get("copilot", {}).get("models", []):
        models.append(
            {
                "model_id": mid,
                "provider_id": "github-copilot",
                "runtime": "copilot",
                "context_tokens": 1000000 if "gpt-5.5" in mid or "sonnet" in mid or "opus" in mid else 400000,
                "capabilities": ["coding", "code_review", "reasoning", "agentic", "tools"],
                "task_fit": ["coding", "code_review", "agentic_repair", "architecture"],
                "latency": "fast" if any(x in mid for x in ("mini", "haiku")) else "medium",
                "privacy_level": "account_cloud",
                "quota_burn": "premium_interaction",
                "failure_risk": "quota_sensitive",
            }
        )
    for mid in cache.get("openai-api", {}).get("models", []):
        models.append(
            {
                "model_id": mid,
                "provider_id": "openai-api",
                "runtime": "openai-api",
                "context_tokens": 1050000 if "gpt-5.5" in mid or "gpt-5.4" in mid else 400000,
                "capabilities": ["reasoning", "coding", "vision", "tools", "structured_output", "agentic"],
                "task_fit": ["architecture", "deep_debugging", "agentic_repair", "visual_reasoning"],
                "latency": "fast" if "mini" in mid or "nano" in mid else "medium",
                "privacy_level": "api_cloud",
                "quota_burn": "metered_tokens",
                "failure_risk": "paid_api",
            }
        )
    for mid in cache.get("anthropic", {}).get("models", []):
        normalized = normalize_model_name(mid)
        models.append(
            {
                "model_id": normalized,
                "provider_id": "anthropic-api",
                "runtime": "anthropic-api",
                "context_tokens": 1000000 if "sonnet" in normalized or "opus" in normalized else 200000,
                "capabilities": ["reasoning", "coding", "vision", "tools", "structured_output"],
                "task_fit": ["architecture", "deep_debugging", "code_review"],
                "latency": "fast" if "haiku" in normalized else "medium",
                "privacy_level": "api_cloud",
                "quota_burn": "metered_tokens",
                "failure_risk": "paid_api",
            }
        )
    cloud_models = read_json(OLLAMA_CLOUD_CACHE, {}).get("models", [])
    for mid in cloud_models:
        caps = ["reasoning", "tools", "structured_output"]
        fit = ["architecture", "reasoning"]
        if "coder" in mid:
            caps.extend(["coding", "code_review", "agentic"])
            fit.extend(["coding", "code_review"])
        if "vl" in mid or "gemini" in mid:
            caps.append("vision")
            fit.append("visual_reasoning")
        models.append(
            {
                "model_id": f"{mid}:cloud",
                "provider_id": "ollama-cloud",
                "runtime": "ollama",
                "context_tokens": 262144 if any(x in mid for x in ("qwen", "glm", "kimi", "minimax")) else 131072,
                "capabilities": sorted(set(caps)),
                "task_fit": sorted(set(fit)),
                "latency": "medium",
                "privacy_level": "account_cloud",
                "quota_burn": "cloud_usage",
                "failure_risk": "quota_sensitive",
            }
        )
    return models


def build_worker_roster(con: sqlite3.Connection) -> dict[str, Any]:
    seed = read_json(SOURCE_ROSTER, {}).get("models", [])
    all_models = {f"{m.get('model_id')}@{m.get('provider_id')}": m for m in seed}
    for model in synthetic_models_from_provider_cache():
        all_models[f"{model.get('model_id')}@{model.get('provider_id')}"] = model
    budget_states = {}
    for row in con.execute("SELECT provider_id, refresh_status, remaining, entitlement, percent_remaining, reset_at, current_cost_usd, current_usage_json, last_receipt_path, last_verified FROM budget_sources"):
        budget_states[row["provider_id"]] = {
            "refresh_status": row["refresh_status"],
            "remaining": row["remaining"],
            "entitlement": row["entitlement"],
            "percent_remaining": row["percent_remaining"],
            "reset_at": row["reset_at"],
            "current_cost_usd": row["current_cost_usd"],
            "current_usage": json.loads(row["current_usage_json"]),
            "last_receipt_path": row["last_receipt_path"],
            "last_verified": row["last_verified"],
        }
    budget_states.setdefault("ollama-local", {"refresh_status": "ok", "remaining_budget_state": "local_hardware_only", "last_verified": utc_now()})
    workers = [worker_from_model(model, budget_states) for model in all_models.values()]
    workers = sorted(workers, key=lambda w: (w["contract_type"], w["surface"], w["worker_id"]))
    data = {
        "version": 2,
        "updated_at": utc_now(),
        "source": {
            "local_roster": str(SOURCE_ROSTER),
            "provider_models_cache": str(PROVIDER_MODELS_CACHE),
            "ollama_cloud_cache": str(OLLAMA_CLOUD_CACHE),
        },
        "job_classes": JOB_CLASSES,
        "contracts": CONTRACTS,
        "workers": workers,
    }
    write_json(ROSTER_PATH, data)
    for worker in workers:
        con.execute(
            """
            INSERT INTO worker_cards(worker_id,model_id,base_model,surface,provider_id,contract_type,salary_bucket,overtime_rule,
              budget_source_id,hardware_fit,context_window,capabilities_json,modalities_json,tools_json,stats_json,best_jobs_json,avoid_jobs_json,
              badges_json,approval_required,marginal_cost_json,source_evidence_json,dynamic_state_json,last_verified,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(worker_id) DO UPDATE SET
              model_id=excluded.model_id,
              base_model=excluded.base_model,
              surface=excluded.surface,
              provider_id=excluded.provider_id,
              contract_type=excluded.contract_type,
              salary_bucket=excluded.salary_bucket,
              overtime_rule=excluded.overtime_rule,
              budget_source_id=excluded.budget_source_id,
              hardware_fit=excluded.hardware_fit,
              context_window=excluded.context_window,
              capabilities_json=excluded.capabilities_json,
              modalities_json=excluded.modalities_json,
              tools_json=excluded.tools_json,
              stats_json=excluded.stats_json,
              best_jobs_json=excluded.best_jobs_json,
              avoid_jobs_json=excluded.avoid_jobs_json,
              badges_json=excluded.badges_json,
              approval_required=excluded.approval_required,
              marginal_cost_json=excluded.marginal_cost_json,
              source_evidence_json=excluded.source_evidence_json,
              dynamic_state_json=excluded.dynamic_state_json,
              last_verified=excluded.last_verified,
              updated_at=excluded.updated_at
            """,
            (
                worker["worker_id"],
                worker["model_id"],
                worker["base_model"],
                worker["surface"],
                worker["provider_id"],
                worker["contract_type"],
                worker["salary_bucket"],
                worker["overtime_rule"],
                worker["budget_source_id"],
                worker["hardware_fit"],
                worker["context_window"],
                json.dumps(worker["capabilities"], sort_keys=True),
                json.dumps(worker["modalities"], sort_keys=True),
                json.dumps(worker["tools"], sort_keys=True),
                json.dumps(worker["stats"], sort_keys=True),
                json.dumps(worker["best_jobs"], sort_keys=True),
                json.dumps(worker["avoid_jobs"], sort_keys=True),
                json.dumps(worker["badges"], sort_keys=True),
                int(worker["approval_required"]),
                json.dumps(worker["marginal_cost"], sort_keys=True),
                json.dumps(worker["source_evidence"], sort_keys=True),
                json.dumps(worker["dynamic_state"], sort_keys=True),
                worker["last_verified"],
                utc_now(),
            ),
        )
    for job, spec in JOB_CLASSES.items():
        con.execute(
            """
            INSERT INTO job_classes(job_class,required_capabilities_json,preferred_stats_json,local_first,approval_floor,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(job_class) DO UPDATE SET
              required_capabilities_json=excluded.required_capabilities_json,
              preferred_stats_json=excluded.preferred_stats_json,
              local_first=excluded.local_first,
              approval_floor=excluded.approval_floor,
              updated_at=excluded.updated_at
            """,
            (
                job,
                json.dumps(spec["required_capabilities"], sort_keys=True),
                json.dumps(spec["preferred_stats"], sort_keys=True),
                int(spec["local_first"]),
                spec["approval_floor"],
                utc_now(),
            ),
        )
    for contract, spec in CONTRACTS.items():
        con.execute(
            """
            INSERT INTO approval_rules(rule_id,contract_type,provider_id,worker_id,approval_required,condition,action,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(rule_id) DO UPDATE SET
              approval_required=excluded.approval_required,
              condition=excluded.condition,
              action=excluded.action,
              updated_at=excluded.updated_at
            """,
            (
                f"contract-{contract}",
                contract,
                None,
                None,
                int(spec["approval_required"]),
                spec["overtime_rule"],
                "allow_with_receipt" if not spec["approval_required"] else "block_without_explicit_approval",
                utc_now(),
            ),
        )
    con.commit()
    return data


def generate_hermes_catalog(roster: dict[str, Any], apply: bool) -> dict[str, Any]:
    providers: dict[str, dict[str, Any]] = {}
    approved_contracts = {"local_resource", "subscription_unlimited", "subscription_quota", "subscription_usage"}
    for worker in roster["workers"]:
        contract = worker["contract_type"]
        # Keep overtime workers visible only as explicit approval rows.
        visible = contract in approved_contracts or worker["approval_required"]
        if not visible:
            continue
        provider = worker["surface"]
        providers.setdefault(
            provider,
            {
                "metadata": {
                    "display_name": provider,
                    "note": "Generated by AI Resource Governor. Badges encode contract, job fit, and approval state.",
                },
                "models": [],
            },
        )
        jobs = ", ".join(worker["best_jobs"][:3])
        badges = " ".join(f"[{b}]" for b in worker["badges"])
        if worker["approval_required"]:
            budget = "overtime blocked until approved"
        else:
            ds = worker.get("dynamic_state", {})
            pct = ds.get("percent_remaining")
            budget = f"{pct}% remaining" if pct is not None else ds.get("remaining_budget_state") or ds.get("refresh_status") or "budget checked"
        providers[provider]["models"].append(
            {
                "id": worker["model_id"],
                "description": f"{badges} {jobs}; {worker['contract_type']}; {budget}",
                "worker_id": worker["worker_id"],
            }
        )
    catalog = {
        "version": 2,
        "updated_at": utc_now(),
        "metadata": {
            "source": str(ROSTER_PATH),
            "purpose": "Compact governed Hermes model catalog with worker badges.",
        },
        "providers": providers,
    }
    write_json(HERMES_CATALOG_PATH, catalog)
    write_json(DESKTOP_GOVERNED_CATALOG, catalog)
    action = "companion_written"
    backup = None
    if apply and DESKTOP_HERMES_CACHE.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = DESKTOP_HERMES_CACHE.with_suffix(f".json.backup-{stamp}")
        shutil.copy2(DESKTOP_HERMES_CACHE, backup)
        write_json(DESKTOP_HERMES_CACHE, catalog)
        action = "desktop_cache_replaced_with_governed_catalog"
    receipt = RECEIPTS / f"hermes-governed-catalog-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(
        receipt,
        {
            "ok": True,
            "action": action,
            "catalog": str(HERMES_CATALOG_PATH),
            "desktop_companion": str(DESKTOP_GOVERNED_CATALOG),
            "desktop_cache": str(DESKTOP_HERMES_CACHE),
            "backup_path": str(backup) if backup else None,
            "provider_count": len(providers),
            "model_count": sum(len(p["models"]) for p in providers.values()),
            "created_at": utc_now(),
        },
    )
    return catalog


def validate(con: sqlite3.Connection) -> dict[str, Any]:
    tables = ["worker_cards", "job_classes", "budget_sources", "approval_rules", "usage_snapshots", "hermes_config_state"]
    counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    roster = read_json(ROSTER_PATH, {})
    catalog = read_json(HERMES_CATALOG_PATH, {})
    ok = counts["worker_cards"] > 0 and counts["job_classes"] == len(JOB_CLASSES) and bool(roster.get("workers")) and bool(catalog.get("providers"))
    receipt = RECEIPTS / f"worker-roster-v2-validation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    data = {
        "ok": ok,
        "counts": counts,
        "roster_path": str(ROSTER_PATH),
        "catalog_path": str(HERMES_CATALOG_PATH),
        "created_at": utc_now(),
    }
    write_json(receipt, data)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the AI Resource Governor v2 worker roster.")
    parser.add_argument("--no-apply-hermes", action="store_true", help="Write receipts/catalogs but do not patch Hermes Desktop config/cache.")
    args = parser.parse_args(argv)
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    con = connect()
    try:
        migrate_schema(con)
        env = load_env_files()
        probes = {
            "copilot": probe_copilot(con),
            "openai": probe_openai(con, env),
            "anthropic": probe_anthropic(con, env),
            "ollama_cloud": probe_ollama_cloud(con),
            "hermes_nous": probe_hermes_nous(con),
        }
        hermes = resolve_hermes_split(con, apply=not args.no_apply_hermes)
        roster = build_worker_roster(con)
        catalog = generate_hermes_catalog(roster, apply=not args.no_apply_hermes)
        validation = validate(con)
        final = {
            "ok": validation["ok"],
            "probes": probes,
            "hermes_config": hermes,
            "worker_count": len(roster.get("workers", [])),
            "catalog_provider_count": len(catalog.get("providers", {})),
            "validation": validation,
            "terminal_state": "built" if validation["ok"] else "continuation_required",
            "created_at": utc_now(),
        }
        final_receipt = RECEIPTS / f"worker-roster-v2-build-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        write_json(final_receipt, final)
        print(json.dumps({"receipt": str(final_receipt), **final}, indent=2, sort_keys=True))
        return 0 if validation["ok"] else 2
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
