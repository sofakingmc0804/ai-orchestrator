"""
Model lifecycle manager — detects retired and new models across all platforms.

Platforms probed:
  - ollama-cloud: web-scrapes ollama.com/search?c=cloud for the live model catalog
  - ollama-local: queries 127.0.0.1:11434/api/tags for installed models
  - github-copilot: queries gh api /copilot_internal/user for available models

For each platform, compares live model lists against worker_cards in the DB.
Reports:
  - retired: in DB but not live (model has been retired by the provider)
  - discovered: live but not in DB (new model available)
  - alive: in both DB and live

Also auto-fixes the Hermes auxiliary.vision.model config when the current
vision model is retired — finds the first alive model with vision capability.

Usage:
  from orchestrator.discovery.model_lifecycle import probe_model_lifecycle
  result = await probe_model_lifecycle(settings)

CLI:
  orch model-lifecycle          # probe and report
  orch model-lifecycle --fix    # probe, report, and auto-fix config
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Platform probes ──────────────────────────────────────────────────────

def _fetch_ollama_cloud_models() -> list[dict[str, Any]]:
    """
    Scrape ollama.com/search?c=cloud for the live cloud model catalog.
    Returns list of {name, tags, capabilities, updated}.
    """
    url = "https://ollama.com/search?c=cloud"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return [{"_error": str(e)}]

    models: list[dict[str, Any]] = []
    # Parse model entries from the HTML. The search page has entries like:
    # <a href="/library/glm-5.2" ...> with title and tag info
    # We use regex to extract model names and their capability badges.
    model_pattern = re.compile(
        r'href="/library/([a-z0-9][a-z0-9._-]*)"', re.IGNORECASE
    )
    seen: set[str] = set()
    for match in model_pattern.finditer(html):
        name = match.group(1)
        if name in seen or name in ("blog", "search", "login", "signup"):
            continue
        seen.add(name)
        # Extract capabilities from badge spans: >vision< inside a span element
        start = match.end()
        context = html[start : start + 800]
        caps: list[str] = []
        for cap in ["vision", "tools", "thinking", "audio", "embedding", "cloud"]:
            if re.search(rf'>{cap}<', context, re.IGNORECASE):
                caps.append(cap)
        models.append({
            "name": name,
            "capabilities": sorted(set(caps)),
            "has_vision": "vision" in caps,
            "has_cloud": "cloud" in caps or True,  # we're on the cloud page
        })
    return models


def _fetch_ollama_local_models() -> list[dict[str, Any]]:
    """Query local Ollama instance for installed models."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        models = []
        for m in data.get("models", []):
            name = m.get("name", m.get("model", ""))
            details = m.get("details", {})
            caps = []
            if details.get("parameter_size"):
                caps.append("text")
            # Check modelfile for vision capabilities
            model_path = m.get("modelfile", "")
            if "clip" in model_path.lower() or "vision" in model_path.lower():
                caps.append("vision")
            models.append({
                "name": name,
                "capabilities": sorted(set(caps)),
                "has_vision": "vision" in caps,
                "size": m.get("size", 0),
            })
        return models
    except Exception as e:
        return [{"_error": str(e)}]


