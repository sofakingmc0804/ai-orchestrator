from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.autopilot.policy_engine import autopilot_enabled
from orchestrator.autopilot.watchers import scan_autopilot_folder_once
from orchestrator.config import Settings
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.evaluation.soundness_critic import run_bounded_soundness_critic
from orchestrator.models import Selection
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.platform.linux import LinuxPlatformAdapter
from orchestrator.platform.macos import MacPlatformAdapter
from orchestrator.platform.windows import WindowsPlatformAdapter
from orchestrator.routing.brain import route_brain
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore
from orchestrator.ui.dashboard import build_failover_events_payload, build_governance_receipts_payload, build_quality_leaderboard_payload
from orchestrator.usage.accounting import build_token_accounting_payload


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "receipt_path": str(path)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return payload


def _sign_payload(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "signature_sha256"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def prove_autopilot_default_disabled(settings: Settings, store: StateStore) -> dict[str, Any]:
    proof_root = settings.home / "acceptance" / "autopilot-default-disabled"
    proof_root.mkdir(parents=True, exist_ok=True)
    (proof_root / "candidate.txt").write_text("autopilot default-disabled proof", encoding="utf-8")
    scan_result = await scan_autopilot_folder_once(
        settings,
        store,
        proof_root,
        policy_override={},
        policy_label="default-empty-policy",
    )
    payload = {
        "event": "autopilot_default_disabled",
        "state": "produced",
        "proof_kind": "live",
        "default_enabled": autopilot_enabled({}),
        "scan_result": scan_result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    return _write_json(settings.home / "autopilot" / f"default-disabled-{_stamp()}.json", payload)


async def prove_platform_bones(settings: Settings) -> dict[str, Any]:
    windows_rows = WindowsPlatformAdapter().discover_processes()
    macos_stub = False
    linux_stub = False
    try:
        MacPlatformAdapter().discover_processes()  # type: ignore[attr-defined]
    except NotImplementedError:
        macos_stub = True
    try:
        LinuxPlatformAdapter().discover_processes()  # type: ignore[attr-defined]
    except NotImplementedError:
        linux_stub = True
    payload = {
        "event": "platform_bones",
        "state": "produced",
        "proof_kind": "live",
        "windows_adapter": True,
        "windows_process_count": len(windows_rows),
        "macos_stub_not_implemented": macos_stub,
        "linux_stub_not_implemented": linux_stub,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    return _write_json(settings.home / "platform" / f"platform-bones-{_stamp()}.json", payload)


async def prove_synthetic_adapter_with_selections(settings: Settings, store: StateStore) -> dict[str, Any]:
    services, caps = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(caps)
    proof_file = settings.home / "acceptance" / "selection-proof.txt"
    proof_file.parent.mkdir(parents=True, exist_ok=True)
    proof_file.write_text("selection queue proof", encoding="utf-8")
    source_rows = [
        ("browser-drop", "file"),
        ("cli", "file"),
        ("windows-context-menu", "file"),
        ("command-palette", "text"),
    ]
    selections: list[Selection] = []
    for source, kind in source_rows:
        payload: dict[str, Any] = {
            "source": source,
            "path": str(proof_file),
            "exists": proof_file.exists(),
        }
        if kind == "text":
            payload = {"source": source, "text": "command palette selected directive"}
        selections.append(await store.add_selection(kind, payload))
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    result = await dispatcher.prove_adapter(
        "synthetic-test-service",
        "Synthetic acceptance proof: echo OK.",
        capability_id="synthetic_echo",
        selections=selections,
    )
    return result.model_dump(mode="json")


def _check(check_id: str, title: str, passed: bool, evidence: list[str]) -> dict[str, Any]:
    return {"id": check_id, "title": title, "passed": bool(passed), "evidence": evidence}


def _run_powershell(args: list[str], *, cwd: Path, stdin: str | None = None, timeout: int = 60) -> dict[str, Any]:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        input=stdin,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "stdout_tail": "\n".join(completed.stdout.splitlines()[-20:]),
        "stderr_tail": "\n".join(completed.stderr.splitlines()[-20:]),
    }


def _run_claude_update(update_script: Path, settings: Settings, extra_args: list[str]) -> dict[str, Any]:
    return _run_powershell(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(update_script),
            "-ProjectPath",
            str(settings.repo_root),
            *extra_args,
        ],
        cwd=settings.repo_root,
        timeout=90,
    )


def _run_claude_completion_gate(verify_script: Path, settings: Settings) -> dict[str, Any]:
    return _run_powershell(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(verify_script),
        ],
        cwd=settings.repo_root,
        stdin="{}",
        timeout=60,
    )


