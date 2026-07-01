from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from orchestrator.adapters.builtins import _run_bounded
from orchestrator.state.store import StateStore


async def repair_openclaw_gateway(store: StateStore) -> dict[str, Any]:
    return {
        "service": "openclaw-gateway",
        "state": "retired",
        "reason": "OpenClaw was retired during the 2026-06-16 consolidation; repair-services must not restart or reinstall it.",
        "archive_path": "C:\\Users\\Couch\\Archive\\openclaw-retired-2026-06-16",
        "replacement": "python -m orchestrator.cli.main route",
    }


async def repair_hermes_local_model(store: StateStore) -> dict[str, Any]:
    return {
        "service": "hermes-agent",
        "state": "retired_downstream",
        "reason": "Hermes is upstream of the orchestrator brain; repair-services must not require Hermes as a downstream custom-Ollama adapter.",
        "replacement": "Hermes prompt shims call `python -m orchestrator.cli.main hermes-route` before execution.",
    }


async def _lm_studio_models_visible() -> tuple[bool, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get("http://127.0.0.1:1234/v1/models")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return False, {"ok": False, "error": str(exc)}
    models = payload.get("data") if isinstance(payload, dict) else None
    return bool(models), {"ok": True, "models": models or []}


def _write_lm_studio_repair_packet(store: StateStore, detail: str) -> Path:
    packet_dir = store.settings.home / "repair-packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet_path = packet_dir / "lm-studio-cli-server-2026-06-05.md"
    packet_path.write_text(
        "\n".join(
            [
                "# LM Studio CLI Server Repair Packet",
                "",
                "Terminal state: blocked_after_repair_attempt",
                "",
                "Consequence: the orchestrator cannot prove the `lm-studio` adapter without a no-touch local server or CLI.",
                "",
                "Mechanism:",
                "- Headless CLI path checked: `C:\\Users\\Couch\\.lmstudio\\bin\\lms.exe`.",
                "- Bundled desktop CLI fallback checked: `C:\\Program Files\\LM Studio\\resources\\app\\.webpack\\lms.exe`.",
                "- The orchestrator probed `http://127.0.0.1:1234/v1/models`.",
                f"- Latest repair detail: {detail}",
                "- Agent launch of the visible LM Studio app is forbidden by the no-desktop-takeover rule.",
                "",
                "Operator action:",
                "1. Install the official headless llmster service with `irm https://lmstudio.ai/install.ps1 | iex` if `C:\\Users\\Couch\\.lmstudio\\bin\\lms.exe` is missing.",
                "2. In a terminal you control, run: `lms daemon up`.",
                "3. Run: `lms server start --bind 127.0.0.1 --port 1234`.",
                "4. Load or register one local model.",
                "",
                "Completion test:",
                "- `where.exe lms` returns a path, or `C:\\Users\\Couch\\.lmstudio\\bin\\lms.exe` exists.",
                "- `lms daemon status` succeeds.",
                "- `lms server status` succeeds.",
                "- `Invoke-RestMethod http://127.0.0.1:1234/v1/models` returns at least one model.",
                "- `python -m orchestrator.cli.main prove-adapter lm-studio --capability embeddings` can produce an `lm-studio` receipt when only an embedding model is present.",
                "",
                "Forbidden substitutes:",
                "- Do not launch the visible LM Studio app from an agent.",
                "- Do not use direct OpenAI, Anthropic, OpenRouter, or direct Gemini API as a fallback.",
                "- Do not mark CT-12 complete until `lm-studio` has a receipt or the adapter is explicitly removed from discovered services.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return packet_path


async def repair_lm_studio_local_server(store: StateStore) -> dict[str, Any]:
    visible, models = await _lm_studio_models_visible()
    if visible:
        resolved = await store.resolve_repair_items("lm-studio", {"models": models.get("models", [])})
        return {"service": "lm-studio", "state": "healthy", "models": models, "resolved_repairs": resolved}

    status = await _run_bounded("lms", ["status"], timeout=20)
    daemon = await _run_bounded("lms", ["daemon", "up", "--json"], timeout=35)
    start = await _run_bounded("lms", ["server", "start", "--bind", "127.0.0.1", "--port", "1234"], timeout=35)
    visible_after_start, models_after_start = await _lm_studio_models_visible()
    if visible_after_start:
        resolved = await store.resolve_repair_items("lm-studio", {"models": models_after_start.get("models", [])})
        return {
            "service": "lm-studio",
            "state": "healthy",
            "status": status,
            "daemon": daemon,
            "start": start,
            "models": models_after_start,
            "resolved_repairs": resolved,
        }

    detail = str(
        start.get("error")
        or start.get("stderr")
        or daemon.get("error")
        or daemon.get("stderr")
        or status.get("error")
        or status.get("stderr")
        or models_after_start.get("error")
        or models.get("error")
        or "LM Studio local server is not exposing /v1/models."
    )
    packet_path = _write_lm_studio_repair_packet(store, detail)
    repair_id = await store.add_or_get_open_repair_item(
        "lm-studio",
        detail,
        f"Owner must complete LM Studio first-run and start the local server; packet: {packet_path}",
    )
    return {
        "service": "lm-studio",
        "state": "blocked_after_repair_attempt",
        "repair_id": repair_id,
        "packet": str(packet_path),
        "status": status,
        "daemon": daemon,
        "start": start,
        "models": models_after_start,
    }


async def repair_core_services(store: StateStore) -> dict[str, Any]:
    openclaw = await repair_openclaw_gateway(store)
    hermes = await repair_hermes_local_model(store)
    lm_studio = await repair_lm_studio_local_server(store)
    return {"openclaw": openclaw, "hermes": hermes, "lm_studio": lm_studio}
