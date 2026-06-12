from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar


T = TypeVar("T")

PROVIDER_DISPLAY_ALIASES = {
    "ollama_cloud": "ollama-cloud",
}

PROVIDER_ROOT_ALIASES = {
    "ollama-local": "ollama",
    "ollama-http": "ollama",
    "ollama-cli": "ollama",
    "ollama-cloud": "ollama",
    "github-copilot": "github_copilot",
    "copilot-gh": "github_copilot",
    "claude-max": "claude",
    "claude-code-cli": "claude",
    "gemini-cli": "gemini",
    "gemini-oauth": "gemini",
    "codex-chatgpt": "codex",
    "codex-cli": "codex",
    "hermes-nous": "nous",
    "hermes-agent": "nous",
}


def canonical_provider(provider: str) -> str:
    normalized = provider.strip()
    return PROVIDER_DISPLAY_ALIASES.get(normalized, normalized)


def provider_keys(provider: str) -> tuple[str, ...]:
    normalized = provider.strip()
    canonical = canonical_provider(normalized)
    candidates = [
        canonical,
        normalized,
        canonical.replace("-", "_"),
        canonical.replace("_", "-"),
        normalized.replace("-", "_"),
        normalized.replace("_", "-"),
    ]
    root = PROVIDER_ROOT_ALIASES.get(canonical) or PROVIDER_ROOT_ALIASES.get(canonical.replace("_", "-"))
    if root:
        candidates.extend([root, root.replace("-", "_"), root.replace("_", "-")])
    return tuple(dict.fromkeys(item for item in candidates if item))


def lookup_by_provider(provider: str, rows: Mapping[str, T]) -> T | None:
    keys = set(provider_keys(provider))
    for key, row in rows.items():
        key_text = str(key)
        if key_text in keys or key_text.replace("-", "_") in keys or key_text.replace("_", "-") in keys:
            return row
    return None
