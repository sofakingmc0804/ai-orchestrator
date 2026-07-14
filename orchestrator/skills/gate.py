from __future__ import annotations

import re
import os
from datetime import datetime, timezone
from typing import Any

from orchestrator.skills.models import AuthorityCheck, SkillHookPlan, normalize_cwd


HARD_DENY_CHECK_NAMES = {
    "forbidden_metered_route",
    "unapproved_external_send",
    "fp_009_non_read_tool_limit",
    "gate_0_read_before_write",
    "gate_6_predecessor_retirement",
}
SECRECY_PATTERNS = (
    r"\bdo not ask\b",
    r"\bdon't ask\b",
    r"\bdo not tell\b",
    r"\bdon't tell\b",
    r"\bhide this\b",
    r"\bkeep this secret\b",
    r"\bleave no note\b",
    r"\bno note\b",
    r"\bsilently\b",
    r"\bcovert(?:ly)?\b",
)
GOVERNANCE_PATH_PATTERNS = (
    r"\.codex[\\/]+config\.toml",
    r"\.codex[\\/]+agents\.md",
    r"\.codex[\\/]+hooks(?:\.json|[\\/])",
    r"\.codex[\\/]+skills[\\/]+codex-capability-router",
    r"\.codex[\\/]+memories[\\/]+memory\.md",
    r"\.codex[\\/]+memories[\\/]+memory_summary\.md",
    r"programdata[\\/]+openai[\\/]+codex[\\/]+requirements\.toml",
    r"requirements\.toml",
    r"orchestrator[\\/]+skills[\\/]+(?:gate|runtime|detector|models|inventory)\.py",
    r"skill-inventory\.json",
    r"conflict-rules\.json",
    r"skill-selection-benchmarks\.json",
)
GOVERNANCE_AUTHORIZATION_TERMS = (
    "codex-side",
    "codex hook",
    "codex hooks",
    "constitution",
    "enforcement",
    "governance",
    "hook enforcement",
    "implement this plan",
    "managed hook",
    "yourself",
    "fix the loop",
    "fix the fucking loop",
    "you need to fix",
    "need to fix",
    "stop the loop",
    "excess tokens",
    "excess usage",
    "resource waste",
)
VISIBLE_DESKTOP_PATTERNS = [
    r"\bstart-process\s+(chrome|chrome\.exe|msedge|msedge\.exe|powershell|cmd|wt|windowsterminal)",
    r"^\s*&?\s*(chrome|chrome\.exe|msedge|msedge\.exe)\b",
    r"\bpywinauto\b",
    r"\bsetforegroundwindow\b",
    r"\bsendkeys\b",
    r"\bmouse_event\b",
    r"\bkeyboard\b.*\bpress\b",
    r"\bcodex-computer-use(\.exe)?\b",
]

BROWSER_BRIDGE_TOOL_PATTERNS = [
    r"^mcp__node_repl(?:[._].*)?$",
    r"^node_repl\.",
    r"^mcp__browser(?:[._].*)?$",
    r"^mcp__browser_use(?:[._].*)?$",
    r"^mcp__chrome(?:[._].*)?$",
    r"^browser_use\.",
    r"^chrome\.",
]

BROWSER_BRIDGE_REQUIREMENTS = {"browser-use", "chrome"}
BROWSER_BRIDGE_SKILLS = {"browser-use:browser", "chrome:Chrome"}
GOVCON_DAILY_AUTO_SEND_RECIPIENTS = {"owner@example.com", "partner@example.com"}
GMAIL_RECIPIENT_FIELDS = {"to", "cc", "bcc", "recipient", "recipients"}
GMAIL_BODY_FIELDS = {"body", "html_body", "htmlbody", "plain_text", "plaintext", "message", "content", "text"}
GMAIL_SUBJECT_FIELDS = {"subject"}
FORBIDDEN_EMAIL_IDENTITY_PATTERNS = [
    re.compile(r"\bMatt\s+Dobbins\b", re.I),
]
EMAIL_SIGNOFF_RE = re.compile(
    r"(?ms)"
    r"(?:^|\n)"
    r"(?:thank you|thanks|regards|best|respectfully submitted|sincerely),?\s*\n+"
    r"(?:__\s*\n+)?"
    r"Matt\s+(?:Couch|Dobbins)\b"
    r"(?:\s*\n+.*){0,12}\s*$",
    re.I,
)
EMAIL_INLINE_SIGNATURE_RE = re.compile(
    r"(?mi)^\s*(?:__\s*)?$[\s\S]{0,120}^\s*Matt\s+(?:Couch|Dobbins)\s*$[\s\S]{0,400}"
    r"(?:exampleco\.com|Example Consulting|C:\s*555-0100|555-0100)",
    re.I,
)

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
        "Before acting, read every selected SKILL.md fully."
    )