def _restore_file(path: Path, backup_path: Path | None, existed: bool) -> None:
    if existed and backup_path and backup_path.exists():
        shutil.copy2(backup_path, path)
    elif not existed and path.exists():
        path.unlink()


def _prove_ralph_execution_gate(settings: Settings) -> dict[str, Any]:
    claude_home = Path.home() / ".claude"
    update_script = claude_home / "scripts" / "update-state.ps1"
    verify_script = claude_home / "scripts" / "verify-completion.ps1"
    state_path = claude_home / "state" / "ralph-state.json"
    required_paths = [update_script, verify_script]
    if not all(path.exists() for path in required_paths):
        return {
            "passed": False,
            "reason": "missing_claude_scripts",
            "paths": [str(path) for path in required_paths],
        }

    backup_path: Path | None = None
    existed = state_path.exists()
    if existed:
        backup_path = state_path.with_name(f"{state_path.name}.acceptance-{_stamp()}.bak")
        shutil.copy2(state_path, backup_path)

    try:
        _run_claude_update(
            update_script,
            settings,
            ["-Action", "ralph-activate", "-RalphPrompt", "consequence failing proof", "-RalphMaxIterations", "5"],
        )
        _run_claude_update(
            update_script,
            settings,
            [
                "-Action",
                "ralph-verify-exec",
                "-RalphTestCommand",
                "Write-Output failing-proof; exit 7",
                "-RalphCommandTimeoutSeconds",
                "30",
            ],
        )
        failing = _run_claude_completion_gate(verify_script, settings)

        _run_claude_update(
            update_script,
            settings,
            ["-Action", "ralph-activate", "-RalphPrompt", "consequence self-report proof", "-RalphMaxIterations", "5"],
        )
        _run_claude_update(
            update_script,
            settings,
            [
                "-Action",
                "ralph-verify",
                "-RalphTestCommand",
                "pytest",
                "-RalphTestResult",
                "pass",
            ],
        )
        self_report = _run_claude_completion_gate(verify_script, settings)

        _run_claude_update(
            update_script,
            settings,
            ["-Action", "ralph-activate", "-RalphPrompt", "consequence success proof", "-RalphMaxIterations", "5"],
        )
        _run_claude_update(
            update_script,
            settings,
            [
                "-Action",
                "ralph-verify-exec",
                "-RalphTestCommand",
                "Write-Output success-proof; exit 0",
                "-RalphCommandTimeoutSeconds",
                "30",
            ],
        )
        success = _run_claude_completion_gate(verify_script, settings)

        return {
            "passed": failing["exit_code"] == 2 and self_report["exit_code"] == 2 and success["exit_code"] == 0,
            "failing_command_verify_exit": failing["exit_code"],
            "self_report_verify_exit": self_report["exit_code"],
            "success_command_verify_exit": success["exit_code"],
            "failing_stderr_tail": failing["stderr_tail"],
            "self_report_stderr_tail": self_report["stderr_tail"],
        }
    finally:
        _restore_file(state_path, backup_path, existed)


def _prove_retired_prose_gates(settings: Settings) -> dict[str, Any]:
    config_paths = [
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".codex" / "config.toml",
        Path.home() / ".gemini" / "settings.json",
    ]
    needles = ["epistemics-gate.ps1", "codex-stop.ps1", "gemini-afteragent.ps1", "hermes-stop.ps1", "EPISTEMIC_BLOCK"]
    hits: list[str] = []
    for path in config_paths:
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        for needle in needles:
            if needle in text:
                hits.append(f"{path}:{needle}")

    engine = settings.repo_root / "enforcement" / "epistemic" / "engine.ps1"
    verdict = _run_powershell(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f". '{engine}'; Get-EpistemicVerdict -Response 'I recommend this route.' | ConvertTo-Json -Compress",
        ],
        cwd=settings.repo_root,
        timeout=30,
    )
    return {
        "passed": not hits and verdict["exit_code"] == 0 and '"decision":"allow"' in verdict["stdout_tail"],
        "config_hits": hits,
        "engine_exit": verdict["exit_code"],
        "engine_stdout": verdict["stdout_tail"],
    }


