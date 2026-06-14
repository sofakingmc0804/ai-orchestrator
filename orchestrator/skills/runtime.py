from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import Settings, ensure_runtime_dirs
from orchestrator.skills.detector import detect_skill_route
from orchestrator.skills.gate import handle_permission_request, handle_pre_tool_use, handle_stop, handle_user_prompt_submit
from orchestrator.skills.inventory import load_default_skill_inventory
from orchestrator.skills.models import AuthorityCheck, SelectedSkill, SkillHookPlan, normalize_cwd
from orchestrator.state.store import StateStore, iso


DESKTOP_CONTROL_LEASE_MINUTES = 10


def _plan_dir(settings: Settings) -> Path:
    return settings.home / "skill-hooks" / "plans"


def _turn_key(event: dict[str, Any]) -> str:
    session_id = str(event.get("session_id") or "session")
    turn_id = str(event.get("turn_id") or "turn")
    safe = _safe_key(f"{session_id}_{turn_id}")
    return safe[:180]


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)


def persist_plan(settings: Settings, event: dict[str, Any], plan: SkillHookPlan) -> Path:
    ensure_runtime_dirs(settings)
    plan_dir = _plan_dir(settings)
    plan_dir.mkdir(parents=True, exist_ok=True)
    path = plan_dir / f"{_turn_key(event)}.json"
    path.write_text(json.dumps(plan.model_dump(mode="json"), indent=2), encoding="utf-8")
    return path


