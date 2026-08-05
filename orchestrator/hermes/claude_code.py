"""Claude Code CLI headless contract used by Hermes.

The CLI owns Claude.ai OAuth storage and refresh.  Hermes must invoke that
provider-owned session without copying credentials into its own state or
allowing an inherited API-key environment variable to override the
subscription account.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping


CLAUDE_AUTH_OVERRIDE_ENV_VARS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_VERTEX",
    }
)

CLAUDE_MODEL_ALIASES = ("default", "sonnet", "opus", "haiku", "fable")
LEGACY_MODEL_ALIASES = {
    "claude-max-sonnet-opus": "opus",
}
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def oauth_only_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child environment that can only use Claude.ai OAuth."""

    environment = dict(base or os.environ)
    for key in CLAUDE_AUTH_OVERRIDE_ENV_VARS:
        environment.pop(key, None)
    return environment


def normalize_model(model: str | None) -> str:
    value = str(model or "").strip()
    if not value:
        return "sonnet"
    return LEGACY_MODEL_ALIASES.get(value.lower(), value)


def build_headless_args(prompt: str, model: str | None = None, fallback_model: str | None = None) -> list[str]:
    """Build the non-interactive, OAuth-only-safe Claude Code invocation."""

    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Claude Code headless dispatch requires a prompt.")

    args = [
        "-p",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
    ]
    selected = normalize_model(model)
    if selected.lower() != "default":
        args.extend(["--model", selected])
    if fallback_model:
        args.extend(["--fallback-model", normalize_model(fallback_model)])
    args.append(clean_prompt)
    return args


def parse_headless_output(stdout: str) -> tuple[str, dict[str, Any] | None]:
    """Extract the user-facing result and structured metadata from Claude JSON."""

    cleaned = ANSI_PATTERN.sub("", str(stdout or "")).strip()
    if not cleaned:
        return "", None
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned, None
    if not isinstance(payload, dict):
        return str(payload), None
    text = payload.get("result")
    if text is None:
        text = payload.get("text")
    if text is None:
        text = payload.get("message")
    return str(text or "").strip(), payload
