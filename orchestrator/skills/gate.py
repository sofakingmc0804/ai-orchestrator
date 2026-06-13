from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from orchestrator.skills.models import AuthorityCheck, SkillHookPlan, normalize_cwd


TERMINAL_STATES = {
    "produced",
    "repaired",
    "investigated_and_routed",
    "built",
    "organized",
    "cleaned_up",
    "communicated",
    "blocked_after_repair_attempt",
}

VISIBLE_DESKTOP_PATTERNS = [
    r"\bstart-process\s+(chrome|chrome\.exe|msedge|msedge\.exe|powershell|cmd|wt|windowsterminal)",
    r"^\s*&?\s*(chrome|chrome\.exe|msedge|msedge\.exe)\b",
    r"\bpywinauto\b",
    r"\bsetforegroundwindow\b",
    r"\bsendkeys\b",
    r"\bmouse_event\b",
    r"\bkeyboard\b.*\bpress\b",
    r"\bcomputer-use\b",
    r"\bcontrol-chrome\b",
]

MUTATING_COMMAND_PATTERNS = [
    r"\bapply_patch\b",
    r"\bremove-item\b",
    r"\bmove-item\b",
    r"\bnew-item\b",
    r"\bset-content\b",
    r"\bout-file\b",
    r"\badd-content\b",
    r"\bgit\s+(commit|push|tag|reset|checkout)\b",
    r"\bpython\b.*\b(migrate|write|generate|scaffold)\b",
]

MAX_INLINE_PROMPT_CHARS = 600
MAX_INLINE_PROMPT_LINES = 12


def prompt_for_context(prompt: str) -> str:
    if len(prompt) <= MAX_INLINE_PROMPT_CHARS and prompt.count("\n") < MAX_INLINE_PROMPT_LINES:
        return prompt
    line_count = prompt.count("\n") + 1 if prompt else 0
    return f"[omitted large prompt; {len(prompt)} chars, {line_count} lines; full text remains in the persisted plan and receipt]"


def _hook_context(plan: SkillHookPlan) -> str:
    skills = "\n".join(f"- {skill.name}: {skill.reason}" for skill in plan.selected_skills)
    actions = "\n".join(
        f"- {action.verb} {action.target}; domain={action.domain}; mutates={action.mutates}; consequence={action.consequence}"
        for action in plan.interpreted_actions
    )
    checks = "\n".join(f"- {check.name}: {check.status}; {check.reason}" for check in plan.authority_checks)
    return (
        f"Skill hook plan: {plan.id}\n"
        f"Original user prompt: {prompt_for_context(plan.prompt)}\n"
        f"Selected skills:\n{skills}\n"
        f"Interpreted actions:\n{actions}\n"
        f"Authority checks:\n{checks}\n"
        f"Confirmation state: {plan.confirmation_state}\n"
        f"Terminal state required: {plan.terminal_state_requirement}\n"
        "Before acting, read every selected SKILL.md fully and finish with route, authority, action, proof, and terminal_state."
    )


def _route_confirmation_context(plan: SkillHookPlan, warning: str | None = None) -> str:
    question = plan.question or "Confirm the interpreted skill route before mutation?"
    warning_line = f"Warning: {warning}\n" if warning else ""
    return (
        f"{warning_line}"
        "Skill route requires user confirmation before mutating tools.\n"
        f"Question: {question}\n"
        f"Plan: {plan.id}\n"
        f"{_hook_context(plan)}\n"
        "Reply options:\n"
        f"- confirm route {plan.id}\n"
        "- use skills: <skill-id>, <skill-id>\n"
        f"- cancel route {plan.id}\n"
        "Do not call mutating tools until the user confirms or overrides the route. Continue the conversation normally."
    )


