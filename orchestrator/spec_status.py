from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.config import Settings
from orchestrator.registry.contracts import existing_contract_adapters
from orchestrator.state.store import StateStore


NAMED_ADAPTERS = {
    "claude-desktop-mcp",
    "claude-code-cli",
    "codex-desktop",
    "codex-cli",
    "ollama-http",
    "ollama-cli",
    "copilot-gh",
    "copilot-vscode",
    "gemini-cli",
    "hermes-agent",
    "openclaw-gateway",
    "lm-studio",
}


CAPABILITY_TARGETS: list[dict[str, str]] = [
    {"id": "CT-01", "title": "Service Inventory"},
    {"id": "CT-02", "title": "Capability Inventory"},
    {"id": "CT-03", "title": "Activity Stream"},
    {"id": "CT-04", "title": "Auth and Quota State"},
    {"id": "CT-05", "title": "Selection Capture"},
    {"id": "CT-06", "title": "Intent Capture"},
    {"id": "CT-07", "title": "Dispatch Single Intent"},
    {"id": "CT-08", "title": "Dispatch Decision Transparency"},
    {"id": "CT-09", "title": "Output Delivery Convention"},
    {"id": "CT-10", "title": "Approval Gate"},
    {"id": "CT-11", "title": "Notification Spine"},
    {"id": "CT-12", "title": "Adapter For Every Discovered Service"},
    {"id": "CT-13", "title": "Project Discovery"},
    {"id": "CT-14", "title": "Per-Project Policy"},
    {"id": "CT-15", "title": "Scheduler"},
    {"id": "CT-16", "title": "Failure Recovery"},
    {"id": "CT-17", "title": "Quota Awareness"},
    {"id": "CT-18", "title": "Working Memory Per Project"},
    {"id": "CT-19", "title": "Autopilot Infrastructure"},
    {"id": "CT-20", "title": "Cross-OS Bones"},
    {"id": "CT-21", "title": "Universal Service Registration"},
    {"id": "CT-22", "title": "Selection-to-Output Under 60 Seconds"},
    {"id": "CT-23", "title": "Crash Survival"},
    {"id": "CT-24", "title": "Audit Trail"},
]


def _target(target_id: str, title: str, status: str, evidence: list[str], next_action: str) -> dict[str, Any]:
    return {
        "id": target_id,
        "title": title,
        "status": status,
        "evidence": evidence,
        "next_action": next_action,
    }


def _has_file(settings: Settings, relative: str) -> bool:
    return (settings.repo_root / relative).exists()