def _fetch_copilot_models() -> list[dict[str, Any]]:
    """Query GitHub Copilot for available models via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "api", "/copilot_internal/user"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return [{"_error": result.stderr.strip() or "gh api failed"}]
        data = json.loads(result.stdout)
        models = []
        # Copilot returns model availability in the response
        for m in data.get("models", data.get("available_models", [])):
            if isinstance(m, str):
                models.append({"name": m, "capabilities": ["text"]})
            elif isinstance(m, dict):
                name = m.get("name", m.get("id", ""))
                caps = m.get("capabilities", ["text"])
                models.append({
                    "name": name,
                    "capabilities": caps if isinstance(caps, list) else [caps],
                    "has_vision": "vision" in (caps if isinstance(caps, list) else [caps]),
                })
        return models
    except Exception as e:
        return [{"_error": str(e)}]


# ─── DB comparison ─────────────────────────────────────────────────────────

def _db_worker_models(db_path: str) -> list[dict[str, Any]]:
    """Read all worker_cards from the DB, return model_id + surface + provider."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT worker_id, model_id, base_model, surface, provider_id,
               capabilities_json, modalities_json, dynamic_state_json
        FROM worker_cards
    """).fetchall()
    conn.close()
    workers = []
    for r in rows:
        caps = json.loads(r["capabilities_json"]) if r["capabilities_json"] else []
        mods = json.loads(r["modalities_json"]) if r["modalities_json"] else []
        workers.append({
            "worker_id": r["worker_id"],
            "model_id": r["model_id"],
            "base_model": r["base_model"],
            "surface": r["surface"],
            "provider_id": r["provider_id"],
            "has_vision": "vision" in caps or "image" in mods,
        })
    return workers


def _normalize_model_name(name: str) -> str:
    """Strip :cloud, :latest suffixes for comparison."""
    for suffix in [":cloud", ":latest", ":ollama-cloud"]:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# ─── Lifecycle probe ──────────────────────────────────────────────────────

async def probe_model_lifecycle(
    settings: Settings,
    fix_config: bool = False,
) -> dict[str, Any]:
    """
    Probe all platforms for live model availability, compare against DB,
    report retired/discovered/alive models, and optionally auto-fix config.

    Args:
        settings: Orchestrator Settings
        fix_config: If True, auto-fix Hermes auxiliary.vision.model when retired

    Returns:
        dict with per-platform results and a summary
    """
    db_path = str(settings.state_path)
    db_workers = _db_worker_models(db_path)

    # Probe all platforms in parallel
    loop = asyncio.get_event_loop()
    cloud_result, local_result, copilot_result = await asyncio.gather(
        loop.run_in_executor(None, _fetch_ollama_cloud_models),
        loop.run_in_executor(None, _fetch_ollama_local_models),
        loop.run_in_executor(None, _fetch_copilot_models),
    )

    # Normalize cloud models to names
    cloud_models = [m for m in cloud_result if "_error" not in m]
    cloud_errors = [m for m in cloud_result if "_error" in m]
    local_models = [m for m in local_result if "_error" not in m]
    local_errors = [m for m in local_result if "_error" in m]
    copilot_models = [m for m in copilot_result if "_error" not in m]
    copilot_errors = [m for m in copilot_result if "_error" in m]

    # Build sets of live model names per platform
    live_cloud_names = {_normalize_model_name(m["name"]) for m in cloud_models}
    live_local_names = {_normalize_model_name(m["name"]) for m in local_models}
    live_copilot_names = {m["name"] for m in copilot_models}

    # Classify DB workers
    retired: list[dict[str, Any]] = []
    discovered: list[dict[str, Any]] = []
    alive: list[dict[str, Any]] = []

    for w in db_workers:
        base = _normalize_model_name(w["base_model"] or w["model_id"])
        surface = w["surface"]
        provider = w["provider_id"]

        if provider == "ollama-cloud":
            live_set = live_cloud_names
        elif provider == "ollama-local" or surface == "ollama-local":
            live_set = live_local_names
        elif provider == "github-copilot" or surface == "copilot-gh":
            live_set = live_copilot_names
        else:
            # Metered API providers (anthropic, openai-api) — skip liveness check
            # These are always available if the API key is valid
            alive.append(w)
            continue

        if base in live_set:
            alive.append(w)
        else:
            retired.append(w)

    # Discover new models not in DB
    db_cloud_names = {
        _normalize_model_name(w["base_model"] or w["model_id"])
        for w in db_workers
        if w["provider_id"] == "ollama-cloud"
    }
    for m in cloud_models:
        norm = _normalize_model_name(m["name"])
        if norm not in db_cloud_names:
            discovered.append({
                "name": m["name"],
                "provider_id": "ollama-cloud",
                "capabilities": m.get("capabilities", []),
                "has_vision": m.get("has_vision", False),
            })

    db_local_names = {
        _normalize_model_name(w["base_model"] or w["model_id"])
        for w in db_workers
        if w["provider_id"] == "ollama-local" or w["surface"] == "ollama-local"
    }
    for m in local_models:
        norm = _normalize_model_name(m["name"])
        if norm not in db_local_names:
            discovered.append({
                "name": m["name"],
                "provider_id": "ollama-local",
                "capabilities": m.get("capabilities", []),
                "has_vision": m.get("has_vision", False),
            })

    # Vision model auto-fix
    vision_fix: dict[str, Any] | None = None
    if fix_config:
        vision_fix = _auto_fix_vision_model(retired, cloud_models)

    # Summary
    summary = {
        "probed_at": _utc(),
        "platforms": {
            "ollama-cloud": {
                "live_count": len(cloud_models),
                "errors": [e["_error"] for e in cloud_errors] if cloud_errors else [],
            },
            "ollama-local": {
                "live_count": len(local_models),
                "errors": [e["_error"] for e in local_errors] if local_errors else [],
            },
            "github-copilot": {
                "live_count": len(copilot_models),
                "errors": [e["_error"] for e in copilot_errors] if copilot_errors else [],
            },
        },
        "db_workers_total": len(db_workers),
        "retired_count": len(retired),
        "discovered_count": len(discovered),
        "alive_count": len(alive),
        "retired": [
            {"worker_id": w["worker_id"], "model_id": w["model_id"],
             "surface": w["surface"], "provider_id": w["provider_id"],
             "has_vision": w.get("has_vision", False)}
            for w in retired
        ],
        "discovered": discovered,
        "vision_fix": vision_fix,
    }
    return summary


def _auto_fix_vision_model(
    retired_workers: list[dict[str, Any]],
    live_cloud_models: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Check if the current Hermes auxiliary.vision.model is retired.
    If so, find the first alive cloud model with vision capability and
    update the config via `hermes config set`.
    """
    # Read current vision model from config
    config_path = Path.home() / "AppData" / "Local" / "hermes" / "config.yaml"
    if not config_path.exists():
        return {"error": "hermes config.yaml not found"}

    config_text = config_path.read_text(encoding="utf-8")
    # Find current vision model
    vision_match = re.search(
        r"vision:\s*\n\s*model:\s*(\S+)", config_text
    )
    if not vision_match:
        return {"error": "vision.model not found in config"}

    current_model = vision_match.group(1)
    current_base = _normalize_model_name(current_model)

    # Check if current model is in the retired list
    retired_vision = [
        w for w in retired_workers
        if w.get("has_vision")
        and _normalize_model_name(w["model_id"]) == current_base
    ]

    if not retired_vision:
        # Also check if the model name appears in any retired worker
        # (the aux vision model might not be in worker_cards at all)
        # Try to verify it against the live list directly
        if current_base in {_normalize_model_name(m["name"]) for m in live_cloud_models}:
            return {"current_model": current_model, "status": "alive", "action": "none"}
        # Model not in DB and not in live list — likely retired
        # Find a replacement
        pass

    # Find replacement: first alive cloud model with vision
    vision_candidates = [
        m for m in live_cloud_models
        if m.get("has_vision") and m["name"] not in ("qwen3-vl", "gemini-3-flash-preview", "gemma3")
    ]
    # Prefer models we know are active (kimi, gemma4, minimax-m3)
    preferred_order = ["kimi-k2.6", "kimi-k3", "gemma4", "minimax-m3", "mistral-large-3"]
    replacement = None
    for pref in preferred_order:
        for m in vision_candidates:
            if m["name"] == pref:
                replacement = m
                break
        if replacement:
            break
    if not replacement and vision_candidates:
        replacement = vision_candidates[0]
    if not replacement:
        return {
            "current_model": current_model,
            "status": "retired",
            "action": "no_replacement_found",
            "live_vision_models": [m["name"] for m in vision_candidates],
        }

    new_model = f"{replacement['name']}:cloud"

    # Apply the fix
    try:
        result = subprocess.run(
            ["hermes", "config", "set", "auxiliary.vision.model", new_model],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {
                "current_model": current_model,
                "status": "retired",
                "action": "replaced",
                "new_model": new_model,
                "hermes_output": result.stdout.strip(),
            }
        else:
            return {
                "current_model": current_model,
                "status": "retired",
                "action": "hermes_config_failed",
                "error": result.stderr.strip(),
            }
    except Exception as e:
        return {
            "current_model": current_model,
            "status": "retired",
            "action": "exception",
            "error": str(e),
        }