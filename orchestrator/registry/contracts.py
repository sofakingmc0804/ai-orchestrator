from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from orchestrator.models import BillingClass, Capability, ConsequenceTier


CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "adapters"


def contract_slug(adapter_name: str) -> str:
    return adapter_name.replace("-", "_")


def contract_path(adapter_name: str) -> Path:
    return CONTRACT_ROOT / contract_slug(adapter_name) / "contract.yaml"


def load_contract(adapter_name: str) -> dict[str, Any] | None:
    path = contract_path(adapter_name)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else None


def capabilities_from_contract(adapter_name: str) -> list[Capability] | None:
    contract = load_contract(adapter_name)
    if not contract:
        return None
    rows = contract.get("capabilities")
    if not isinstance(rows, list):
        return None
    billing_class = BillingClass(str(contract.get("billing_class") or BillingClass.UNKNOWN_COST.value))
    default_latency = str(contract.get("latency_band") or "medium")
    default_consequence = ConsequenceTier(str(contract.get("consequence_max") or ConsequenceTier.MEDIUM.value))
    provider = str(contract.get("provider") or "")
    capabilities: list[Capability] = []
    for row in rows:
        if isinstance(row, str):
            item: dict[str, Any] = {"id": row}
        elif isinstance(row, dict):
            item = row
        else:
            continue
        capability_id = str(item.get("id") or item.get("capability_id") or "").strip()
        if not capability_id:
            continue
        capabilities.append(
            Capability(
                id=f"{adapter_name}:{capability_id}",
                adapter_name=adapter_name,
                capability_id=capability_id,
                rating_instruction=int(item.get("rating_instruction") or contract.get("rating_instruction") or 3),
                rating_quality=int(item.get("rating_quality") or contract.get("rating_quality") or 3),
                latency_band=str(item.get("latency_band") or default_latency),
                consequence_max=ConsequenceTier(str(item.get("consequence_max") or default_consequence.value)),
                billing_class=BillingClass(str(item.get("billing_class") or billing_class.value)),
                provider=provider,
                enabled=bool(item.get("enabled", True)),
            )
        )
    return capabilities


def existing_contract_adapters() -> set[str]:
    return {
        str(data.get("adapter"))
        for path in CONTRACT_ROOT.glob("*/contract.yaml")
        if (data := yaml.safe_load(path.read_text(encoding="utf-8")) or {}) and isinstance(data, dict) and data.get("adapter")
    }