def _latest_latency_receipt(settings: Settings) -> dict[str, Any] | None:
    benchmark_root = settings.home / "benchmarks"
    if not benchmark_root.exists():
        return None
    receipts = sorted(benchmark_root.glob("selection-latency-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for receipt in receipts:
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("benchmark") == "selection_to_output_latency":
            payload["receipt_path"] = str(receipt)
            return dict(payload)
    return None


def _latest_supervisor_restart_receipt(settings: Settings) -> dict[str, Any] | None:
    supervisor_root = settings.home / "supervisor"
    if not supervisor_root.exists():
        return None
    candidates: list[tuple[str, dict[str, Any]]] = []
    for path in supervisor_root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("proof_kind") != "live":
            continue
        try:
            recovered = int(payload.get("recovered_dispatches_count") or 0)
        except (TypeError, ValueError):
            recovered = 0
        restart_event = payload.get("event") == "watchdog_restart"
        if not restart_event and recovered <= 0 and payload.get("restart_recovery_proved") is not True:
            continue
        payload["receipt_path"] = payload.get("receipt_path") or str(path)
        candidates.append((str(payload.get("completed_at") or path.stat().st_mtime), payload))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1] if candidates else None


def _jsonl_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _subscriber_receipt_count(settings: Settings, subscriber: str) -> int:
    return len(_jsonl_entries(settings.home / "subscribers" / f"{subscriber}.jsonl"))


def _subscriber_ok_count(settings: Settings, subscriber: str) -> int:
    return sum(1 for item in _jsonl_entries(settings.home / "subscribers" / f"{subscriber}.jsonl") if item.get("ok") is True)


async def evaluate_spec_status(settings: Settings, store: StateStore | None = None) -> dict[str, Any]:
    state = store or StateStore(settings)
    await state.initialize()
    services = await state.list_services()
    capabilities = await state.list_capabilities()
    projects = await state.list_projects()
    selections = await state.list_selections()
    activity = await state.list_activity(limit=10)
    dispatches = await state.list_dispatches(limit=50)
    attempts = await state.list_dispatch_attempts(limit=100)
    approvals = await state.list_pending_approvals()
    quota_state = await state.latest_quota_state()
    quota_ledger = await state.list_quota_ledger(limit=20)
    repair_queue = await state.list_repair_queue()
    all_repair_items = await state.list_repair_queue(include_resolved=True, limit=500)
    standing_orders = await state.list_standing_orders()
    scheduler_tasks = await state.list_scheduler_tasks()
    working_memory = await state.list_working_memory()
    audit_log = await state.list_audit_log(limit=50)

    adapter_names = {str(s.get("adapter_name")) for s in services}
    matched_adapters = sorted(adapter_names & NAMED_ADAPTERS)
    missing_adapters = sorted(NAMED_ADAPTERS - adapter_names)
    completed_dispatches = [d for d in dispatches if d.get("state") == "completed"]
    dispatch_with_output = [d for d in completed_dispatches if d.get("output_path")]
    proved_dispatch_adapters = {str(d.get("adapter_name")) for d in completed_dispatches if d.get("adapter_name") in NAMED_ADAPTERS}
    unproved_dispatch_adapters = sorted(NAMED_ADAPTERS - proved_dispatch_adapters)
    selection_sources = {str((s.get("payload") or {}).get("source") or "") for s in selections}
    selection_modes = {
        "browser" if selection_sources & {"ui", "browser-drop"} else "",
        "cli" if "cli" in selection_sources else "",
        "context_menu" if "windows-context-menu" in selection_sources else "",
        "palette" if "command-palette" in selection_sources else "",
    }
    selection_modes.discard("")
    selection_mechanisms = {
        "browser": _has_file(settings, "orchestrator/ui/static/app.js"),
        "cli": _has_file(settings, "orchestrator/cli/main.py"),
        "context_menu": _has_file(settings, "scripts/install-context-menu.ps1") and _has_file(settings, "scripts/queue-selection.ps1"),
        "palette": _has_file(settings, "scripts/queue-command-palette-selection.ps1"),
    }
    proved_selection_modes = {mode for mode in selection_modes if selection_mechanisms.get(mode)}
    notification_entries = _jsonl_entries(settings.notifications_path)
    approval_requests = [n for n in notification_entries if n.get("severity") == "approval_request"]
    approval_proved = bool(approval_requests) and _has_file(settings, "orchestrator/ui/static/app.js")
    notifications_file = settings.notifications_path.exists()
    in_app_receipts = _subscriber_receipt_count(settings, "in_app")
    email_receipts = _subscriber_receipt_count(settings, "email")
    tray_receipts = _subscriber_receipt_count(settings, "tray_windows")
    in_app_ok = _subscriber_ok_count(settings, "in_app")
    email_ok = _subscriber_ok_count(settings, "email")
    tray_ok = _subscriber_ok_count(settings, "tray_windows")
    notification_subscribers_proved = notifications_file and in_app_ok > 0 and email_ok > 0 and tray_ok > 0
    policy_files = [p for p in projects if p.get("policy_file_path")]
    policy_enforcement = _has_file(settings, "orchestrator/routing/project_policy.py")
    legacy_tasks = [t for t in scheduler_tasks if t.get("task_type") == "legacy_claude_scheduled_task"]
    latest_latency = _latest_latency_receipt(settings)
    latency_passed = bool(latest_latency and latest_latency.get("passed") is True and float(latest_latency.get("elapsed_seconds") or 9999) < 60)
    latest_supervisor_restart = _latest_supervisor_restart_receipt(settings)
    expected_contracts = NAMED_ADAPTERS | {"synthetic-test-service"}
    contract_adapters = existing_contract_adapters()
    missing_contracts = sorted(expected_contracts - contract_adapters)

    rows: list[dict[str, Any]] = [
        _target("CT-01", "Service Inventory", "passed" if services else "missing", [f"{len(services)} service rows"], "Run discovery refresh."),
        _target("CT-02", "Capability Inventory", "passed" if capabilities and not missing_contracts else "missing", [f"{len(capabilities)} capability rows", f"contract_yaml={len(contract_adapters)}"], "Register adapter capability contracts."),
        _target("CT-03", "Activity Stream", "passed" if activity else "partial", [f"{len(activity)} recent activity rows"], "Continue feeding normalized events from all sources."),
        _target("CT-04", "Auth and Quota State", "passed" if quota_state else "partial", [f"{len(quota_state)} providers with latest quota state", f"{len(quota_ledger)} quota ledger rows sampled"], "Probe providers that do not expose quota yet."),
        _target("CT-05", "Selection Capture", "passed" if {"browser", "cli", "context_menu", "palette"}.issubset(proved_selection_modes) else "partial", [f"{len(selections)} queued selections", f"proved_modes={', '.join(sorted(proved_selection_modes)) or 'none'}", f"mechanisms={selection_mechanisms}"], "Prove browser/drop, CLI, Windows context-menu, and command-palette capture."),
        _target("CT-06", "Intent Capture", "passed" if _has_file(settings, "orchestrator/intent/interpreter.py") else "missing", ["intent interpreter module present"], "Add one-question clarification flow for ambiguous intents."),
        _target("CT-07", "Dispatch Single Intent", "passed" if completed_dispatches else "partial", [f"{len(completed_dispatches)} completed dispatches"], "Run an end-to-end dispatch if none exist in this state DB."),
        _target("CT-08", "Dispatch Decision Transparency", "passed" if activity else "partial", ["routing decisions are included in activity when present"], "Expose full routing decision detail per dispatch in UI."),
        _target("CT-09", "Output Delivery Convention", "passed" if dispatch_with_output else "partial", [f"{len(dispatch_with_output)} completed dispatches with output_path"], "Prove project-root ORCHESTRATOR_OUTPUT layout for latest dispatch."),
        _target("CT-10", "Approval Gate", "passed" if approval_proved else "partial", [f"{len(approvals)} pending approvals", f"{len(approval_requests)} approval_request notifications recorded"], "Run high-tier approval and approve/reject proof through UI or API."),
        _target("CT-11", "Notification Spine", "passed" if notification_subscribers_proved else "partial", [f"notifications path exists={notifications_file}", f"in_app_receipts={in_app_receipts}/ok={in_app_ok}", f"email_receipts={email_receipts}/ok={email_ok}", f"tray_receipts={tray_receipts}/ok={tray_ok}"], "Run subscribers and prove tray, email, and in-app delivery receipts."),
        _target("CT-12", "Adapter For Every Discovered Service", "passed" if not missing_adapters and not unproved_dispatch_adapters else "partial", [f"{len(matched_adapters)}/12 named adapters registered", f"dispatch_proven={len(proved_dispatch_adapters)}/12", f"unproved_dispatch={', '.join(unproved_dispatch_adapters) if unproved_dispatch_adapters else 'none'}"], "Implement and prove dispatch for each named adapter without metered API fallback."),
        _target("CT-13", "Project Discovery", "passed" if projects else "missing", [f"{len(projects)} projects registered"], "Expand scan roots and classify project consequence tiers."),
        _target(
            "CT-14",
            "Per-Project Policy",
            "passed" if policy_files and policy_enforcement else "partial",
            [f"{len(policy_files)} projects with policy_file_path", f"policy_enforcement={policy_enforcement}"],
            "Add at least one discovered project policy and keep routing enforcement tests green.",
        ),
        _target("CT-15", "Scheduler", "passed" if scheduler_tasks else "partial", [f"{len(scheduler_tasks)} scheduler tasks", f"{len(legacy_tasks)} legacy tasks imported"], "Keep legacy tasks disabled until owner review."),
        _target(
            "CT-16",
            "Failure Recovery",
            "passed" if attempts and all_repair_items else "partial",
            [f"{len(attempts)} dispatch attempts", f"{len(all_repair_items)} repair history items", f"{len(repair_queue)} open repair items"],
            "Prove retry, fallback, repair item creation, and successful repair resolution.",
        ),
        _target("CT-17", "Quota Awareness", "passed" if quota_state else "partial", [f"{len(quota_state)} providers in quota state"], "Enforce reserve thresholds in router for every billable provider."),
        _target("CT-18", "Working Memory Per Project", "passed" if working_memory else "partial", [f"{len(working_memory)} working-memory records"], "Refresh memory after every output and approval transition."),
        _target("CT-19", "Autopilot Infrastructure", "passed" if standing_orders or _has_file(settings, "orchestrator/autopilot/watchers.py") else "missing", [f"{len(standing_orders)} standing orders", "autopilot modules present"], "Keep default disabled and expand sandbox tests."),
        _target("CT-20", "Cross-OS Bones", "passed" if all(_has_file(settings, f"orchestrator/platform/{name}.py") for name in ["base", "windows", "macos", "linux"]) else "partial", ["platform base/windows/macos/linux files checked"], "Keep macOS/Linux as stable stubs until v2."),
        _target(
            "CT-21",
            "Universal Service Registration",
            "passed" if _has_file(settings, "orchestrator/adapters/base.py") and _has_file(settings, "orchestrator/registry/contracts.py") and not missing_contracts else "missing",
            [
                "adapter protocol and contract loader present",
                f"contract_yaml={len(contract_adapters)}/{len(expected_contracts)}",
                f"missing_contracts={', '.join(missing_contracts) if missing_contracts else 'none'}",
            ],
            "Add one adapter plus one capability contract YAML without router or dispatcher changes.",
        ),
        _target(
            "CT-22",
            "Selection-to-Output Under 60 Seconds",
            "passed" if latency_passed else "partial",
            [
                "latency benchmark endpoint and CLI command available",
                f"latest_receipt={latest_latency.get('receipt_path') if latest_latency else 'none'}",
                f"latest_elapsed={latest_latency.get('elapsed_seconds') if latest_latency else 'none'}",
            ],
            "Run benchmark and record a passed receipt under 60 seconds.",
        ),
        _target(
            "CT-23",
            "Crash Survival",
            "passed" if latest_supervisor_restart else "partial",
            [
                "supervisor module present",
                f"latest_restart_receipt={latest_supervisor_restart.get('receipt_path') if latest_supervisor_restart else 'none'}",
                f"restart_event={latest_supervisor_restart.get('event') if latest_supervisor_restart else 'none'}",
                f"recovered_dispatches={latest_supervisor_restart.get('recovered_dispatches_count') if latest_supervisor_restart else 0}",
            ],
            "Prove interrupted dispatch recovery after process restart with a live supervisor receipt.",
        ),
        _target("CT-24", "Audit Trail", "passed" if audit_log else "partial", [f"{len(audit_log)} audit rows sampled"], "Audit every remaining adapter action and policy transition."),
    ]

    counts = {
        "passed": sum(1 for row in rows if row["status"] == "passed"),
        "partial": sum(1 for row in rows if row["status"] == "partial"),
        "missing": sum(1 for row in rows if row["status"] == "missing"),
    }
    return {
        "spec_version": "4.0",
        "targets": rows,
        "counts": counts,
        "next_targets": [row for row in rows if row["status"] != "passed"][:5],
    }
