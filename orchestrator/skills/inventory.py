from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


ROUTER_HOME = Path.home() / ".codex" / "skills" / "codex-capability-router"
DEFAULT_INVENTORY_PATH = ROUTER_HOME / "references" / "skill-inventory.json"
DEFAULT_CONFLICT_RULES_PATH = ROUTER_HOME / "references" / "conflict-rules.json"
DEFAULT_BENCHMARKS_PATH = ROUTER_HOME / "references" / "skill-selection-benchmarks.json"


class SkillInventoryItem(BaseModel):
    skill_id: str
    name: str
    path: str | None = None
    description: str = ""
    primary_group: str | None = None
    secondary_groups: list[str] = Field(default_factory=list)
    tool_requirements: list[str] = Field(default_factory=list)
    authority_checks: list[str] = Field(default_factory=list)


class SkillInventory(BaseModel):
    items: list[SkillInventoryItem] = Field(default_factory=list)
    conflict_rules: dict[str, Any] = Field(default_factory=dict)
    benchmarks: dict[str, Any] = Field(default_factory=dict)
    inventory_path: str | None = None
    invalid_paths: list[str] = Field(default_factory=list)
    load_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.load_error is None and not self.invalid_paths

    def by_name(self) -> dict[str, SkillInventoryItem]:
        lookup: dict[str, SkillInventoryItem] = {}
        for item in self.items:
            lookup[item.skill_id] = item
            lookup[item.name] = item
        return lookup

    def get(self, name: str) -> SkillInventoryItem | None:
        return self.by_name().get(name)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _item_from_raw(raw: dict[str, Any]) -> SkillInventoryItem:
    skill_id = str(raw.get("skill_id") or raw.get("id") or raw.get("name") or "")
    name = str(raw.get("name") or skill_id)
    return SkillInventoryItem(
        skill_id=skill_id,
        name=name,
        path=str(raw.get("path") or "") or None,
        description=str(raw.get("description") or ""),
        primary_group=raw.get("primary_group"),
        secondary_groups=[str(item) for item in raw.get("secondary_groups") or []],
        tool_requirements=[str(item) for item in raw.get("tool_requirements") or []],
        authority_checks=[str(item) for item in raw.get("authority_checks") or []],
    )


def _raw_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("skills") or payload.get("items") or []
    return value if isinstance(value, list) else []


def load_skill_inventory(
    inventory_path: Path | None = None,
    conflict_rules_path: Path | None = None,
    benchmarks_path: Path | None = None,
) -> SkillInventory:
    inventory_path = inventory_path or DEFAULT_INVENTORY_PATH
    conflict_rules_path = conflict_rules_path or DEFAULT_CONFLICT_RULES_PATH
    benchmarks_path = benchmarks_path or DEFAULT_BENCHMARKS_PATH
    try:
        payload = _read_json(inventory_path)
    except Exception as exc:
        return SkillInventory(inventory_path=str(inventory_path), load_error=f"{inventory_path}: {exc}")

    items = [_item_from_raw(raw) for raw in _raw_items(payload)]
    invalid_paths = [
        str(item.path)
        for item in items
        if item.path and not Path(item.path).expanduser().exists()
    ]
    conflict_rules: dict[str, Any] = {}
    benchmarks: dict[str, Any] = {}
    if conflict_rules_path.exists():
        try:
            conflict_rules = _read_json(conflict_rules_path)
        except Exception as exc:
            invalid_paths.append(f"{conflict_rules_path}: {exc}")
    else:
        invalid_paths.append(str(conflict_rules_path))
    if benchmarks_path.exists():
        try:
            benchmarks = _read_json(benchmarks_path)
        except Exception as exc:
            invalid_paths.append(f"{benchmarks_path}: {exc}")
    else:
        invalid_paths.append(str(benchmarks_path))
    return SkillInventory(
        items=items,
        conflict_rules=conflict_rules,
        benchmarks=benchmarks,
        inventory_path=str(inventory_path),
        invalid_paths=invalid_paths,
    )


@lru_cache(maxsize=1)
def load_default_skill_inventory() -> SkillInventory:
    return load_skill_inventory()
