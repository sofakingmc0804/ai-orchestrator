from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


def autopilot_enabled(policy: dict[str, object] | None = None) -> bool:
    policy = policy or {}
    return bool(policy.get("autopilot") == "enabled" or policy.get("autopilot_enabled") is True)


def load_policy(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def parse_policy_yaml(policy_yaml: str) -> dict[str, Any]:
    data = yaml.safe_load(policy_yaml) or {}
    return data if isinstance(data, dict) else {}


def policy_for_folder(folder: Path) -> tuple[dict[str, Any], Path | None]:
    policy_path = folder / ".orchestrator-policy.yaml"
    policy = load_policy(policy_path)
    return policy, policy_path if policy_path.exists() else None


def standing_order_from_policy(folder: Path, policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "folder_path": str(folder),
        "enabled": autopilot_enabled(policy),
        "max_consequence": str(policy.get("max_consequence") or "medium"),
        "default_intent": str(policy.get("default_intent") or "Classify this selected file and propose next action."),
    }
