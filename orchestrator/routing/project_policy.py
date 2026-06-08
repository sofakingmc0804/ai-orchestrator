from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


DEFAULT_APPROVAL_TIER = "high"
TIER_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def load_project_policy(path: Path | str | None) -> dict[str, Any]:
    if not path:
        return {}
    policy_path = Path(path).expanduser()
    if not policy_path.exists():
        return {}
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return normalize_project_policy(data, policy_path)


def normalize_project_policy(policy: dict[str, Any], policy_path: Path | None = None) -> dict[str, Any]:
    routing_raw = policy.get("routing")
    approval_raw = policy.get("approval")
    output_raw = policy.get("output")
    routing: dict[str, Any] = routing_raw if isinstance(routing_raw, dict) else {}
    approval: dict[str, Any] = approval_raw if isinstance(approval_raw, dict) else {}
    output: dict[str, Any] = output_raw if isinstance(output_raw, dict) else {}
    approval_required_at = str(approval.get("required_at") or policy.get("approval_required_at") or DEFAULT_APPROVAL_TIER).lower()
    if approval_required_at not in TIER_RANK:
        approval_required_at = DEFAULT_APPROVAL_TIER
    result: dict[str, Any] = {
        "policy_file_path": str(policy_path) if policy_path else None,
        "allowed_adapters": _string_list(routing.get("allowed_adapters") or policy.get("allowed_adapters")),
        "forbidden_adapters": _string_list(routing.get("forbidden_adapters") or policy.get("forbidden_adapters")),
        "preferred_adapters": _string_list(routing.get("preferred_adapters") or policy.get("preferred_adapters")),
        "approval_required_at": approval_required_at,
        "output_dir": str(output.get("directory") or policy.get("output_dir") or "").strip(),
    }
    return result


def approval_required_for_tier(consequence_tier: str, policy: dict[str, Any] | None = None) -> bool:
    threshold = str((policy or {}).get("approval_required_at") or DEFAULT_APPROVAL_TIER).lower()
    if threshold not in TIER_RANK:
        threshold = DEFAULT_APPROVAL_TIER
    return TIER_RANK.get(consequence_tier, 99) >= TIER_RANK[threshold]


def output_base_for_project(project_root: Path | None, settings_home: Path, output_dirname: str, policy: dict[str, Any] | None = None) -> Path:
    override = str((policy or {}).get("output_dir") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute() and project_root is not None:
            return project_root / candidate
        return candidate
    return (project_root or settings_home) / output_dirname
