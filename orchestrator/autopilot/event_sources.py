from __future__ import annotations

from pathlib import Path
from typing import Any


def decompose_file_event(path: Path, policy: dict[str, Any]) -> dict[str, Any]:
    default_intent = str(policy.get("default_intent") or "Classify this selected file and propose next action.")
    max_consequence = str(policy.get("max_consequence") or "medium")
    if max_consequence not in {"low", "medium"}:
        max_consequence = "medium"
    return {
        "source": "autopilot",
        "event_type": "file_seen",
        "path": str(path),
        "intent_text": default_intent,
        "max_consequence": max_consequence,
        "dispatch": False,
    }
