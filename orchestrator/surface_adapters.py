from __future__ import annotations

from typing import Any


SURFACE_TO_DISPATCH_ADAPTER = {
    "claude-desktop-mcp": "claude-desktop-mcp",
    "claude-max": "claude-code-cli",
    "claude-code-cli": "claude-code-cli",
    "codex-chatgpt": "codex-desktop",
    "codex-cli": "codex-cli",
    "copilot-gh": "copilot-gh",
    "github-copilot": "copilot-gh",
    "copilot-vscode": "copilot-vscode",
    "gemini-cli": "gemini-cli",
    "ollama-cloud": "ollama-cloud",
    "ollama-local": "ollama-http",
    "ollama-cli": "ollama-cli",
    "ollama-http": "ollama-http",
    "lm-studio": "lm-studio",
}


def dispatch_adapter_for_worker(worker: dict[str, Any]) -> str | None:
    for key in ("dispatch_adapter", "adapter_name"):
        value = str(worker.get(key) or "").strip()
        if value:
            return value
    surface = str(worker.get("surface") or "").strip()
    return SURFACE_TO_DISPATCH_ADAPTER.get(surface)
