from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from orchestrator.config import Settings
from orchestrator.registry.contracts import load_contract
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore


def _iso_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _receipt_path_from_output(output_path: str | None) -> str:
    if not output_path:
        return "none"
    path = Path(output_path)
    candidate = path.with_name("receipt.json")
    return str(candidate if candidate.exists() else path)


def _fmt_quota(provider: str, billing_class: str, quota_state: dict[str, dict[str, Any]]) -> str:
    quota = quota_state.get(provider)
    if quota:
        limit = int(quota.get("units_limit") or 0)
        remaining = int(quota.get("remaining") or 0)
        consumed = int(quota.get("units_consumed") or 0)
        percent = quota.get("percent_remaining")
        pct_text = f"{float(percent):.1f}%" if percent is not None else "unknown %"
        return f"{remaining}/{limit} remaining ({pct_text}; consumed {consumed})"
    if billing_class == "local_resource":
        return "local resource; no external quota"
    if billing_class == "subscription_unlimited":
        return "fixed subscription; no scarce quota recorded"
    if billing_class == "subscription_quota":
        return "subscription quota; no live probe recorded"
    if billing_class == "metered_extra_cost":
        return "metered extra cost; forbidden default"
    return "unknown cost; disabled until classified"


async def _latest_receipts(settings: Settings) -> dict[str, dict[str, Any]]:
    query = """
        SELECT r.service, r.capability, r.model, r.cost_class, d.output_path, d.completed_at, r.full_receipt
        FROM receipts r
        JOIN dispatches d ON d.id = r.dispatch_id
        WHERE r.success = 1
        ORDER BY d.completed_at DESC
    """
    latest: dict[str, dict[str, Any]] = {}
    async with aiosqlite.connect(settings.state_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(query)).fetchall()
    for row in rows:
        item = dict(row)
        service = str(item.get("service") or "")
        if service and service not in latest:
            latest[service] = item
    return latest


async def export_owner_receipt(settings: Settings) -> dict[str, Any]:
    store = StateStore(settings)
    await store.initialize()
    status = await evaluate_spec_status(settings, store)
    services = await store.list_services()
    capabilities = await store.list_capabilities()
    quota_state = await store.latest_quota_state()
    repair_queue = await store.list_repair_queue()
    receipts = await _latest_receipts(settings)

    caps_by_adapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for capability in capabilities:
        caps_by_adapter[str(capability.get("adapter_name") or "")].append(capability)

    cost_counter: Counter[str] = Counter()
    service_rows: list[dict[str, str]] = []
    for service in services:
        adapter = str(service.get("adapter_name") or "")
        contract = load_contract(adapter) or {}
        provider = str(contract.get("provider") or adapter)
        adapter_caps = caps_by_adapter.get(adapter, [])
        billing_classes = sorted({str(row.get("billing_class") or "unknown_cost") for row in adapter_caps})
        billing_class = str(contract.get("billing_class") or (billing_classes[0] if billing_classes else "unknown_cost"))
        cost_counter[billing_class] += 1
        proof = receipts.get(adapter, {})
        capability_names = ", ".join(sorted({str(row.get("capability_id")) for row in adapter_caps if row.get("capability_id")}))
        service_rows.append(
            {
                "provider": provider,
                "adapter": adapter,
                "cost": billing_class,
                "health": str(service.get("health_state") or "unknown"),
                "capabilities": capability_names or "none",
                "quota": _fmt_quota(provider, billing_class, quota_state),
                "proof": _receipt_path_from_output(str(proof.get("output_path") or "")),
            }
        )

    spec_counts = status.get("counts", {})
    receipt_dir = settings.home / "owner-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"AI_ORCHESTRATOR_OWNER_RECEIPT_{_iso_slug()}.md"
    lines = [
        "# AI Orchestrator Owner Receipt",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Runtime: `{settings.home}`",
        f"State DB: `{settings.state_path}`",
        "",
        "## Acceptance",
        "",
        f"- Spec status: `{spec_counts.get('passed', 0)} passed`, `{spec_counts.get('partial', 0)} partial`, `{spec_counts.get('missing', 0)} missing`.",
        f"- Services: `{len(services)}`. Capabilities: `{len(capabilities)}`. Open repairs: `{len(repair_queue)}`.",
        f"- Cost-class mix: `{dict(sorted(cost_counter.items()))}`.",
        "- Guardrail: metered and unknown-cost providers are not default routes; local/resource-cheap work remains first.",
        "",
        "## Providers",
        "",
        "| Provider | Adapter | Cost class | Health | Quota posture | Latest proof |",
        "|---|---|---|---|---|---|",
    ]
    for row in sorted(service_rows, key=lambda item: (item["cost"], item["provider"], item["adapter"])):
        lines.append(
            f"| {row['provider']} | `{row['adapter']}` | `{row['cost']}` | `{row['health']}` | {row['quota']} | `{row['proof']}` |"
        )
    lines.extend(
        [
            "",
            "## Road Through",
            "",
            "- Keep bulk extraction, classification, embeddings, OCR, and cheap validation on local-resource routes.",
            "- Spend subscription quota only when the receipt names a value reason and the reserve policy allows it.",
            "- Treat any missing proof path, open repair, unknown cost, or metered default as a repair trigger before dispatch.",
        ]
    )
    receipt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    await store.audit(
        "owner_receipt",
        "exported",
        str(receipt_path),
        {
            "spec_counts": spec_counts,
            "services": len(services),
            "capabilities": len(capabilities),
            "open_repairs": len(repair_queue),
            "cost_classes": dict(cost_counter),
        },
    )
    return {
        "receipt_path": str(receipt_path),
        "spec_counts": spec_counts,
        "services": len(services),
        "capabilities": len(capabilities),
        "open_repairs": len(repair_queue),
        "cost_classes": dict(cost_counter),
    }