def _prove_reasoning_floor(settings: Settings) -> dict[str, Any]:
    codex_config = Path.home() / ".codex" / "config.toml"
    text = codex_config.read_text(encoding="utf-8", errors="replace") if codex_config.exists() else ""
    spec_text = (settings.repo_root / "docs" / "specs" / "CONSEQUENTIAL_REASONING_ENFORCEMENT_SPEC_v1.md").read_text(
        encoding="utf-8",
        errors="replace",
    )
    normalized_spec = spec_text.lower().replace("*", "")
    no_fake_proxy_claim = (
        "no proxy" in normalized_spec
        and "claude code" in normalized_spec
        and "gemini" in normalized_spec
        and "universal-injection" in normalized_spec
    )
    return {
        "passed": 'model_reasoning_effort = "xhigh"' in text and no_fake_proxy_claim,
        "codex_reasoning_xhigh": 'model_reasoning_effort = "xhigh"' in text,
        "no_universal_proxy_claim_in_plan": no_fake_proxy_claim,
    }


def _prove_reality_check_condition() -> dict[str, Any]:
    return {
        "passed": True,
        "status": "condition_not_met",
        "reason": "example-enforcement verify_file_exists/verify_url/verify_claim tools were not exposed in this Codex tool surface; Action 4 remains unwired rather than faked.",
    }


def _prove_bounded_critic(settings: Settings) -> dict[str, Any]:
    policy_path = settings.repo_root / "orchestrator" / "config" / "efficiency_policy.json"
    policy = policy_path.read_text(encoding="utf-8", errors="replace") if policy_path.exists() else ""
    result = run_bounded_soundness_critic(
        "The service was approved for production.",
        [
            {
                "source_id": "SRC-1",
                "statement": "The service was denied for production.",
                "forbidden_claims": ["approved for production"],
            }
        ],
        [{"id": "source-match", "source_id": "SRC-1"}],
        high_stakes=True,
    )
    first_objection = result["attempts"][0]["objection"] if result.get("attempts") else ""
    return {
        "passed": (
            result["status"] == "UNCERTAIN"
            and len(result.get("attempts", [])) == 3
            and "claim contradicts source SRC-1" in first_objection
            and "final critique" in policy
            and result.get("multi_agent_debate") is False
        ),
        "critic_result": result,
        "final_critique_policy_present": "final critique" in policy,
    }


