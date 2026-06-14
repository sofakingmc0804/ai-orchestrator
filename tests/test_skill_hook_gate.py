from __future__ import annotations

import os
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.cli.main import _skill_hook_plan
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.models import BillingClass, Capability, ConsequenceTier, ServiceInfo
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.skills.detector import detect_skill_route
from orchestrator.skills.gate import handle_permission_request, handle_pre_tool_use, handle_stop, handle_user_prompt_submit
from orchestrator.skills.models import AuthorityCheck
from orchestrator.skills.runtime import persist_plan, run_hook_event
from orchestrator.state.store import StateStore


def _skill_names(plan: Any) -> set[str]:
    return {skill.name for skill in plan.selected_skills}


def _ambient_suggestion_prompt() -> str:
    return """
# Overview

Generate 0 to 3 hyperpersonalized suggestions for what this user can do with Codex in this local project:
C:\\Users\\Couch\\dev\\ai-orchestrator

Use relevant connected apps or MCP sources available in this session.
For local project suggestions, inspect recent git history so each suggestion is grounded in the repo.
Avoid suggestions that merely ask the user to work on the thing.

Recent Codex threads in this project:
[
  {
    "title": "Confirm route shp_709358f3764b4d7b",
    "preview": "confirm route shp_709358f3764b4d7b"
  },
  {
    "title": "Assess ai-orchestrator gaps",
    "preview": "What skills should you be using to improve the ai-orchestrator? What is it not accomplishing?"
  },
  {
    "title": "Fix health status drift",
    "preview": "Trace how status is computed, fix the smallest shared mechanism, and add tests."
  }
]

Bad title: "Debug nightly query devtools reopen"
Better title: "Fix nightly query devtools not opening by resetting Electron state"

Return 0 to 3 fresh suggestions.
""".strip()


def _approved_desktop_plan(cwd: Path, *, expires_delta: timedelta = timedelta(minutes=10)) -> Any:
    plan = detect_skill_route("use my logged-in Chrome tab to inspect the page", cwd=cwd)
    checks = [
        AuthorityCheck(
            name="desktop_control_lease",
            status="passed",
            reason="User approved visible desktop or Chrome control for this pending task.",
            expires_at=(datetime.now(timezone.utc) + expires_delta).isoformat(),
            scope_cwd=str(cwd.resolve()),
        )
        if check.name == "desktop_control_lease"
        else check
        for check in plan.authority_checks
    ]
    return plan.model_copy(update={"authority_checks": checks})


def test_code_repair_prompt_selects_development_skills() -> None:
    plan = detect_skill_route("fix this repo bug and add tests", cwd=Path.cwd())

    assert {"codex-capability-router", "superpowers:test-driven-development", "superpowers:verification-before-completion"} <= _skill_names(plan)
    assert plan.domain == "code"
    assert plan.interpreted_actions[0].mutates is True
    assert plan.confirmation_state == "not_required"
    assert plan.terminal_state_requirement == "built"


def test_codex_hook_prompt_selects_openai_docs() -> None:
    plan = detect_skill_route("Explain how Codex hooks should enforce skill activation", cwd=Path.cwd())

    assert "openai-docs" in _skill_names(plan)
    assert "codex-capability-router" in _skill_names(plan)
    assert plan.domain == "codex"


def test_codex_hook_repair_prompt_selects_development_gates() -> None:
    plan = detect_skill_route(
        "Execute road 2 and repair the Codex hooks so future browser abilities are not blocked.",
        cwd=Path.cwd(),
    )

    assert "openai-docs" in _skill_names(plan)
    assert "superpowers:test-driven-development" in _skill_names(plan)
    assert "superpowers:verification-before-completion" in _skill_names(plan)
    assert plan.interpreted_actions[0].mutates is True


