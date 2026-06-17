#!/usr/bin/env python3
"""Local AI resource governor for model routing and quota protection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path.home() / ".ai-resource-governor"
ROSTER_SOURCE = Path.home() / "AI_PLATFORM_ROSTER_2026-06-05.md"

BILLING_RANK = {
    "local_resource": 0,
    "subscription_unlimited": 1,
    "subscription_quota": 2,
    "unknown_cost": 99,
    "metered_extra_cost": 100,
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama-local": {
        "billing_class": "local_resource",
        "allowed_by_default": True,
        "authority": "http://localhost:11434/v1",
    },
    "ollama-cloud": {
        "billing_class": "subscription_quota",
        "allowed_by_default": True,
        "authority": "ollama account-backed cloud model",
    },
    "github-copilot": {
        "billing_class": "subscription_quota",
        "allowed_by_default": True,
        "authority": "gh auth token / Copilot plan",
    },
    "codex-chatgpt": {
        "billing_class": "subscription_unlimited",
        "allowed_by_default": True,
        "authority": "Codex logged in using ChatGPT",
    },
    "claude-max": {
        "billing_class": "subscription_quota",
        "allowed_by_default": True,
        "authority": "Claude Max account route",
    },
    "gemini-oauth": {
        "billing_class": "subscription_quota",
        "allowed_by_default": True,
        "authority": "Gemini OAuth / Code Assist",
    },
    "openrouter": {
        "billing_class": "metered_extra_cost",
        "allowed_by_default": False,
        "authority": "separately billed API",
    },
    "openai-api": {
        "billing_class": "metered_extra_cost",
        "allowed_by_default": False,
        "authority": "separately billed API",
    },
    "anthropic-api": {
        "billing_class": "metered_extra_cost",
        "allowed_by_default": False,
        "authority": "separately billed API",
    },
    "unknown": {
        "billing_class": "unknown_cost",
        "allowed_by_default": False,
        "authority": "unproved",
    },
}

SEED_MODELS: list[dict[str, Any]] = [
    {
        "model_id": "glm-ocr:latest",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 131072,
        "capabilities": ["ocr", "vision", "document_intake", "tools"],
        "task_fit": ["ocr", "document_intake"],
        "latency": "fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "nomic-embed-text:latest",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 8192,
        "capabilities": ["embedding", "search"],
        "task_fit": ["embedding", "search"],
        "latency": "fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "qwen3-embedding:0.6b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 32768,
        "capabilities": ["embedding", "search", "thinking"],
        "task_fit": ["embedding", "search"],
        "latency": "fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "qwen3-embedding:4b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 40960,
        "capabilities": ["embedding", "search", "tools"],
        "task_fit": ["embedding", "search"],
        "latency": "medium",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "qwen2.5:0.5b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 32768,
        "capabilities": ["classification", "routing", "tools"],
        "task_fit": ["classification", "routing"],
        "latency": "very_fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "medium",
    },
    {
        "model_id": "qwen3.5:0.8b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 262144,
        "capabilities": ["classification", "routing", "vision", "tools", "thinking"],
        "task_fit": ["classification", "routing"],
        "latency": "very_fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "medium",
    },
    {
        "model_id": "qwen3.5:4b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 262144,
        "capabilities": ["classification", "routing", "general", "vision", "tools", "thinking"],
        "task_fit": ["classification", "routing", "general"],
        "latency": "fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "qwen3.5:9b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 262144,
        "capabilities": ["classification", "routing", "general", "drafting", "vision", "tools", "thinking"],
        "task_fit": ["general", "drafting", "classification"],
        "latency": "medium",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "qwen3:8b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 40960,
        "capabilities": ["general", "reasoning", "tools", "thinking"],
        "task_fit": ["general", "validation"],
        "latency": "medium",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "ministral-3:8b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 262144,
        "capabilities": ["general", "vision", "tools", "document_intake"],
        "task_fit": ["general", "document_intake"],
        "latency": "medium",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "qwen2.5vl:3b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 128000,
        "capabilities": ["vision", "document_intake", "ocr"],
        "task_fit": ["ocr", "document_intake"],
        "latency": "fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "gemma4:e2b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 131072,
        "capabilities": ["vision", "audio", "document_intake", "ocr", "tools", "thinking"],
        "task_fit": ["ocr", "document_intake", "general"],
        "latency": "fast",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "medium",
    },
    {
        "model_id": "gemma4:e4b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 131072,
        "capabilities": ["vision", "audio", "document_intake", "ocr", "tools", "thinking"],
        "task_fit": ["ocr", "document_intake", "general"],
        "latency": "medium",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "medium",
    },
    {
        "model_id": "qwen2.5-coder:7b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 32768,
        "capabilities": ["coding", "insert", "tools"],
        "task_fit": ["coding", "code_edit"],
        "latency": "medium",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "deepseek-coder-v2:latest",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 163840,
        "capabilities": ["coding", "code_review", "insert"],
        "task_fit": ["coding", "code_review"],
        "latency": "slow",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "deepseek-r1:8b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 131072,
        "capabilities": ["reasoning", "math", "validation", "tools", "thinking"],
        "task_fit": ["reasoning", "validation"],
        "latency": "medium",
        "privacy_level": "local",
        "quota_burn": "hardware_only",
        "failure_risk": "low",
    },
    {
        "model_id": "gpt-oss:20b",
        "provider_id": "ollama-local",
        "runtime": "ollama",
        "context_tokens": 131072,
        "capabilities": ["reasoning", "coding", "general", "tools", "thinking"],
        "task_fit": ["reasoning", "coding", "general"],
        "latency": "slow",
        "privacy_level": "local",
        "quota_burn": "hardware_heavy",
        "failure_risk": "medium",
    },
    {
        "model_id": "github-copilot",
        "provider_id": "github-copilot",
        "runtime": "copilot",
        "context_tokens": 128000,
        "capabilities": ["coding", "code_review", "reasoning", "agentic"],
        "task_fit": ["coding", "code_review", "architecture"],
        "latency": "fast",
        "privacy_level": "account_cloud",
        "quota_burn": "premium_interaction",
        "failure_risk": "quota_sensitive",
    },
    {
        "model_id": "codex-account",
        "provider_id": "codex-chatgpt",
        "runtime": "codex",
        "context_tokens": 272000,
        "capabilities": ["coding", "agentic", "reasoning", "tools"],
        "task_fit": ["coding", "architecture", "repair"],
        "latency": "medium",
        "privacy_level": "account_cloud",
        "quota_burn": "plan_limited",
        "failure_risk": "quota_sensitive",
    },
    {
        "model_id": "claude-max-sonnet-opus",
        "provider_id": "claude-max",
        "runtime": "claude",
        "context_tokens": 200000,
        "capabilities": ["architecture", "reasoning", "coding", "critique", "vision"],
        "task_fit": ["architecture", "critique", "code_review"],
        "latency": "medium",
        "privacy_level": "account_cloud",
        "quota_burn": "plan_limited",
        "failure_risk": "quota_sensitive",
    },
    {
        "model_id": "glm-5.1:cloud",
        "provider_id": "ollama-cloud",
        "runtime": "ollama",
        "context_tokens": 202752,
        "capabilities": ["reasoning", "coding", "agentic", "tools", "thinking"],
        "task_fit": ["architecture", "reasoning", "coding"],
        "latency": "slow",
        "privacy_level": "account_cloud",
        "quota_burn": "cloud_quota",
        "failure_risk": "quota_sensitive",
    },
    {
        "model_id": "qwen3-coder-next:cloud",
        "provider_id": "ollama-cloud",
        "runtime": "ollama",
        "context_tokens": 262144,
        "capabilities": ["coding", "agentic", "tools"],
        "task_fit": ["coding", "code_review"],
        "latency": "medium",
        "privacy_level": "account_cloud",
        "quota_burn": "cloud_quota",
        "failure_risk": "quota_sensitive",
    },
]

DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    "balanced_roi": True,
    "reserve_percent": 20.0,
    "no_extra_cost": True,
    "disabled_billing_classes": ["metered_extra_cost", "unknown_cost"],
    "bulk_requires_billing_class": "local_resource",
    "default_local_model": "qwen3.5:4b",
    "premium_value_reasons": [
        "architecture",
        "hard code repair",
        "high-stakes decision",
        "long-context synthesis",
        "final critique",
        "security review",
    ],
    "fallback_order": [
        "local_resource",
        "subscription_unlimited",
        "subscription_quota",
    ],
    "app_defaults": {
        "hermes_cli_model_call": "qwen2.5:0.5b",
        "openclaw_cli_model_call": "qwen3.5:4b",
    },
    "forbidden_fallback_billing_classes": ["metered_extra_cost", "unknown_cost"],
}

PROJECT_MARKERS = {
    ".git",
    "AGENTS.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    ".code-workspace",
    "README.md",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dirs(root: Path) -> None:
    for child in ("receipts", "scripts", "bin", "tests", "snapshots"):
        (root / child).mkdir(parents=True, exist_ok=True)


def connect_db(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "inventory.sqlite")
    con.row_factory = sqlite3.Row
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS providers (
            provider_id TEXT PRIMARY KEY,
            billing_class TEXT NOT NULL,
            allowed_by_default INTEGER NOT NULL,
            authority TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS models (
            model_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            runtime TEXT NOT NULL,
            context_tokens INTEGER NOT NULL,
            capabilities_json TEXT NOT NULL,
            task_fit_json TEXT NOT NULL,
            attributes_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quotas (
            provider_id TEXT PRIMARY KEY,
            remaining REAL,
            entitlement REAL,
            percent_remaining REAL,
            reset_at TEXT,
            source TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            root TEXT NOT NULL UNIQUE,
            domain TEXT,
            consequence TEXT,
            allowed_providers_json TEXT NOT NULL,
            forbidden_billing_classes_json TEXT NOT NULL,
            default_local_model TEXT NOT NULL,
            premium_escalation_rule TEXT NOT NULL,
            quota_budget_json TEXT NOT NULL,
            receipt_path TEXT NOT NULL,
            repair_path TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS receipts (
            receipt_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            summary TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repairs (
            repair_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL,
            failure TEXT NOT NULL,
            next_action TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    con.commit()


def initialize_governor(root: Path = DEFAULT_ROOT) -> None:
    root = Path(root)
    ensure_dirs(root)
    con = connect_db(root)
    try:
        init_schema(con)
        now = utc_now()
        for provider_id, provider in PROVIDERS.items():
            con.execute(
                """
                INSERT INTO providers(provider_id,billing_class,allowed_by_default,authority,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  billing_class=excluded.billing_class,
                  allowed_by_default=excluded.allowed_by_default,
                  authority=excluded.authority,
                  updated_at=excluded.updated_at
                """,
                (
                    provider_id,
                    provider["billing_class"],
                    int(provider["allowed_by_default"]),
                    provider["authority"],
                    now,
                ),
            )
        for model in SEED_MODELS:
            _upsert_model(con, model, now)
        con.commit()
    finally:
        con.close()
    write_json(root / "policy.json", DEFAULT_POLICY)
    write_json(root / "model-roster.json", {"source": str(ROSTER_SOURCE), "models": SEED_MODELS, "updated_at": now})
    repair_queue = root / "repair-queue.jsonl"
    if not repair_queue.exists():
        repair_queue.write_text("", encoding="utf-8")


def _upsert_model(con: sqlite3.Connection, model: dict[str, Any], now: str | None = None) -> None:
    now = now or utc_now()
    attrs = {k: v for k, v in model.items() if k not in {"capabilities", "task_fit"}}
    con.execute(
        """
        INSERT INTO models(model_id,provider_id,runtime,context_tokens,capabilities_json,task_fit_json,attributes_json,updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(model_id) DO UPDATE SET
          provider_id=excluded.provider_id,
          runtime=excluded.runtime,
          context_tokens=excluded.context_tokens,
          capabilities_json=excluded.capabilities_json,
          task_fit_json=excluded.task_fit_json,
          attributes_json=excluded.attributes_json,
          updated_at=excluded.updated_at
        """,
        (
            model["model_id"],
            model["provider_id"],
            model["runtime"],
            int(model["context_tokens"]),
            json.dumps(model["capabilities"], sort_keys=True),
            json.dumps(model["task_fit"], sort_keys=True),
            json.dumps(attrs, sort_keys=True),
            now,
        ),
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def slug(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-").lower()
    return value[:80] or "item"


class Governor:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root)
        if not (self.root / "inventory.sqlite").exists():
            initialize_governor(self.root)
        self.con = connect_db(self.root)
        init_schema(self.con)
        self.policy = self._load_policy()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Governor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _load_policy(self) -> dict[str, Any]:
        policy_path = self.root / "policy.json"
        if policy_path.exists():
            return json.loads(policy_path.read_text(encoding="utf-8"))
        return DEFAULT_POLICY

    def update_quota(
        self,
        provider_id: str,
        remaining: float | None,
        entitlement: float | None,
        percent_remaining: float | None,
        reset_at: str | None = None,
        source: str = "manual",
    ) -> None:
        self.con.execute(
            """
            INSERT INTO quotas(provider_id,remaining,entitlement,percent_remaining,reset_at,source,updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(provider_id) DO UPDATE SET
              remaining=excluded.remaining,
              entitlement=excluded.entitlement,
              percent_remaining=excluded.percent_remaining,
              reset_at=excluded.reset_at,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (provider_id, remaining, entitlement, percent_remaining, reset_at, source, utc_now()),
        )
        self.con.commit()

    def select_model(
        self,
        task_type: str,
        required_capabilities: list[str] | None = None,
        context_tokens: int = 0,
        consequence: str = "normal",
        value_reason: str | None = None,
        provider_allowlist: list[str] | None = None,
        bulk: bool = False,
        privacy: str = "normal",
    ) -> dict[str, Any]:
        required = set(required_capabilities or [])
        candidates = self._models_with_provider()
        if provider_allowlist:
            allow = set(provider_allowlist)
            candidates = [c for c in candidates if c["provider_id"] in allow]

        disabled = set(self.policy["disabled_billing_classes"])
        unsafe_seen = sorted({c["billing_class"] for c in candidates if c["billing_class"] in disabled})
        if provider_allowlist:
            unsafe_seen = sorted(set(unsafe_seen).union(self._disabled_billing_classes_for_providers(provider_allowlist)))
        candidates = [c for c in candidates if c["billing_class"] not in disabled]
        if bulk:
            candidates = [c for c in candidates if c["billing_class"] == self.policy["bulk_requires_billing_class"]]
        if required:
            candidates = [c for c in candidates if required.issubset(set(c["capabilities"]))]
        candidates = [c for c in candidates if int(c["context_tokens"]) >= int(context_tokens)]
        quota_blocks = self._quota_block_reasons(candidates, consequence, value_reason)
        candidates = [c for c in candidates if self._quota_allows(c["provider_id"], c["billing_class"], consequence, value_reason)]

        if not candidates:
            reasons = []
            if unsafe_seen:
                reasons.append("disabled billing class present: " + ",".join(unsafe_seen))
            if quota_blocks:
                reasons.append("quota blocked: " + "; ".join(sorted(set(quota_blocks))))
            reasons.append("no safe competent provider satisfied cost, quota, capability, and context gates")
            return self._write_decision_receipt(
                {
                    "decision": "deny",
                    "reason": "; ".join(reasons),
                    "task_type": task_type,
                    "required_capabilities": sorted(required),
                    "context_tokens": context_tokens,
                    "consequence": consequence,
                    "value_reason": value_reason,
                    "provider_allowlist": provider_allowlist,
                    "bulk": bulk,
                    "privacy": privacy,
                }
            )

        ranked = sorted(
            candidates,
            key=lambda c: (
                BILLING_RANK[c["billing_class"]],
                -self._task_score(c, task_type, required),
                c["context_tokens"],
                c["model_id"],
            ),
        )
        chosen = ranked[0]
        return self._write_decision_receipt(
            {
                "decision": "allow",
                "reason": "cheapest competent model selected after cost, quota, capability, and context gates",
                "task_type": task_type,
                "model_id": chosen["model_id"],
                "provider_id": chosen["provider_id"],
                "billing_class": chosen["billing_class"],
                "capabilities": chosen["capabilities"],
                "context_tokens": context_tokens,
                "consequence": consequence,
                "value_reason": value_reason,
                "provider_allowlist": provider_allowlist,
                "bulk": bulk,
                "privacy": privacy,
            }
        )

    def _disabled_billing_classes_for_providers(self, provider_ids: list[str]) -> list[str]:
        disabled = set(self.policy["disabled_billing_classes"])
        rows = self.con.execute(
            "SELECT billing_class FROM providers WHERE provider_id IN (%s)" % ",".join("?" for _ in provider_ids),
            provider_ids,
        ).fetchall()
        return sorted({row["billing_class"] for row in rows if row["billing_class"] in disabled})

    def _quota_block_reasons(
        self,
        candidates: list[dict[str, Any]],
        consequence: str,
        value_reason: str | None,
    ) -> list[str]:
        reasons = []
        seen: set[str] = set()
        for candidate in candidates:
            provider_id = candidate["provider_id"]
            billing_class = candidate["billing_class"]
            if billing_class != "subscription_quota" or provider_id in seen:
                continue
            seen.add(provider_id)
            quota = self.con.execute("SELECT * FROM quotas WHERE provider_id=?", (provider_id,)).fetchone()
            if not quota:
                reasons.append(f"{provider_id} subscription_quota unavailable because quota state is unproved")
                continue
            percent = quota["percent_remaining"]
            if percent is None:
                reasons.append(f"{provider_id} subscription_quota unavailable because percent remaining is unproved")
                continue
            reserve = float(self.policy["reserve_percent"])
            if float(percent) < reserve and not (consequence == "urgent" and value_reason):
                reasons.append(f"{provider_id} subscription quota reserve protected ({float(percent):.1f}% < {reserve:.1f}%)")
        return reasons

    def _models_with_provider(self) -> list[dict[str, Any]]:
        rows = self.con.execute(
            """
            SELECT m.*, p.billing_class
            FROM models m
            JOIN providers p ON p.provider_id = m.provider_id
            """
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            attrs = json.loads(row["attributes_json"])
            out.append(
                {
                    **attrs,
                    "model_id": row["model_id"],
                    "provider_id": row["provider_id"],
                    "runtime": row["runtime"],
                    "context_tokens": row["context_tokens"],
                    "capabilities": json.loads(row["capabilities_json"]),
                    "task_fit": json.loads(row["task_fit_json"]),
                    "billing_class": row["billing_class"],
                }
            )
        return out

    def _task_score(self, model: dict[str, Any], task_type: str, required: set[str]) -> int:
        score = 0
        if model["model_id"] == self.policy.get("app_defaults", {}).get(task_type):
            score += 50
        if task_type in model["task_fit"]:
            score += 10
        score += len(required.intersection(set(model["capabilities"]))) * 3
        if model["model_id"] == self.policy["default_local_model"]:
            score += 2
        return score

    def _quota_allows(self, provider_id: str, billing_class: str, consequence: str, value_reason: str | None) -> bool:
        if billing_class != "subscription_quota":
            return True
        quota = self.con.execute("SELECT * FROM quotas WHERE provider_id=?", (provider_id,)).fetchone()
        if not quota:
            return False
        percent = quota["percent_remaining"]
        if percent is None:
            return False
        if float(percent) >= float(self.policy["reserve_percent"]):
            return True
        if consequence == "urgent" and value_reason:
            return True
        return False

    def _write_decision_receipt(self, data: dict[str, Any]) -> dict[str, Any]:
        receipt_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{slug(data.get('task_type', 'route'))}"
        path = self.root / "receipts" / f"{receipt_id}.json"
        data = {**data, "receipt_id": receipt_id, "created_at": utc_now()}
        write_json(path, data)
        self.con.execute(
            "INSERT INTO receipts(receipt_id,kind,path,summary,created_at) VALUES(?,?,?,?,?)",
            (receipt_id, "route_decision", str(path), data["decision"], data["created_at"]),
        )
        self.con.commit()
        data["receipt_path"] = str(path)
        return data

    def discover_projects(self, roots: list[Path], max_depth: int = 5) -> list[dict[str, Any]]:
        found: dict[str, Path] = {}
        for root in roots:
            root = Path(root)
            if not root.exists():
                self.add_repair("project-discovery", f"root missing: {root}", "verify drive mount or remove stale root")
                continue
            if root.is_file():
                root = root.parent
            for path in self._walk_project_candidates(root, max_depth=max_depth):
                found[str(path).lower()] = path

        records = []
        for path in sorted(found.values(), key=lambda p: str(p).lower()):
            records.append(self._upsert_project(path))
        return records

    def _walk_project_candidates(self, root: Path, max_depth: int) -> list[Path]:
        skip_names = {
            "$Recycle.Bin",
            "System Volume Information",
            "Windows",
            "Program Files",
            "Program Files (x86)",
            "node_modules",
            ".venv",
            "__pycache__",
            "AppData",
        }
        out: list[Path] = []
        root_depth = len(root.parts)
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            dirs[:] = [d for d in dirs if d not in skip_names and not d.startswith(".cache")]
            if depth > max_depth:
                dirs[:] = []
                continue
            names = set(files).union(set(dirs))
            if names.intersection(PROJECT_MARKERS):
                out.append(current_path)
        if not out and root.exists():
            out.append(root)
        return out

    def _upsert_project(self, path: Path) -> dict[str, Any]:
        path_text = str(path)
        path_hash = hashlib.sha1(path_text.lower().encode("utf-8")).hexdigest()[:10]
        normalized_path = path_text.replace("\\", "-").replace(":", "")
        project_id = f"{slug(normalized_path)[:68]}-{path_hash}"
        receipt_path = str(self.root / "receipts" / f"project-{project_id}.json")
        repair_path = str(self.root / "repair-queue.jsonl")
        record = {
            "project_id": project_id,
            "root": str(path),
            "domain": infer_domain(path),
            "consequence": "normal",
            "allowed_providers": ["ollama-local", "github-copilot", "codex-chatgpt", "claude-max", "ollama-cloud", "gemini-oauth"],
            "forbidden_billing_classes": ["metered_extra_cost", "unknown_cost"],
            "default_local_model": self.policy["default_local_model"],
            "premium_escalation_rule": "premium requires high consequence plus value_reason and quota above reserve",
            "quota_budget": {"reserve_percent": self.policy["reserve_percent"], "policy": "balanced_roi"},
            "receipt_path": receipt_path,
            "repair_path": repair_path,
            "updated_at": utc_now(),
        }
        write_json(Path(receipt_path), record)
        self.con.execute(
            """
            INSERT INTO projects(project_id,root,domain,consequence,allowed_providers_json,forbidden_billing_classes_json,
              default_local_model,premium_escalation_rule,quota_budget_json,receipt_path,repair_path,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(root) DO UPDATE SET
              domain=excluded.domain,
              consequence=excluded.consequence,
              allowed_providers_json=excluded.allowed_providers_json,
              forbidden_billing_classes_json=excluded.forbidden_billing_classes_json,
              default_local_model=excluded.default_local_model,
              premium_escalation_rule=excluded.premium_escalation_rule,
              quota_budget_json=excluded.quota_budget_json,
              receipt_path=excluded.receipt_path,
              repair_path=excluded.repair_path,
              updated_at=excluded.updated_at
            """,
            (
                record["project_id"],
                record["root"],
                record["domain"],
                record["consequence"],
                json.dumps(record["allowed_providers"], sort_keys=True),
                json.dumps(record["forbidden_billing_classes"], sort_keys=True),
                record["default_local_model"],
                record["premium_escalation_rule"],
                json.dumps(record["quota_budget"], sort_keys=True),
                record["receipt_path"],
                record["repair_path"],
                record["updated_at"],
            ),
        )
        self.con.commit()
        return record

    def add_repair(self, surface: str, failure: str, next_action: str, status: str = "open") -> None:
        repair_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{slug(surface)}"
        entry = {
            "repair_id": repair_id,
            "surface": surface,
            "failure": failure,
            "next_action": next_action,
            "status": status,
            "created_at": utc_now(),
        }
        with (self.root / "repair-queue.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        self.con.execute(
            "INSERT INTO repairs(repair_id,surface,failure,next_action,status,created_at) VALUES(?,?,?,?,?,?)",
            (repair_id, surface, failure, next_action, status, entry["created_at"]),
        )
        self.con.commit()

    def refresh(self, roots: list[Path] | None = None, max_depth: int = 4) -> dict[str, Any]:
        summary: dict[str, Any] = {"created_at": utc_now(), "steps": {}}
        summary["steps"]["ollama"] = self._refresh_ollama_models()
        summary["steps"]["copilot"] = self._refresh_copilot_quota()
        summary["steps"]["hermes"] = self._probe_command(["hermes", "status"], "hermes")
        summary["steps"]["codex"] = self._probe_command(["codex.cmd", "login", "status"], "codex")
        summary["steps"]["claude"] = self._probe_command(["claude", "auth", "status"], "claude")
        if roots:
            projects = self.discover_projects(roots, max_depth=max_depth)
            summary["steps"]["projects"] = {"count": len(projects)}
        receipt = self.root / "receipts" / f"refresh-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        write_json(receipt, summary)
        return summary

    def _refresh_ollama_models(self) -> dict[str, Any]:
        result = run_command(["ollama", "list"], timeout=20)
        if result["returncode"] != 0:
            self.add_repair("ollama", result["stderr"] or result["stdout"], "verify Ollama listener on 11434 and rerun refresh")
            return {"ok": False, "error": result}
        count = 0
        for line in result["stdout"].splitlines()[1:]:
            parts = line.split()
            if not parts:
                continue
            model_id = parts[0]
            if self.con.execute("SELECT 1 FROM models WHERE model_id=?", (model_id,)).fetchone():
                count += 1
        return {"ok": True, "listed_models": count}

    def _refresh_copilot_quota(self) -> dict[str, Any]:
        result = run_command(["gh", "api", "/copilot_internal/user"], timeout=20)
        if result["returncode"] != 0:
            self.add_repair("github-copilot", result["stderr"] or result["stdout"], "rerun gh auth status and gh api /copilot_internal/user")
            return {"ok": False, "error": result}
        try:
            data = json.loads(result["stdout"])
            premium = data["quota_snapshots"]["premium_interactions"]
            self.update_quota(
                "github-copilot",
                remaining=premium.get("remaining"),
                entitlement=premium.get("entitlement"),
                percent_remaining=premium.get("percent_remaining"),
                reset_at=data.get("quota_reset_date_utc"),
                source="gh api /copilot_internal/user",
            )
            return {
                "ok": True,
                "plan": data.get("copilot_plan"),
                "remaining": premium.get("remaining"),
                "entitlement": premium.get("entitlement"),
                "percent_remaining": premium.get("percent_remaining"),
                "reset_at": data.get("quota_reset_date_utc"),
            }
        except Exception as exc:
            self.add_repair("github-copilot", f"quota parse failed: {exc}", "inspect gh api /copilot_internal/user response")
            return {"ok": False, "error": str(exc)}

    def _probe_command(self, command: list[str], surface: str) -> dict[str, Any]:
        result = run_command(command, timeout=20)
        if result["returncode"] != 0:
            self.add_repair(surface, result["stderr"] or result["stdout"], f"repair {surface} command surface and rerun {' '.join(command)}")
            return {"ok": False, "returncode": result["returncode"]}
        return {"ok": True, "stdout_head": result["stdout"][:1000]}


def infer_domain(path: Path) -> str:
    text = str(path).lower()
    if "example" in text:
        return "example"
    if "resume" in text:
        return "resume"
    if "retropie" in text or "raspberry" in text:
        return "hardware"
    if "website" in text:
        return "website"
    return "general"


def run_command(command: list[str], timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": f"timeout after {timeout}s",
        }
    except FileNotFoundError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}
    except PermissionError as exc:
        return {"returncode": 126, "stdout": "", "stderr": str(exc)}


def default_roots() -> list[Path]:
    roots: list[Path] = []
    for drive in "C", "G":
        root = Path(f"{drive}:\\")
        if root.exists():
            roots.append(root)
    return roots


def cmd_init(args: argparse.Namespace) -> int:
    initialize_governor(Path(args.root))
    print(json.dumps({"ok": True, "root": args.root}, indent=2))
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    with Governor(Path(args.root)) as gov:
        route = gov.select_model(
            task_type=args.task_type,
            required_capabilities=args.capability or [],
            context_tokens=args.context_tokens,
            consequence=args.consequence,
            value_reason=args.value_reason,
            provider_allowlist=args.provider,
            bulk=args.bulk,
            privacy=args.privacy,
        )
    print(json.dumps(route, indent=2, sort_keys=True))
    return 0 if route["decision"] == "allow" else 2


def cmd_discover(args: argparse.Namespace) -> int:
    roots = [Path(p) for p in args.scan_root] if args.scan_root else default_roots()
    with Governor(Path(args.root)) as gov:
        records = gov.discover_projects(roots, max_depth=args.max_depth)
    print(json.dumps({"count": len(records), "roots": [str(r) for r in roots]}, indent=2))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    roots = [Path(p) for p in args.scan_root] if args.scan_root else None
    with Governor(Path(args.root)) as gov:
        summary = gov.refresh(roots=roots, max_depth=args.max_depth)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with Governor(Path(args.root)) as gov:
        counts = {}
        for table in ("providers", "models", "quotas", "projects", "receipts", "repairs"):
            counts[table] = gov.con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    print(json.dumps({"root": str(gov.root), "counts": counts}, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Resource Governor")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = parser.add_subparsers(required=True)

    p_init = sub.add_parser("init")
    p_init.set_defaults(func=cmd_init)

    p_route = sub.add_parser("route")
    p_route.add_argument("--task-type", required=True)
    p_route.add_argument("--capability", action="append")
    p_route.add_argument("--context-tokens", type=int, default=0)
    p_route.add_argument("--consequence", default="normal")
    p_route.add_argument("--value-reason")
    p_route.add_argument("--provider", action="append")
    p_route.add_argument("--bulk", action="store_true")
    p_route.add_argument("--privacy", default="normal")
    p_route.set_defaults(func=cmd_route)

    p_discover = sub.add_parser("discover-projects")
    p_discover.add_argument("--scan-root", action="append")
    p_discover.add_argument("--max-depth", type=int, default=5)
    p_discover.set_defaults(func=cmd_discover)

    p_refresh = sub.add_parser("refresh")
    p_refresh.add_argument("--scan-root", action="append")
    p_refresh.add_argument("--max-depth", type=int, default=4)
    p_refresh.set_defaults(func=cmd_refresh)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