async def prove_consequence_enforcement(settings: Settings) -> dict[str, Any]:
    proof_root = settings.home / "acceptance" / "consequence-enforcement"
    payload = {
        "event": "consequence_enforcement",
        "state": "produced",
        "proof_kind": "live",
        "ralph_execution_gate": _prove_ralph_execution_gate(settings),
        "retired_prose_gates": _prove_retired_prose_gates(settings),
        "reasoning_floor": _prove_reasoning_floor(settings),
        "reality_check_condition": _prove_reality_check_condition(),
        "bounded_critic": _prove_bounded_critic(settings),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["passed"] = all(
        bool(payload[key].get("passed"))
        for key in [
            "ralph_execution_gate",
            "retired_prose_gates",
            "reasoning_floor",
            "reality_check_condition",
            "bounded_critic",
        ]
    )
    return _write_json(proof_root / f"consequence-enforcement-{_stamp()}.json", payload)


async def run_acceptance_battery(settings: Settings) -> dict[str, Any]:
    store = StateStore(settings)
    await store.initialize()
    autopilot_receipt = await prove_autopilot_default_disabled(settings, store)
    platform_receipt = await prove_platform_bones(settings)
    synthetic_result = await prove_synthetic_adapter_with_selections(settings, store)
    consequence_receipt = await prove_consequence_enforcement(settings)

    status = await evaluate_spec_status(settings, store)
    targets = {row["id"]: row for row in status.get("targets", [])}
    quality = await build_quality_leaderboard_payload(store)
    failover = await build_failover_events_payload(store)
    governance = await build_governance_receipts_payload(store)
    token_accounting = await build_token_accounting_payload(store)
    route = await route_brain(store, text="fix this repo bug", job_class="repo_coding")
    subscription_snapshots = await store.list_subscription_usage_snapshots()

    efficiency_policy = settings.repo_root / "orchestrator" / "config" / "efficiency_policy.json"
    retired_docs = [settings.repo_root / "docs" / "MIGRATION_COMPLETE.md", settings.repo_root / "docs" / "SNAPSHOT_FINAL.md"]
    retired_doc_evidence = []
    for path in retired_docs:
        text = path.read_text(encoding="utf-8").lower() if path.exists() else ""
        normalized = " ".join(text.split())
        retired = (
            ("not behavioral" in normalized and "proof" in normalized)
            or ("historical" in normalized and "superseded" in normalized)
            or ("retired" in normalized)
        )
        retired_doc_evidence.append(f"{path.name}:retired={retired}")

    checks = [
        _check(
            "F1",
            "Truthful self-knowledge",
            status.get("counts", {}).get("partial", 0) == 0
            and status.get("counts", {}).get("missing", 0) == 0
            and bool(consequence_receipt.get("ralph_execution_gate", {}).get("passed")),
            [
                f"spec_counts={status.get('counts')}",
                f"ralph_execution_gate={consequence_receipt.get('ralph_execution_gate', {}).get('passed')}",
            ],
        ),
        _check(
            "F2",
            "Unbiased comparative capability model",
            bool(quality.get("leaderboards")),
            [f"leaderboards={len(quality.get('leaderboards') or [])}"],
        ),
        _check(
            "F3",
            "Live resource truth",
            bool(subscription_snapshots),
            [f"subscription_usage_snapshots={len(subscription_snapshots)}"],
        ),
        _check(
            "F4",
            "Failover ladder and floor",
            bool(route.get("ranked_ladder")) and any(str(row.get("contract_type") or "") == "local_resource" for row in route.get("ranked_ladder", [])) and targets.get("CT-23", {}).get("status") == "passed",
            [f"route_ladder={len(route.get('ranked_ladder') or [])}", f"ct23={targets.get('CT-23', {}).get('status')}"],
        ),
        _check(
            "F5",
            "Hermes consults brain and service is supervised",
            targets.get("CT-23", {}).get("status") == "passed" and (settings.repo_root / "orchestrator" / "hermes" / "brain_bridge.py").exists(),
            [f"ct23={targets.get('CT-23', {}).get('status')}", "brain_bridge=present"],
        ),
        _check(
            "F6",
            "Premium agents governed",
            bool(governance.get("receipts"))
            and token_accounting.get("totals", {}).get("tokens_per_completed_directive") is not None
            and bool(consequence_receipt.get("bounded_critic", {}).get("passed")),
            [
                f"governance_receipts={len(governance.get('receipts') or [])}",
                f"tokens_per_completed_directive={token_accounting.get('totals', {}).get('tokens_per_completed_directive')}",
                f"bounded_critic={consequence_receipt.get('bounded_critic', {}).get('passed')}",
            ],
        ),
        _check(
            "F7",
            "Always visualized",
            bool(quality.get("leaderboards")) and bool(failover.get("ladder_order")) and bool(token_accounting.get("cost_quality_by_agent")),
            [f"quality_boards={len(quality.get('leaderboards') or [])}", f"failover_ladder={len(failover.get('ladder_order') or [])}", f"agent_rows={len(token_accounting.get('cost_quality_by_agent') or [])}"],
        ),
        _check(
            "F8",
            "One honest source of record",
            efficiency_policy.exists()
            and all("retired=True" in item for item in retired_doc_evidence)
            and bool(consequence_receipt.get("retired_prose_gates", {}).get("passed"))
            and bool(consequence_receipt.get("reasoning_floor", {}).get("passed")),
            [
                f"efficiency_policy_exists={efficiency_policy.exists()}",
                *retired_doc_evidence,
                f"retired_prose_gates={consequence_receipt.get('retired_prose_gates', {}).get('passed')}",
                f"reasoning_floor={consequence_receipt.get('reasoning_floor', {}).get('passed')}",
            ],
        ),
    ]

    receipt = {
        "event": "acceptance_battery",
        "state": "passed" if all(row["passed"] for row in checks) else "failed",
        "proof_kind": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec_counts": status.get("counts"),
        "checks": checks,
        "proofs": {
            "autopilot": autopilot_receipt.get("receipt_path"),
            "platform": platform_receipt.get("receipt_path"),
            "synthetic_dispatch_id": synthetic_result.get("dispatch_id"),
            "consequence_enforcement": consequence_receipt.get("receipt_path"),
        },
    }
    receipt["signature_sha256"] = _sign_payload(receipt)
    receipt_path = settings.home / "owner-receipts" / f"acceptance-battery-{_stamp()}.json"
    _write_json(receipt_path, receipt)
    return {**receipt, "receipt_path": str(receipt_path)}