def test_ambient_suggestion_prompt_is_read_only_discovery_without_confirmation() -> None:
    plan = detect_skill_route(_ambient_suggestion_prompt(), cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    action = plan.interpreted_actions[0]
    checks = {check.name: check for check in plan.authority_checks}
    context = decision["hookSpecificOutput"]["additionalContext"]

    assert action.mutates is False
    assert plan.consequence == "low"
    assert plan.confirmation_state == "not_required"
    assert "openai-docs" not in _skill_names(plan)
    assert "superpowers:systematic-debugging" not in _skill_names(plan)
    assert checks["connected_app_tool_proof"].status == "required"
    assert "omitted large prompt" in context.lower()
    assert "confirm route" not in context.lower()


@pytest.mark.parametrize(
    ("prompt", "expected_skill", "expected_domain"),
    [
        ("Open localhost:3000 and tell me whether the page renders correctly.", "browser-use:browser", "browser"),
        ("Run a deterministic browser regression check and capture a screenshot for the local app.", "playwright", "browser"),
        ("Update the connected Google Sheet and verify the changed range.", "google-drive:google-sheets", "connected_app"),
        ("Search Gmail for the latest thread from Balluff and extract the action item.", "gmail:gmail", "connected_app"),
        ("Resize this Canva design for LinkedIn and Instagram.", "canva:canva-resize-for-all-social-media", "connected_app"),
    ],
)
def test_router_benchmark_child_skill_prompts_select_expected_skill(prompt: str, expected_skill: str, expected_domain: str) -> None:
    plan = detect_skill_route(prompt, cwd=Path.cwd())

    assert expected_skill in _skill_names(plan)
    assert plan.domain == expected_domain


def test_medium_confidence_prompt_injects_single_confirmation_question() -> None:
    plan = detect_skill_route("work on the thing", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": "work on the thing", "cwd": str(Path.cwd())}, plan)

    assert plan.confirmation_state == "required"
    assert "decision" not in decision
    context = decision["hookSpecificOutput"]["additionalContext"]
    assert context.count("?") == 1
    assert "confirm route" in context.lower()
    assert "use skills:" in context.lower()


def test_medium_confidence_non_ambiguous_prompt_does_not_request_confirmation() -> None:
    plan = detect_skill_route("continue", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert plan.confirmation_state == "not_required"
    assert "confirm route" not in context.lower()
    assert "use skills:" not in context.lower()


def test_visible_desktop_prompt_is_managed_without_confirmation_route() -> None:
    plan = detect_skill_route("use my logged-in Chrome tab to inspect the page", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert plan.confirmation_state == "not_required"
    checks = {check.name: check for check in plan.authority_checks}
    assert checks["desktop_control_lease"].status == "required"
    assert "background-first" in context.lower()
    assert "confirm route" not in context.lower()
    assert "forbidden" not in context.lower()


def test_obvious_code_repair_prompt_injects_route_without_confirmation_prompt() -> None:
    plan = detect_skill_route("fix this repo bug and add tests", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert "decision" not in decision
    assert plan.id in context
    assert "superpowers:test-driven-development" in context
    assert "superpowers:verification-before-completion" in context
    assert "confirm route" not in context.lower()
    assert "use skills:" not in context.lower()
    assert "cancel route" not in context.lower()


def test_external_mutation_still_requires_confirmation() -> None:
    plan = detect_skill_route("send this Gmail reply to the customer", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert plan.confirmation_state == "required"
    assert "confirm route" in context.lower()
    assert "gmail:gmail" in context


def test_forbidden_metered_route_prompt_is_hard_denied() -> None:
    plan = detect_skill_route("Route this through a metered OpenAI API provider for dispatch.", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    output = decision["hookSpecificOutput"]
    assert plan.enforcement_mode == "hard_deny"
    assert decision["systemMessage"].startswith("Hard deny by owner ruleset")
    assert output["hookEventName"] == "UserPromptSubmit"
    assert output["decision"]["behavior"] == "deny"
    assert output["permissionDecision"] == "deny"
    assert "metered" in output["permissionDecisionReason"].lower()


def test_repo_hook_entrypoint_emits_hard_deny(tmp_path: Path) -> None:
    event = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "hard-deny-subprocess",
        "turn_id": "turn-1",
        "cwd": str(Path.cwd()),
        "prompt": "Route this through a metered OpenAI API provider for dispatch.",
    }
    env = {**os.environ, "ORCHESTRATOR_HOME": str(tmp_path / ".orchestrator")}
    result = subprocess.run(
        [sys.executable, ".codex/hooks/skill_gate_repo.py"],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert payload["systemMessage"].startswith("Hard deny by owner ruleset")


def test_efficiency_only_violation_is_advisory_not_blocked() -> None:
    plan = detect_skill_route("fix this repo bug and add tests", cwd=Path.cwd())
    plan = plan.model_copy(
        update={
            "selected_skills": [
                skill
                for skill in plan.selected_skills
                if skill.name not in {"superpowers:test-driven-development", "superpowers:verification-before-completion"}
            ]
        }
    )
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert plan.enforcement_mode == "advisory"
    assert "systemMessage" not in decision
    assert "permissionDecision" not in output
    assert "missing test-driven-development" in output["additionalContext"].lower()


def test_local_repo_cleanup_does_not_require_route_confirmation() -> None:
    plan = detect_skill_route("delete generated files in this repo", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert plan.confirmation_state == "not_required"
    assert "superpowers:test-driven-development" in context
    assert "confirm route" not in context.lower()


def test_govcon_local_build_cleanup_prompt_does_not_require_route_confirmation() -> None:
    prompt = (
        "Inspect the public-monitor promotions and the bid package folders created around 2026-06-12 06:20 AM. "
        "Trace why the folders received the same PDF and Unknown Solicitation summary, remove or quarantine the "
        "contaminated packages, repair the document-fetch/package-build path, and prove the fix with a validation receipt. "
        "Authority: GovCon AGENTS.md, README.md, live pipeline/docreader/dashboard code, state/opportunities.json, "
        "DIBBS RFQ public PDFs."
    )
    plan = detect_skill_route(prompt, cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert plan.confirmation_state == "not_required"
    assert "superpowers:test-driven-development" in context
    assert "confirm route" not in context.lower()


def test_broad_destructive_machine_prompt_still_requires_confirmation() -> None:
    plan = detect_skill_route("wipe the C:\\ drive and reset the machine", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert plan.confirmation_state == "required"
    assert "confirm route" in context.lower()


def test_destructive_prompt_does_not_borrow_suggestion_discovery_exemption() -> None:
    plan = detect_skill_route("Generate 0 to 3 suggestions, then wipe the C:\\ drive and reset the machine.", cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": plan.prompt, "cwd": str(Path.cwd())}, plan)

    context = decision["hookSpecificOutput"]["additionalContext"]

    assert plan.interpreted_actions[0].mutates is True
    assert plan.confirmation_state == "required"
    assert "confirm route" in context.lower()


def test_confirmation_reply_proceeds_and_marks_confirmed() -> None:
    prompt = "Yes, that skill route is correct: use repo coding skills to fix the bug."
    plan = detect_skill_route(prompt, cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(Path.cwd())}, plan)

    assert plan.confirmation_state == "confirmed"
    assert "decision" not in decision
    assert decision["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "selected skills" in decision["hookSpecificOutput"]["additionalContext"].lower()


def test_short_confirmation_reply_proceeds_and_marks_confirmed() -> None:
    prompt = "Yes, confirm that route."
    plan = detect_skill_route(prompt, cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(Path.cwd())}, plan)

    assert plan.confirmation_state == "confirmed"
    assert "decision" not in decision


def test_user_prompt_submit_emits_valid_additional_context() -> None:
    prompt = "Tell me what this repository is for."
    plan = detect_skill_route(prompt, cwd=Path.cwd())
    decision = handle_user_prompt_submit({"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(Path.cwd())}, plan)

    assert "decision" not in decision
    assert decision["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "terminal state" in decision["hookSpecificOutput"]["additionalContext"].lower()


def test_pre_tool_use_advises_visible_desktop_command_without_rewriting() -> None:
    plan = detect_skill_route("inspect my logged-in Chrome tab", cwd=Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "updatedInput" not in output
    assert "visible desktop" in output["additionalContext"].lower()


def test_pre_tool_use_does_not_rewrite_text_search_that_mentions_chrome() -> None:
    plan = detect_skill_route("inspect the policy text", cwd=Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rg -n \"Chrome\" AGENTS.md"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output


def test_pre_tool_use_does_not_rewrite_text_search_that_mentions_chrome_skill() -> None:
    plan = detect_skill_route("inspect the policy text", cwd=Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": 'rg -n "control-chrome" AGENTS.md'},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output


def test_pre_tool_use_advises_unplanned_visible_desktop_command_without_rewriting() -> None:
    plan = detect_skill_route("inspect the policy text", cwd=Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "updatedInput" not in output
    assert "visible desktop" in output["additionalContext"].lower()


def test_pre_tool_use_advises_expired_desktop_control_lease_without_rewriting() -> None:
    plan = _approved_desktop_plan(Path.cwd(), expires_delta=timedelta(minutes=-1))
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(Path.cwd()),
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "updatedInput" not in output
    assert "visible desktop" in output["additionalContext"].lower()


def test_pre_tool_use_advises_desktop_control_lease_for_wrong_cwd_without_rewriting(tmp_path: Path) -> None:
    approved_cwd = tmp_path / "approved"
    other_cwd = tmp_path / "other"
    approved_cwd.mkdir()
    other_cwd.mkdir()
    plan = _approved_desktop_plan(approved_cwd)
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(other_cwd),
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "updatedInput" not in output
    assert "visible desktop" in output["additionalContext"].lower()


def test_permission_request_allows_valid_desktop_control_lease() -> None:
    plan = _approved_desktop_plan(Path.cwd())
    decision = handle_permission_request(
        {
            "hook_event_name": "PermissionRequest",
            "cwd": str(Path.cwd()),
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PermissionRequest"
    assert output["decision"]["behavior"] == "allow"


def test_pre_tool_use_allows_browser_bridge_with_approved_desktop_control_lease() -> None:
    plan = _approved_desktop_plan(Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(Path.cwd()),
            "tool_name": "mcp__node_repl.js",
            "tool_input": {"code": "return 1"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output


@pytest.mark.parametrize(
    "tool_name",
    [
        "mcp__browser_use.navigate",
        "mcp__browser.navigate",
        "mcp__chrome_session.call",
        "browser_use.navigate",
        "chrome.session",
    ],
)
def test_pre_tool_use_allows_future_browser_bridge_aliases_with_approved_desktop_control_lease(tool_name: str) -> None:
    plan = _approved_desktop_plan(Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(Path.cwd()),
            "tool_name": tool_name,
            "tool_input": {"code": "return 1"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output


def test_pre_tool_use_allows_in_app_browser_bridge_without_desktop_lease() -> None:
    plan = detect_skill_route("Open localhost:3000 and tell me whether the page renders correctly.", cwd=Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(Path.cwd()),
            "tool_name": "mcp__browser_use.navigate",
            "tool_input": {"url": "http://localhost:3000"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output


def test_pre_tool_use_warns_for_browser_bridge_when_route_did_not_select_browser_authority() -> None:
    plan = detect_skill_route("inspect the policy text", cwd=Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(Path.cwd()),
            "tool_name": "mcp__browser_use.navigate",
            "tool_input": {"url": "http://localhost:3000"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "outside the selected skill authority" in output["additionalContext"]


def test_pre_tool_use_warns_for_chrome_bridge_until_desktop_control_lease_is_approved() -> None:
    plan = detect_skill_route("use my logged-in Chrome tab to inspect the page", cwd=Path.cwd())
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(Path.cwd()),
            "tool_name": "mcp__chrome_session.call",
            "tool_input": {"code": "return 1"},
        },
        plan,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "desktop control lease" in output["additionalContext"].lower()


def test_permission_request_without_desktop_control_lease_falls_through_to_native_prompt() -> None:
    plan = detect_skill_route("inspect the policy text", cwd=Path.cwd())
    decision = handle_permission_request(
        {
            "hook_event_name": "PermissionRequest",
            "cwd": str(Path.cwd()),
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        plan,
    )

    assert "hookSpecificOutput" not in decision
    assert "systemMessage" in decision
    assert "one-click" in decision["systemMessage"]


def test_pre_tool_use_warns_apply_patch_without_skill_plan() -> None:
    decision = handle_pre_tool_use(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
        },
        None,
    )

    output = decision["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "skill hook plan" in output["additionalContext"].lower()


@pytest.mark.asyncio
async def test_obvious_code_repair_plan_allows_mutation_without_confirmation(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-auto",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "fix this repo bug and add tests",
        },
        settings=settings,
        record=False,
    )

    pre_tool = await run_hook_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-auto",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
        },
        settings=settings,
        record=False,
    )
    assert "decision" not in pre_tool
    assert pre_tool["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


@pytest.mark.asyncio
async def test_continue_reply_reuses_prior_obvious_plan_without_confirmation(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-continue",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "fix this repo bug and add tests",
        },
        settings=settings,
        record=False,
    )

    continued = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-continue",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "prompt": "continue",
        },
        settings=settings,
        record=False,
    )
    context = continued["hookSpecificOutput"]["additionalContext"]

    assert "Original user prompt: fix this repo bug and add tests" in context
    assert "superpowers:test-driven-development" in context
    assert "confirm route" not in context.lower()

    pre_tool = await run_hook_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-continue",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** End Patch\n"},
        },
        settings=settings,
        record=False,
    )
    assert "decision" not in pre_tool
    assert pre_tool["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


@pytest.mark.asyncio
async def test_yes_reply_approves_pending_desktop_control_lease(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-desktop",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "use my logged-in Chrome tab to inspect the page",
        },
        settings=settings,
        record=False,
    )

    approved = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-desktop",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "prompt": "YES!!! THAT'S WHY I SET IT UP",
        },
        settings=settings,
        record=False,
    )
    context = approved["hookSpecificOutput"]["additionalContext"]
    assert "desktop_control_lease: passed" in context
    assert "chrome:Chrome" in context
    assert "confirm route" not in context.lower()

    pre_tool = await run_hook_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-desktop",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        settings=settings,
        record=False,
    )
    output = pre_tool["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output


def test_codex_chrome_extension_prompt_selects_chrome_skill_and_lease() -> None:
    plan = detect_skill_route(
        "I opened the Codex Chrome browser extension on the active Bonfire page; fill the supplier profile from the packet.",
        cwd=Path.cwd(),
    )

    checks = {check.name: check for check in plan.authority_checks}
    assert "chrome:Chrome" in _skill_names(plan)
    assert checks["desktop_control_lease"].status == "required"


@pytest.mark.asyncio
async def test_yes_approved_desktop_lease_is_cwd_scoped(tmp_path: Path) -> None:
    approved_cwd = tmp_path / "approved"
    other_cwd = tmp_path / "other"
    approved_cwd.mkdir()
    other_cwd.mkdir()
    settings = Settings(home=tmp_path / "runtime", state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-desktop-scope",
            "turn_id": "turn-1",
            "cwd": str(approved_cwd),
            "prompt": "use my logged-in Chrome tab to inspect the page",
        },
        settings=settings,
        record=False,
    )
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-desktop-scope",
            "turn_id": "turn-2",
            "cwd": str(approved_cwd),
            "prompt": "yes",
        },
        settings=settings,
        record=False,
    )

    pre_tool = await run_hook_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-desktop-scope",
            "turn_id": "turn-2",
            "cwd": str(other_cwd),
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        settings=settings,
        record=False,
    )

    output = pre_tool["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output
    assert "updatedInput" not in output
    assert "visible desktop" in output["additionalContext"].lower()


@pytest.mark.asyncio
async def test_runtime_permission_request_allows_valid_desktop_lease(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-permission",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "use my logged-in Chrome tab to inspect the page",
        },
        settings=settings,
        record=False,
    )
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-permission",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "prompt": "yes",
        },
        settings=settings,
        record=False,
    )

    permission = await run_hook_event(
        {
            "hook_event_name": "PermissionRequest",
            "session_id": "session-permission",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "tool_name": "Bash",
            "tool_input": {"command": "Start-Process chrome.exe https://www.google.com"},
        },
        settings=settings,
        record=False,
    )

    output = permission["hookSpecificOutput"]
    assert output["hookEventName"] == "PermissionRequest"
    assert output["decision"]["behavior"] == "allow"


@pytest.mark.asyncio
async def test_confirmation_reply_uses_pending_plan_and_allows_risky_mutation(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    first = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "push this commit to GitHub",
        },
        settings=settings,
        record=False,
    )
    first_context = first["hookSpecificOutput"]["additionalContext"]
    plan_id = first_context.split("Plan: ", maxsplit=1)[1].splitlines()[0]

    warned = await run_hook_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        },
        settings=settings,
        record=False,
    )
    assert "permissionDecision" not in warned["hookSpecificOutput"]
    assert "confirmation is requested" in warned["hookSpecificOutput"]["additionalContext"].lower()

    confirmed = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "prompt": f"confirm route {plan_id}",
        },
        settings=settings,
        record=False,
    )

    context = confirmed["hookSpecificOutput"]["additionalContext"]
    assert "Original user prompt: push this commit to GitHub" in context
    assert "Confirmation state: confirmed" in context

    pre_tool = await run_hook_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        },
        settings=settings,
        record=False,
    )
    assert "decision" not in pre_tool
    assert pre_tool["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


@pytest.mark.asyncio
async def test_ambient_suggestion_receipt_records_read_only_tool_discovery(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    decision = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-suggestions",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": _ambient_suggestion_prompt(),
        },
        settings=settings,
        record=True,
    )

    store = StateStore(settings)
    receipts = await store.list_skill_hook_receipts(limit=1)

    context = decision["hookSpecificOutput"]["additionalContext"]
    receipt = receipts[0]
    selected = {skill["name"] for skill in receipt["selected_skills"]}
    action = receipt["interpreted_actions"][0]
    checks = {check["name"]: check for check in receipt["authority_checks"]}

    assert "omitted large prompt" in context.lower()
    assert "confirm route" not in context.lower()
    assert receipt["confirmation_state"] == "not_required"
    assert "confirm route shp_709358f3764b4d7b" in receipt["prompt"].lower()
    assert action["mutates"] is False
    assert "openai-docs" not in selected
    assert "superpowers:systematic-debugging" not in selected
    assert checks["connected_app_tool_proof"]["status"] == "required"
    assert checks["connected_app_tool_proof"]["required_tool"] == "tool_search"


@pytest.mark.asyncio
async def test_skill_hook_plan_cli_inspection_omits_large_prompt_body(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    plan = detect_skill_route(_ambient_suggestion_prompt(), cwd=Path.cwd())
    persisted_path = persist_plan(settings, {"session_id": "session-cli", "turn_id": "turn-1"}, plan)

    result = await _skill_hook_plan(settings)

    inspected = result["skill_hook_plan"]
    assert result["state"] == "produced"
    assert result["plan_path"] == str(persisted_path)
    assert inspected["id"] == plan.id
    assert inspected["prompt"].startswith("[omitted large prompt;")
    assert "confirm route shp_709358f3764b4d7b" not in inspected["prompt"].lower()
    assert inspected["prompt_omitted"] is True
    assert inspected["prompt_chars"] == len(plan.prompt)
    assert inspected["prompt_lines"] == plan.prompt.count("\n") + 1


@pytest.mark.asyncio
async def test_override_reply_uses_named_skills_from_pending_plan(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-override",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "work on the thing",
        },
        settings=settings,
        record=False,
    )

    override = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-override",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "prompt": "use skills: openai-docs",
        },
        settings=settings,
        record=False,
    )

    context = override["hookSpecificOutput"]["additionalContext"]
    assert "openai-docs" in context
    assert "Confirmation state: confirmed" in context


@pytest.mark.asyncio
async def test_mismatched_route_reply_is_advisory_not_blocking(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-mismatch",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "work on the thing",
        },
        settings=settings,
        record=False,
    )

    decision = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-mismatch",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "prompt": "confirm route shp_deadbeef",
        },
        settings=settings,
        record=False,
    )

    assert "decision" not in decision
    assert "No pending skill route matched" in decision["hookSpecificOutput"]["additionalContext"]