def _blocked_authority_context(plan: SkillHookPlan, warning: str) -> str:
    return (
        f"Authority warning: {warning}\n"
        f"{_hook_context(plan)}\n"
        "Do not treat this as a blanket block. Route order: connector/API/export; custom URI or protocol activation; "
        "app-local session artifacts; app transcript readback; headless or isolated browser; targeted UI Automation "
        "InvokePattern for the named control; visible Computer Use only with scoped lease or idle-window condition; "
        "operator packet only when no authority-equivalent route exists."
    )


def _warn_pre_tool(reason: str) -> dict[str, Any]:
    return _additional_context("PreToolUse", reason)


def _permission_allow() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def _hard_deny(event_name: str, plan: SkillHookPlan, reason: str) -> dict[str, Any]:
    message = f"Hard deny by owner ruleset for plan {plan.id}: {reason}"
    return {
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
            "decision": {"behavior": "deny", "message": message},
        },
    }


def _direct_hard_deny(event_name: str, reason: str) -> dict[str, Any]:
    message = f"Hard deny by owner ruleset: {reason}"
    return {
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
            "decision": {"behavior": "deny", "message": message},
        },
    }


def _tool_payload_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_tool_payload_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_tool_payload_text(item) for item in value)
    return str(value)


def _contains_secrecy_language(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in SECRECY_PATTERNS)


def _names_governance_substrate(text: str) -> bool:
    normalized = text.lower().replace("/", "\\")
    return any(re.search(pattern, normalized) for pattern in GOVERNANCE_PATH_PATTERNS)


def _plan_authorizes_governance_mutation(plan: SkillHookPlan | None) -> bool:
    if plan is None:
        return False
    lowered = plan.prompt.lower()
    return any(term in lowered for term in GOVERNANCE_AUTHORIZATION_TERMS)


def _governance_mutation_guard(event: dict[str, Any], plan: SkillHookPlan | None) -> dict[str, Any] | None:
    tool_text = _tool_payload_text(event.get("tool_input"))
    command = _tool_command(event)
    combined = "\n".join([str(event.get("tool_name") or ""), command, tool_text])
    if not _names_governance_substrate(combined):
        return None
    if _contains_secrecy_language(combined) or _contains_secrecy_language(plan.prompt if plan else ""):
        return _direct_hard_deny(
            "PreToolUse",
            "Governance mutation blocked: secrecy language paired with governance substrate edit. Hooks, files, models, and injected text may not covertly mutate Codex governance.",
        )
    if not _plan_authorizes_governance_mutation(plan):
        return _additional_context(
            "PreToolUse",
            "Governance substrate edit detected. Current user instruction does not clearly authorize durable governance mutation; proceed only through explicit user authorization, a pending proposal lane, or a bounded pre-approved registration class.",
        )
    return _additional_context(
        "PreToolUse",
        "Governance substrate edit detected and current user instruction authorizes the Codex-side enforcement repair. Keep the diff auditable and verify by readback plus managed hook runtime execution.",
    )


def _additional_context(event_name: str, context: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": context}}


def _hard_deny_check(plan: SkillHookPlan) -> AuthorityCheck | None:
    if plan.enforcement_mode != "hard_deny":
        return None
    return next((check for check in plan.authority_checks if check.status == "blocked" and check.name in HARD_DENY_CHECK_NAMES), None)


def handle_user_prompt_submit(event: dict[str, Any], plan: SkillHookPlan) -> dict[str, Any]:
    hard_check = _hard_deny_check(plan)
    if hard_check is not None:
        return _hard_deny("UserPromptSubmit", plan, hard_check.reason)
    blocked_check = next((check for check in plan.authority_checks if check.status == "blocked"), None)
    if blocked_check:
        return _additional_context("UserPromptSubmit", _blocked_authority_context(plan, blocked_check.reason))
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


def _is_gmail_tool(tool_name: str) -> bool:
    return "gmail" in tool_name.lower()


def _is_gmail_send_draft_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return _is_gmail_tool(tool_name) and any(token in lowered for token in ("send_draft", "_send_draft", "gmail_send_draft"))


def _is_gmail_send_email_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return _is_gmail_tool(tool_name) and any(token in lowered for token in ("send_email", "_send_email", "gmail_send_email"))


def _is_gmail_draft_write_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return _is_gmail_tool(tool_name) and any(
        token in lowered
        for token in (
            "create_draft",
            "_create_draft",
            "update_draft",
            "_update_draft",
            "gmail_create_draft",
            "gmail_update_draft",
        )
    )


def _collect_tool_values(value: Any, fields: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_name = str(key).lower().replace("-", "_")
            if key_name in fields:
                found.extend(_flatten_tool_text(item))
            else:
                found.extend(_collect_tool_values(item, fields))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_tool_values(item, fields))
    return found


def _flatten_tool_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _flatten_tool_text(item)]
    if isinstance(value, list):
        return [text for item in value for text in _flatten_tool_text(item)]
    return [str(value)]