def load_plan(settings: Settings, event: dict[str, Any]) -> SkillHookPlan | None:
    path = _plan_dir(settings) / f"{_turn_key(event)}.json"
    if not path.exists():
        return None
    try:
        return SkillHookPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def load_latest_plan(settings: Settings, event: dict[str, Any]) -> SkillHookPlan | None:
    session_id = _safe_key(str(event.get("session_id") or "session"))
    plan_dir = _plan_dir(settings)
    if not plan_dir.exists():
        return None
    event_cwd = normalize_cwd(event.get("cwd"))
    paths = sorted(plan_dir.glob(f"{session_id}_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            plan = SkillHookPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        if event_cwd is None or plan.cwd is None or normalize_cwd(plan.cwd) == event_cwd:
            return plan
    return None


def _route_plan_id(prompt: str) -> str | None:
    match = re.search(r"\b(?:confirm|cancel)(?:\s+skill)?\s+route\s+(shp_[a-f0-9]+)\b", prompt, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _is_confirm_reply(prompt: str) -> bool:
    lowered = prompt.lower()
    return bool(re.search(r"\bconfirm(?:\s+skill)?\s+route\b", lowered)) or any(
        term in lowered for term in ("yes, confirm", "yes confirm", "confirm that route", "confirm the route")
    )


def _is_cancel_reply(prompt: str) -> bool:
    return bool(re.search(r"\bcancel(?:\s+skill)?\s+route\b", prompt, flags=re.IGNORECASE))


def _is_continue_reply(prompt: str) -> bool:
    return prompt.strip().lower() in {"continue", "keep going", "proceed", "carry on"}


def _is_desktop_control_approval_reply(prompt: str) -> bool:
    normalized = re.sub(r"[^a-z\s]", " ", prompt.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized.startswith("yes "):
        return True
    return normalized in {
        "yes",
        "approve",
        "approved",
        "yes approve",
        "yes, approve",
        "approve desktop control",
        "approve visible desktop control",
        "approve chrome control",
    }


def _has_pending_desktop_control_lease(plan: SkillHookPlan) -> bool:
    return any(check.name == "desktop_control_lease" and check.status == "required" for check in plan.authority_checks)


def _override_skill_names(prompt: str) -> list[str]:
    match = re.search(r"\buse\s+skills?\s*:\s*(.+)$", prompt, flags=re.IGNORECASE)
    if not match:
        return []
    raw_names = re.split(r"[,;\n]", match.group(1))
    return [name.strip().strip("`'\"") for name in raw_names if name.strip()]


def _selected_skill(name: str, reason: str) -> SelectedSkill:
    inventory = load_default_skill_inventory()
    item = inventory.get(name)
    if item is None:
        return SelectedSkill(name=name, reason=f"{reason} Inventory item missing; repair router assets before relying on this override.")
    return SelectedSkill(
        name=item.skill_id,
        path=item.path,
        reason=reason,
        primary_group=item.primary_group,
        tool_requirements=item.tool_requirements,
    )


def _advisory_context(event_name: str, context: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": context}}


def _append_selected_skill(plan: SkillHookPlan, name: str, reason: str) -> SkillHookPlan:
    selected = list(plan.selected_skills)
    candidate = _selected_skill(name, reason)
    existing = {skill.name for skill in selected}
    if candidate.name not in existing and name not in existing:
        selected.append(candidate)
    return plan.model_copy(update={"selected_skills": selected})


def _prompt_requests_chrome_control(prompt: str) -> bool:
    lowered = prompt.lower()
    if any(
        term in lowered
        for term in (
            "active chrome",
            "active tab",
            "browser extension",
            "chrome browser",
            "chrome extension",
            "codex chrome",
            "logged-in chrome",
        )
    ):
        return True
    return "chrome" in lowered and any(term in lowered for term in ("extension", "profile", "tab"))


def _mark_confirmed(plan: SkillHookPlan, reason: str) -> SkillHookPlan:
    checks: list[AuthorityCheck] = []
    replaced = False
    for check in plan.authority_checks:
        if check.name == "route_confirmation":
            checks.append(AuthorityCheck(name="route_confirmation", status="passed", reason=reason))
            replaced = True
        else:
            checks.append(check)
    if not replaced:
        checks.append(AuthorityCheck(name="route_confirmation", status="passed", reason=reason))
    return plan.model_copy(update={"confirmation_state": "confirmed", "question": None, "authority_checks": checks})


def _mark_desktop_control_approved(plan: SkillHookPlan, reason: str) -> SkillHookPlan:
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=DESKTOP_CONTROL_LEASE_MINUTES)).isoformat()
    scope_cwd = normalize_cwd(plan.cwd)
    checks: list[AuthorityCheck] = []
    replaced = False
    for check in plan.authority_checks:
        if check.name == "desktop_control_lease":
            checks.append(
                AuthorityCheck(
                    name="desktop_control_lease",
                    status="passed",
                    reason=reason,
                    expires_at=expires_at,
                    scope_cwd=scope_cwd,
                )
            )
            replaced = True
        else:
            checks.append(check)
    if not replaced:
        checks.append(
            AuthorityCheck(
                name="desktop_control_lease",
                status="passed",
                reason=reason,
                expires_at=expires_at,
                scope_cwd=scope_cwd,
            )
        )
    updated = plan.model_copy(update={"authority_checks": checks, "question": None})
    if _prompt_requests_chrome_control(plan.prompt):
        updated = _append_selected_skill(
            updated,
            "control-chrome",
            "Approved Chrome extension or active-tab lease requires Chrome control skill authority.",
        )
    return updated


def _apply_skill_override(plan: SkillHookPlan, names: list[str]) -> SkillHookPlan:
    selected: list[SelectedSkill] = [_selected_skill("codex-capability-router", "Route overrides still require capability-router governance.")]
    for name in names:
        if name not in {skill.name for skill in selected}:
            selected.append(_selected_skill(name, "User explicitly selected this skill in the route override."))
    updated = plan.model_copy(update={"selected_skills": selected})
    return _mark_confirmed(updated, f"User overrode the selected route with: {', '.join(names)}.")


def _resolve_followup_plan(settings: Settings, event: dict[str, Any]) -> tuple[SkillHookPlan | None, dict[str, Any] | None]:
    prompt = str(event.get("prompt") or "")
    pending = load_latest_plan(settings, event)
    if pending is None:
        return None, None

    requested_id = _route_plan_id(prompt)
    if requested_id and requested_id != pending.id:
        return None, _advisory_context("UserPromptSubmit", f"No pending skill route matched {requested_id}. Continue with the latest user instruction or submit the task again with the intended route.")

    overrides = _override_skill_names(prompt)
    if overrides:
        return _apply_skill_override(pending, overrides), None
    if _has_pending_desktop_control_lease(pending) and _is_desktop_control_approval_reply(prompt):
        return _mark_desktop_control_approved(pending, "User approved visible desktop or Chrome control for this pending task."), None
    if _is_confirm_reply(prompt):
        return _mark_confirmed(pending, "User confirmed the pending skill route."), None
    if _is_cancel_reply(prompt):
        return None, _advisory_context("UserPromptSubmit", f"Skill route {pending.id} cancelled. Continue with the latest user instruction or submit a new request with the intended route.")
    if _is_continue_reply(prompt):
        return pending, None
    return None, None


async def _record_receipt(settings: Settings, event: dict[str, Any], plan: SkillHookPlan | None, decision: dict[str, Any]) -> None:
    store = StateStore(settings)
    await store.initialize()
    hook_output = decision.get("hookSpecificOutput") or {}
    hook_decision = hook_output.get("decision")
    if isinstance(hook_decision, dict):
        recorded_decision = hook_decision.get("behavior")
    else:
        recorded_decision = decision.get("decision") or hook_output.get("permissionDecision")
    await store.record_skill_hook_receipt(
        {
            "id": f"shr_{_turn_key(event)}_{str(event.get('hook_event_name') or 'event').lower()}_{iso().replace(':', '').replace('.', '')}",
            "plan_id": plan.id if plan else None,
            "session_id": event.get("session_id"),
            "turn_id": event.get("turn_id"),
            "hook_event_name": event.get("hook_event_name"),
            "cwd": event.get("cwd"),
            "prompt": event.get("prompt"),
            "tool_name": event.get("tool_name"),
            "decision": recorded_decision,
            "confidence": plan.confidence if plan else None,
            "selected_skills": [skill.model_dump(mode="json") for skill in plan.selected_skills] if plan else [],
            "interpreted_actions": [action.model_dump(mode="json") for action in plan.interpreted_actions] if plan else [],
            "authority_checks": [check.model_dump(mode="json") for check in plan.authority_checks] if plan else [],
            "confirmation_state": plan.confirmation_state if plan else None,
            "terminal_state_requirement": plan.terminal_state_requirement if plan else None,
            "reason": decision.get("reason") or hook_output.get("permissionDecisionReason") or decision.get("systemMessage"),
            "raw_event": event,
            "created_at": iso(),
        }
    )


async def run_hook_event(event: dict[str, Any], settings: Settings | None = None, record: bool = True) -> dict[str, Any]:
    settings = settings or Settings.load()
    event_name = str(event.get("hook_event_name") or "")
    plan: SkillHookPlan | None = None
    if event_name == "UserPromptSubmit":
        prompt = str(event.get("prompt") or "")
        plan, followup_decision = _resolve_followup_plan(settings, event)
        if followup_decision is not None:
            decision = followup_decision
        else:
            if plan is None:
                plan = detect_skill_route(prompt, cwd=event.get("cwd"), source_event=event_name)
            persist_plan(settings, event, plan)
            decision = handle_user_prompt_submit(event, plan)
    elif event_name == "PreToolUse":
        raw_plan = event.get("skill_hook_plan")
        if isinstance(raw_plan, dict):
            plan = SkillHookPlan.model_validate(raw_plan)
        else:
            plan = load_plan(settings, event)
        decision = handle_pre_tool_use(event, plan)
    elif event_name == "PermissionRequest":
        raw_plan = event.get("skill_hook_plan")
        if isinstance(raw_plan, dict):
            plan = SkillHookPlan.model_validate(raw_plan)
        else:
            plan = load_plan(settings, event)
        decision = handle_permission_request(event, plan)
    elif event_name == "Stop":
        plan = load_plan(settings, event)
        decision = handle_stop(event, plan)
    else:
        decision = {}

    if record and event_name in {"UserPromptSubmit", "PreToolUse", "PermissionRequest", "Stop"}:
        try:
            await _record_receipt(settings, event, plan, decision)
        except Exception:
            pass
    return decision


def main() -> None:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps(_advisory_context("Unknown", f"Invalid hook JSON: {exc}")))
        return
    decision = asyncio.run(run_hook_event(event))
    print(json.dumps(decision or {}, separators=(",", ":")))


if __name__ == "__main__":
    main()