@pytest.mark.asyncio
async def test_cancel_route_reply_is_advisory_not_blocking(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    first = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-cancel",
            "turn_id": "turn-1",
            "cwd": str(Path.cwd()),
            "prompt": "work on the thing",
        },
        settings=settings,
        record=False,
    )
    plan_id = first["hookSpecificOutput"]["additionalContext"].split("Plan: ", maxsplit=1)[1].splitlines()[0]

    decision = await run_hook_event(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-cancel",
            "turn_id": "turn-2",
            "cwd": str(Path.cwd()),
            "prompt": f"cancel route {plan_id}",
        },
        settings=settings,
        record=False,
    )

    assert "decision" not in decision
    assert "cancelled" in decision["hookSpecificOutput"]["additionalContext"].lower()


def test_stop_continues_when_terminal_receipt_is_missing() -> None:
    plan = detect_skill_route("fix this repo bug and add tests", cwd=Path.cwd())
    decision = handle_stop(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "I made the change.",
            "stop_hook_active": False,
        },
        plan,
    )

    assert decision == {}


def test_stop_does_not_continue_low_risk_standard_prompt_without_receipt() -> None:
    plan = detect_skill_route("continue", cwd=Path.cwd())
    decision = handle_stop(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "Continuing.",
            "stop_hook_active": False,
        },
        plan,
    )

    assert decision == {}