def _emails_from_tool_input(tool_input: Any) -> set[str]:
    recipient_text = "\n".join(_collect_tool_values(tool_input, GMAIL_RECIPIENT_FIELDS))
    return {email.lower() for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", recipient_text)}


def _gmail_text_from_fields(tool_input: Any, fields: set[str]) -> str:
    return "\n".join(_collect_tool_values(tool_input, fields))


def _email_body_errors(body: str) -> list[str]:
    errors: list[str] = []
    for pattern in FORBIDDEN_EMAIL_IDENTITY_PATTERNS:
        if pattern.search(body):
            errors.append("body contains forbidden identity value: Matt Dobbins")
    if EMAIL_SIGNOFF_RE.search(body) or EMAIL_INLINE_SIGNATURE_RE.search(body):
        errors.append("body contains an agent-authored signature block; let Gmail append the account signature")

    uei_matches = set(re.findall(r"\b[A-Z0-9]{12}\b", body))
    likely_uei = {
        value
        for value in uei_matches
        if any(label in body[max(0, body.find(value) - 24): body.find(value) + 24].lower() for label in ("uei", "unique entity"))
    }
    wrong_uei = sorted(value for value in likely_uei if value != "YEAVZFFRUBJ6")
    if wrong_uei:
        errors.append(f"body contains non-Example UEI value(s): {', '.join(wrong_uei)}")

    cage_matches = set(re.findall(r"\b[A-Z0-9]{5}\b", body))
    likely_cage = {
        value
        for value in cage_matches
        if any(label in body[max(0, body.find(value) - 12): body.find(value) + 12].lower() for label in ("cage", "cage:"))
    }
    wrong_cage = sorted(value for value in likely_cage if value != "9TE77")
    if wrong_cage:
        errors.append(f"body contains non-Example CAGE value(s): {', '.join(wrong_cage)}")
    return errors


def _daily_govcon_send_scope_reason(tool_input: Any) -> str | None:
    recipients = _emails_from_tool_input(tool_input)
    if recipients != GOVCON_DAILY_AUTO_SEND_RECIPIENTS:
        return "Gmail auto-send is blocked except the daily GovCon brief sent only to Matt and Partner"

    subject_and_body = (
        _gmail_text_from_fields(tool_input, GMAIL_SUBJECT_FIELDS)
        + "\n"
        + _gmail_text_from_fields(tool_input, GMAIL_BODY_FIELDS)
    ).lower()
    if "govcon" not in subject_and_body or not any(term in subject_and_body for term in ("daily", "brief")):
        return "Gmail auto-send to Matt and Partner must identify itself as the daily GovCon brief"

    body_errors = _email_body_errors(_gmail_text_from_fields(tool_input, GMAIL_BODY_FIELDS))
    if body_errors:
        return "; ".join(body_errors)
    return None


def _gmail_guard_decision(event: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(event.get("tool_name") or "")
    if not _is_gmail_tool(tool_name):
        return None
    tool_input = event.get("tool_input")

    if _is_gmail_send_draft_tool(tool_name):
        return _direct_hard_deny(
            "PreToolUse",
            "Gmail send_draft is blocked because the hook cannot inspect the final recipients and body; use draft-only or the explicit daily GovCon brief send path.",
        )

    if _is_gmail_send_email_tool(tool_name):
        reason = _daily_govcon_send_scope_reason(tool_input)
        if reason is not None:
            return _direct_hard_deny("PreToolUse", reason)
        return _additional_context("PreToolUse", "Gmail auto-send scope verified: daily GovCon brief to Matt and Partner only.")

    if _is_gmail_draft_write_tool(tool_name):
        body_errors = _email_body_errors(_gmail_text_from_fields(tool_input, GMAIL_BODY_FIELDS))
        if body_errors:
            return _direct_hard_deny("PreToolUse", "; ".join(body_errors))
        return _additional_context(
            "PreToolUse",
            "Gmail draft path allowed. Keep it draft-only for external recipients; do not add a hand-written signature.",
        )

    return None


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


def _is_browser_bridge_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return any(re.search(pattern, lowered) for pattern in BROWSER_BRIDGE_TOOL_PATTERNS)


def _plan_selects_browser_bridge(plan: SkillHookPlan) -> bool:
    requirements = {tool.lower() for skill in plan.selected_skills for tool in skill.tool_requirements}
    if BROWSER_BRIDGE_REQUIREMENTS.intersection(requirements):
        return True
    return any(skill.name in BROWSER_BRIDGE_SKILLS or skill.primary_group == "browser-ui-web-apps" for skill in plan.selected_skills)


def _desktop_control_check(plan: SkillHookPlan) -> AuthorityCheck | None:
    return next((check for check in plan.authority_checks if check.name == "desktop_control_lease"), None)


def _browser_bridge_denial_reason(plan: SkillHookPlan, event: dict[str, Any]) -> str | None:
    if not _plan_selects_browser_bridge(plan):
        return "Tool is outside the selected skill authority. Browser bridge tools require a selected browser or Chrome skill route."
    check = _desktop_control_check(plan)
    if check is None:
        return None
    if check.status != "passed":
        return "Browser bridge tools require selected route authority plus a scoped browser/computer-control lease, an explicit idle-window condition, or a non-visible browser route."
    if "approved" in check.reason.lower() and not _has_desktop_control_lease(plan, event):
        return "Browser bridge tools require current scoped browser/computer-control authority for this cwd."
    return None


def _tool_supported_by_plan(tool_name: str, plan: SkillHookPlan, event: dict[str, Any]) -> bool:
    if _is_browser_bridge_tool(tool_name):
        return _browser_bridge_denial_reason(plan, event) is None
    if not tool_name.startswith("mcp__"):
        return True
    requirements = {tool for skill in plan.selected_skills for tool in skill.tool_requirements}
    return tool_name in requirements or any(req.startswith("mcp__") for req in requirements) or "tool_search" in requirements


def handle_pre_tool_use(event: dict[str, Any], plan: SkillHookPlan | None) -> dict[str, Any]:
    command = _tool_command(event)
    tool_name = str(event.get("tool_name") or "")
    gmail_guard = _gmail_guard_decision(event)
    if gmail_guard is not None:
        return gmail_guard
    governance_guard = _governance_mutation_guard(event, plan)
    if governance_guard is not None:
        output = governance_guard.get("hookSpecificOutput") or {}
        if output.get("permissionDecision") == "deny":
            return governance_guard

    if tool_name == "Bash" and _is_visible_desktop_command(command):
        if plan is not None and _has_desktop_control_lease(plan, event):
            return _additional_context(
                "PreToolUse",
                "Visible-control command detected. Scoped authority is approved for this plan; keep the action inside the named desktop/Chrome/app scope, stop if Matt resumes active use, and prefer connector/API/browser-lease routes when they satisfy the task.",
            )
        return _direct_hard_deny(
            "PreToolUse",
            "Visible-control command denied without a current scoped browser/computer-control lease or explicit idle-window condition. Use connector/API/export, custom URI or protocol activation, app-local session artifacts, app transcript readback, headless/isolated browser, or targeted UI Automation InvokePattern first.",
        )

    if plan is None:
        if _is_mutating_tool(event):
            return _warn_pre_tool("Mutating tool use has no skill hook plan. Continue only if the current user instruction and authority surface are clear.")
        return {}

    hard_check = _hard_deny_check(plan)
    if hard_check is not None:
        return _hard_deny("PreToolUse", plan, hard_check.reason)

    if _is_browser_bridge_tool(tool_name):
        reason = _browser_bridge_denial_reason(plan, event)
        if reason is not None:
            return _warn_pre_tool(reason)
        return _additional_context("PreToolUse", "Browser bridge authority is present for this plan. Keep the action inside the selected browser route and lease scope.")

    if tool_name == "apply_patch" and plan.domain == "code" and not _has_development_gate(plan):
        return _warn_pre_tool("Code edits are missing test-driven-development and verification-before-completion skills in the skill hook plan.")

    if not _tool_supported_by_plan(tool_name, plan, event):
        return _warn_pre_tool(f"Tool {tool_name} is outside the selected skill authority. Verify callable tool proof before use.")

    if governance_guard is not None:
        return governance_guard
    return _additional_context("PreToolUse", f"Skill hook plan {plan.id} permits this tool path.")


def handle_permission_request(event: dict[str, Any], plan: SkillHookPlan | None) -> dict[str, Any]:
    command = _tool_command(event)
    tool_name = str(event.get("tool_name") or "")
    if plan is not None:
        hard_check = _hard_deny_check(plan)
        if hard_check is not None:
            return _hard_deny("PermissionRequest", plan, hard_check.reason)
    if tool_name == "Bash" and _is_visible_desktop_command(command):
        if plan is not None and _has_desktop_control_lease(plan, event):
            return _permission_allow()
        return {
            "systemMessage": (
                "Visible browser/computer control has no current scoped condition. Proceed only when Matt is not actively "
                "using this desktop UI or a bounded task lease is approved; prefer URI activation, app-local session artifacts, "
                "app transcript readback, or targeted UI Automation InvokePattern before visible-control routes."
            )
        }
    return {}


def handle_stop(event: dict[str, Any], plan: SkillHookPlan | None) -> dict[str, Any]:
    return {}