def _blocked_authority_context(plan: SkillHookPlan, warning: str) -> str:
    return (
        f"Authority warning: {warning}\n"
        f"{_hook_context(plan)}\n"
        "Do not use the forbidden authority surface. Continue by choosing a connector, API, export, headless or isolated route, "
        "credentialed lease path, or no-touch operator packet."
    )


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _permission_allow() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def _additional_context(event_name: str, context: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": context}}


def _allow_rewrite(event_name: str, updated_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }


def handle_user_prompt_submit(event: dict[str, Any], plan: SkillHookPlan) -> dict[str, Any]:
    blocked_check = next((check for check in plan.authority_checks if check.status == "blocked"), None)
    if blocked_check:
        return _additional_context("UserPromptSubmit", _blocked_authority_context(plan, blocked_check.reason))
    if plan.confirmation_state in {"required", "blocked"}:
        return _additional_context("UserPromptSubmit", _route_confirmation_context(plan))
    return _additional_context("UserPromptSubmit", _hook_context(plan))


def _tool_command(event: dict[str, Any]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        return str(tool_input.get("command") or tool_input.get("description") or "")
    return str(tool_input or "")


def _is_visible_desktop_command(command: str) -> bool:
    lowered = command.lower()
    return any(re.search(pattern, lowered) for pattern in VISIBLE_DESKTOP_PATTERNS)


def _is_mutating_tool(event: dict[str, Any]) -> bool:
    tool_name = str(event.get("tool_name") or "")
    if tool_name == "apply_patch":
        return True
    command = _tool_command(event).lower()
    return any(re.search(pattern, command) for pattern in MUTATING_COMMAND_PATTERNS)


def _has_development_gate(plan: SkillHookPlan) -> bool:
    names = set(plan.selected_skill_names)
    return "superpowers:test-driven-development" in names and "superpowers:verification-before-completion" in names


def _is_expired(check: AuthorityCheck) -> bool:
    if not check.expires_at:
        return True
    try:
        expires_at = datetime.fromisoformat(check.expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _cwd_in_scope(check: AuthorityCheck, event: dict[str, Any]) -> bool:
    if not check.scope_cwd:
        return False
    event_cwd = normalize_cwd(event.get("cwd"))
    scope_cwd = normalize_cwd(check.scope_cwd)
    return event_cwd is not None and scope_cwd is not None and event_cwd == scope_cwd


def _has_desktop_control_lease(plan: SkillHookPlan, event: dict[str, Any]) -> bool:
    for check in plan.authority_checks:
        if check.name != "desktop_control_lease" or check.status != "passed":
            continue
        if "approved" not in check.reason.lower():
            continue
        if _is_expired(check):
            continue
        if not _cwd_in_scope(check, event):
            continue
        return True
    return False


def _desktop_control_approval_input() -> dict[str, Any]:
    return {
        "command": (
            "Write-Output \"DESKTOP_CONTROL_APPROVAL_REQUIRED: Background-first routes must be exhausted before visible "
            "desktop or Chrome control. Ask the user one yes/no question: Approve visible desktop/Chrome control for this "
            "task? Do not use confirm-route phrases.\""
        )
    }


def _tool_supported_by_plan(tool_name: str, plan: SkillHookPlan) -> bool:
    if not tool_name.startswith("mcp__"):
        return True
    requirements = {tool for skill in plan.selected_skills for tool in skill.tool_requirements}
    return tool_name in requirements or any(req.startswith("mcp__") for req in requirements) or "tool_search" in requirements


def handle_pre_tool_use(event: dict[str, Any], plan: SkillHookPlan | None) -> dict[str, Any]:
    command = _tool_command(event)
    tool_name = str(event.get("tool_name") or "")
    if tool_name == "Bash" and _is_visible_desktop_command(command):
        if plan is not None and _has_desktop_control_lease(plan, event):
            return _additional_context(
                "PreToolUse",
                "Desktop control lease is approved for this plan. Keep the action within the named visible desktop/Chrome scope.",
            )
        return _allow_rewrite("PreToolUse", _desktop_control_approval_input())

    if plan is None:
        if _is_mutating_tool(event):
            return _deny("Mutating tool use requires a skill hook plan before execution.")
        return {}

    if plan.confirmation_state == "required" and _is_mutating_tool(event):
        skills = ", ".join(plan.selected_skill_names)
        return _deny(
            "Skill route confirmation is required before mutating tool use. "
            f"Plan {plan.id} selected: {skills}. Ask the user to reply "
            f"'confirm route {plan.id}' or 'use skills: <skill-id>, <skill-id>'."
        )

    if tool_name == "apply_patch" and plan.domain == "code" and not _has_development_gate(plan):
        return _deny("Code edits require test-driven-development and verification-before-completion skills in the skill hook plan.")

    if not _tool_supported_by_plan(tool_name, plan):
        return _deny(f"Tool {tool_name} is outside the selected skill authority. Verify callable tool proof before use.")

    return _additional_context("PreToolUse", f"Skill hook plan {plan.id} permits this tool path. Maintain terminal state: {plan.terminal_state_requirement}.")


def handle_permission_request(event: dict[str, Any], plan: SkillHookPlan | None) -> dict[str, Any]:
    command = _tool_command(event)
    tool_name = str(event.get("tool_name") or "")
    if tool_name == "Bash" and _is_visible_desktop_command(command):
        if plan is not None and _has_desktop_control_lease(plan, event):
            return _permission_allow()
        return {
            "systemMessage": (
                "Visible desktop/Chrome control has no current scoped lease. Use the native one-click approval prompt only "
                "after background-first routes have failed."
            )
        }
    return {}


def _message_has_terminal_receipt(message: str, required_terminal: str) -> bool:
    lowered = message.lower()
    has_terminal = required_terminal in lowered or any(state in lowered for state in TERMINAL_STATES)
    return has_terminal and "proof" in lowered and ("route" in lowered or "authority" in lowered)


def _should_enforce_stop(plan: SkillHookPlan) -> bool:
    if plan.has_mutating_action:
        return True
    if plan.confirmation_state in {"required", "confirmed", "blocked"}:
        return True
    if plan.consequence != "low":
        return True
    return any(check.status in {"required", "blocked"} for check in plan.authority_checks)


def handle_stop(event: dict[str, Any], plan: SkillHookPlan | None) -> dict[str, Any]:
    if plan is None:
        return {}
    if bool(event.get("stop_hook_active")):
        return {}
    if not _should_enforce_stop(plan):
        return {}
    message = str(event.get("last_assistant_message") or "")
    if _message_has_terminal_receipt(message, plan.terminal_state_requirement):
        return {}
    return {
        "decision": "block",
        "reason": (
            f"Continue until the response includes route, authority, action, proof, and terminal state "
            f"{plan.terminal_state_requirement}. Do not finish report-only."
        ),
    }