@pytest.mark.asyncio
async def test_schema_migration_creates_skill_hook_receipts(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()

    with sqlite3.connect(settings.state_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(skill_hook_receipts)").fetchall()]

    assert {"id", "plan_id", "hook_event_name", "selected_skills_json", "terminal_state_requirement"} <= set(columns)


@pytest.mark.asyncio
async def test_dispatcher_receipt_embeds_selected_skills(tmp_path: Path) -> None:
    class FakeLocalAdapter:
        async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "model": "fake-local", "text": "ok"}

    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_services([ServiceInfo(id="fake-local", name="Fake Local", service_group="test", adapter_name="fake-local", protocol="test")])
    await store.upsert_capabilities(
        [
            Capability(
                id="fake-local:code_repair",
                adapter_name="fake-local",
                capability_id="code_repair",
                rating_instruction=3,
                rating_quality=3,
                latency_band="fast",
                consequence_max=ConsequenceTier.MEDIUM,
                billing_class=BillingClass.LOCAL_RESOURCE,
            )
        ]
    )
    dispatcher = Dispatcher(settings, store, NotificationSpine(settings.notifications_path, store))
    dispatcher.adapters = {"fake-local": FakeLocalAdapter()}  # type: ignore[assignment]

    result = await dispatcher.dispatch_text("fix this repo bug and add tests")

    assert result.state == "completed"
    assert result.receipt["skill_hook_plan_id"]
    selected = {item["name"] for item in result.receipt["selected_skills"]}
    assert "superpowers:test-driven-development" in selected
    assert result.receipt["terminal_state_requirement"] == "built"
    saved_receipt = json.loads(Path(str(result.receipt["receipt_path"])).read_text(encoding="utf-8"))
    assert saved_receipt["selected_skills"] == result.receipt["selected_skills"]
