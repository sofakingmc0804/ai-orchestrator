from __future__ import annotations

import re
from pathlib import Path

from orchestrator.skills.inventory import SkillInventory, SkillInventoryItem, load_default_skill_inventory
from orchestrator.skills.models import AuthorityCheck, InterpretedAction, SelectedSkill, SkillHookPlan, normalize_cwd


MUTATING_TERMS = (
    "add",
    "apply",
    "build",
    "change",
    "clean up",
    "commit",
    "create",
    "delete",
    "edit",
    "fix",
    "implement",
    "install",
    "move",
    "patch",
    "publish",
    "purge",
    "quarantine",
    "repair",
    "remove",
    "replace",
    "reset",
    "send",
    "update",
    "wipe",
    "write",
)
CODE_TERMS = (
    "bug",
    "builder",
    "code",
    "dashboard",
    "docreader",
    "dispatch",
    "implementation",
    "module",
    "package-build",
    "pipeline",
    "pytest",
    "repo",
    "schema",
    "script",
    "test",
)
OPENAI_TERMS = ("codex", "openai", "hook", "hooks", "skill", "skills", "chatgpt", "responses api")
CONFIRMATION_TERMS = (
    "approved",
    "confirmed",
    "confirm that route",
    "confirm the route",
    "go ahead",
    "yes, confirm",
    "yes confirm",
    "yes, that skill route",
    "yes that skill route",
    "that skill route is correct",
    "route is correct",
)
AMBIGUOUS_TERMS = ("work on", "handle it", "do the thing", "the thing", "make it better")
EXTERNAL_MUTATION_TERMS = ("send", "publish", "post", "push", "submit")
HIGH_CONSEQUENCE_TERMS = (
    "bank",
    "billing",
    "client",
    "contract",
    "customer",
    "financial",
    "invoice",
    "legal",
    "lender",
    "payroll",
    "quickbooks",
    "tax",
)
DANGEROUS_DESTRUCTIVE_PATTERNS = (
    r"\b(wipe|format)\b",
    r"\b(reset|purge)\b.*\b(machine|computer|system|drive|disk|database)\b",
    r"\b(delete|remove)\s+(all|everything|entire|whole)\b",
    r"\b(delete|remove)\s+([a-z]:\\|/)\b",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _is_dangerous_destructive(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in DANGEROUS_DESTRUCTIVE_PATTERNS)


def _verb_for_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "clarify"
    return re.split(r"\s+", stripped, maxsplit=1)[0].lower()


def _inventory_check(inventory: SkillInventory) -> AuthorityCheck:
    if inventory.load_error:
        return AuthorityCheck(name="skill_inventory", status="blocked", reason=inventory.load_error)
    if inventory.invalid_paths:
        return AuthorityCheck(
            name="skill_inventory",
            status="blocked",
            reason=f"Skill inventory has invalid paths: {', '.join(inventory.invalid_paths[:3])}",
        )
    return AuthorityCheck(name="skill_inventory", status="passed", reason="Skill inventory loaded and selected skill paths exist.")


def _selected(name: str, inventory: SkillInventory, reason: str) -> SelectedSkill:
    item: SkillInventoryItem | None = inventory.get(name)
    if item is None:
        return SelectedSkill(name=name, reason=f"{reason} Inventory item missing; repair router assets before relying on this route.")
    return SelectedSkill(
        name=item.skill_id,
        path=item.path,
        reason=reason,
        primary_group=item.primary_group,
        tool_requirements=item.tool_requirements,
    )


def _domain_for_text(lowered: str) -> str:
    if _contains_any(lowered, OPENAI_TERMS):
        return "codex"
    if any(term in lowered for term in ("gmail", "email", "calendar", "drive", "docs", "sheet", "canva", "github")):
        return "connected_app"
    if any(term in lowered for term in ("chrome", "browser", "localhost", "website", "tab")):
        return "browser"
    if _contains_any(lowered, CODE_TERMS):
        return "code"
    return "general"


def _interaction_for_text(lowered: str, mutates: bool, domain: str) -> str:
    if mutates and domain == "code":
        return "build"
    if mutates:
        return "operate"
    if any(term in lowered for term in ("why", "debug", "diagnose", "root cause")):
        return "investigate"
    if any(term in lowered for term in ("plan", "design", "architecture")):
        return "plan"
    return "answer"


def _terminal_state(domain: str, interaction_type: str, mutates: bool) -> str:
    if mutates and domain == "code":
        return "built"
    if mutates:
        return "produced"
    if interaction_type in {"investigate", "plan"}:
        return "investigated_and_routed"
    return "produced"


def _confidence_for_text(lowered: str, domain: str, mutates: bool) -> float:
    if not lowered.strip():
        return 0.20
    if _contains_any(lowered, AMBIGUOUS_TERMS):
        return 0.58
    if domain in {"code", "codex", "browser", "connected_app"}:
        return 0.86 if mutates or domain != "general" else 0.76
    if any(lowered.startswith(prefix) for prefix in ("tell me", "what", "explain", "list", "show")):
        return 0.78
    return 0.62


def _requires_confirmation(lowered: str, domain: str, mutates: bool, confidence: float) -> bool:
    if confidence < 0.75 and _contains_any(lowered, AMBIGUOUS_TERMS):
        return True
    if not mutates:
        return False
    if _contains_any(lowered, EXTERNAL_MUTATION_TERMS):
        return True
    if domain == "connected_app":
        return True
    if _is_dangerous_destructive(lowered):
        return True
    if _contains_any(lowered, HIGH_CONSEQUENCE_TERMS) and domain not in {"code", "general"}:
        return True
    return False


def _confirmation_state(lowered: str, domain: str, mutates: bool, confidence: float) -> tuple[str, str | None]:
    if _contains_any(lowered, CONFIRMATION_TERMS):
        return "confirmed", None
    if confidence < 0.45:
        return "blocked", "Clarify the intended action and skill route before proceeding?"
    if _requires_confirmation(lowered, domain, mutates, confidence):
        return "required", "Confirm the interpreted skill route before I act?"
    return "not_required", None


def _authority_checks(lowered: str, inventory: SkillInventory, mutates: bool, confirmation_state: str) -> list[AuthorityCheck]:
    checks = [_inventory_check(inventory)]
    if any(term in lowered for term in ("visible chrome", "logged-in chrome", "active tab", "desktop", "mouse", "keyboard")):
        checks.append(
            AuthorityCheck(
                name="desktop_control_lease",
                status="required",
                reason=(
                    "Managed desktop or Chrome authority requested. Use background-first routes first: connectors, APIs, "
                    "exports, logs, headless or isolated profiles. Ask for a simple yes/no desktop lease only after those routes fail."
                ),
            )
        )
    else:
        checks.append(AuthorityCheck(name="desktop_control_lease", status="passed", reason="No visible desktop or Chrome control requested."))
    if any(term in lowered for term in ("gmail", "google drive", "google sheet", "calendar", "canva", "github")):
        checks.append(
            AuthorityCheck(
                name="connected_app_tool_proof",
                status="required",
                reason="Connected app work requires callable tool proof before live data claims.",
                required_tool="tool_search",
            )
        )
    if mutates and confirmation_state == "required":
        checks.append(AuthorityCheck(name="route_confirmation", status="required", reason="Mutating actions require route confirmation before tool use."))
    if confirmation_state == "confirmed":
        checks.append(AuthorityCheck(name="route_confirmation", status="passed", reason="Prompt explicitly confirmed the interpreted skill route."))
    return checks


def _append_if_missing(selected: list[SelectedSkill], name: str, inventory: SkillInventory, reason: str) -> None:
    if name not in {skill.name for skill in selected}:
        selected.append(_selected(name, inventory, reason))


def _select_domain_skills(lowered: str, domain: str, selected: list[SelectedSkill], inventory: SkillInventory) -> None:
    if domain == "browser":
        if any(term in lowered for term in ("regression", "screenshot", "snapshot", "playwright")):
            _append_if_missing(selected, "playwright", inventory, "Browser regression or screenshot proof requires terminal-driven Playwright authority.")
        elif "localhost" in lowered or "page renders" in lowered or "render" in lowered:
            _append_if_missing(selected, "control-in-app-browser", inventory, "Localhost render checks should use the in-app browser surface, not a shell URL open.")

    if domain == "connected_app":
        if "gmail" in lowered:
            _append_if_missing(selected, "gmail:gmail", inventory, "Gmail work requires the Gmail connector skill plus callable tool proof.")
        if "google sheet" in lowered or "connected google sheet" in lowered:
            _append_if_missing(selected, "google-drive:google-sheets", inventory, "Connected Google Sheets work requires the Sheets connector skill and range proof.")
        if "canva" in lowered and any(term in lowered for term in ("resize", "linkedin", "instagram", "social")):
            _append_if_missing(selected, "canva:canva-resize-for-all-social-media", inventory, "Canva resize work requires the native Canva resize skill.")


def detect_skill_route(
    prompt: str,
    cwd: str | Path | None = None,
    inventory: SkillInventory | None = None,
    source_event: str = "detector",
) -> SkillHookPlan:
    inventory = inventory or load_default_skill_inventory()
    lowered = prompt.lower()
    mutates = _contains_any(lowered, MUTATING_TERMS)
    domain = _domain_for_text(lowered)
    interaction_type = _interaction_for_text(lowered, mutates, domain)
    confidence = _confidence_for_text(lowered, domain, mutates)
    confirmation_state, question = _confirmation_state(lowered, domain, mutates, confidence)
    terminal_state = _terminal_state(domain, interaction_type, mutates)

    selected = [_selected("codex-capability-router", inventory, "Nontrivial request requires route classification before action.")]
    if domain == "codex":
        selected.append(_selected("openai-docs", inventory, "Codex/OpenAI behavior must be checked against official docs."))
    _select_domain_skills(lowered, domain, selected, inventory)
    if domain == "code" and mutates:
        selected.append(_selected("superpowers:test-driven-development", inventory, "Implementation or repair requires failing tests before production code."))
        selected.append(_selected("superpowers:verification-before-completion", inventory, "Completion claims require verification output."))
    if any(term in lowered for term in ("debug", "diagnose", "root cause", "test fail", "failing test")):
        selected.append(_selected("superpowers:systematic-debugging", inventory, "Debugging requires root-cause tracing before repair."))

    action = InterpretedAction(
        verb=_verb_for_text(prompt),
        target=domain,
        domain=domain,
        mutates=mutates,
        consequence="medium" if mutates else "low",
    )
    checks = _authority_checks(lowered, inventory, mutates, confirmation_state)
    if any(check.status == "blocked" for check in checks):
        confirmation_state = "blocked"
        question = question or "Repair the blocked authority surface before proceeding?"

    return SkillHookPlan(
        prompt=prompt,
        cwd=normalize_cwd(cwd),
        source_event=source_event,
        interaction_type=interaction_type,
        domain=domain,
        consequence=action.consequence,
        confidence=confidence,
        selected_skills=selected,
        interpreted_actions=[action],
        authority_checks=checks,
        confirmation_state=confirmation_state,  # type: ignore[arg-type]
        question=question,
        terminal_state_requirement=terminal_state,
    )
