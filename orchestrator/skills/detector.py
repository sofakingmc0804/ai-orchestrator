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
    "fill",
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
READ_ONLY_DISCOVERY_PATTERNS = (
    r"\bambient suggestion candidates\b",
    r"\byour task is to determine if any suggestions should be excluded\b",
)
CONNECTED_APP_TOOL_PROOF_TERMS = (
    "calendar",
    "canva",
    "connected app",
    "connected apps",
    "github",
    "gmail",
    "google drive",
    "google sheet",
    "mcp source",
    "mcp sources",
    "tool discovery",
)
CHROME_CONTROL_TERMS = (
    "active chrome",
    "active tab",
    "browser extension",
    "chrome browser",
    "chrome extension",
    "codex chrome",
    "logged-in chrome",
)
NON_INTERRUPTING_APP_ROUTE_TERMS = (
    "app-local session",
    "app transcript",
    "app-owned transcript",
    "claude desktop",
    "custom uri",
    "local-agent",
    "local agent",
    "non-active task chat",
    "non active task chat",
    "non-foreground",
    "not interrupting",
    "without interrupting",
    "without taking over",
)
HARD_DENY_CHECK_NAMES = {
    "forbidden_metered_route",
    "unapproved_external_send",
    "fp_009_non_read_tool_limit",
    "gate_0_read_before_write",
    "gate_6_predecessor_retirement",
}
FORBIDDEN_METERED_ROUTE_PATTERNS = (
    r"\bmetered\b.*\b(api|provider|route|model)\b",
    r"\b(openai|anthropic|gemini)\s+api\b.*\b(dispatch|route|call|use)\b",
    r"\b--provider\s+(openai|anthropic|gemini|claude|gpt)\b",
    r"\bapi[_-]?key\b.*\b(openai|anthropic|gemini)\b",
)
UNAPPROVED_EXTERNAL_SEND_PATTERNS = (
    r"\bgmail_send\b",
    r"\bgmail\.send\b",
    r"\b(send|submit|post)\b.*\bexternal\b.*\b(email|address|recipient|customer)\b",
)
FP_009_PATTERNS = (
    r"\bfp-?009\b.*\b(three|3|more than two|>2)\b",
    r"\bmore than two\b.*\bnon[- ]read\b.*\b(tool|call|step)s?\b",
)
GATE_0_PATTERNS = (
    r"\bgate[- ]?0\b.*\b(skip|without|violate)\b.*\bread\b",
    r"\bwrite\b.*\bwithout\b.*\bread(?:ing)?\b",
)
GATE_6_PATTERNS = (
    r"\bgate[- ]?6\b.*\b(skip|without|violate)\b.*\b(retire|predecessor)\b",
    r"\bnew canonical\b.*\bwithout\b.*\b(retiring|retire)\b",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _is_dangerous_destructive(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in DANGEROUS_DESTRUCTIVE_PATTERNS)


def _is_read_only_discovery_prompt(text: str) -> bool:
    if any(re.search(pattern, text) for pattern in READ_ONLY_DISCOVERY_PATTERNS):
        return True
    has_codex_suggestion_envelope = (
        re.search(r"\bgenerate\s+0\s+to\s+\d+\s+hyperpersonalized suggestions\b", text) is not None
        or re.search(r"\breturn\s+0\s+to\s+\d+\s+fresh suggestions\b", text) is not None
    )
    return has_codex_suggestion_envelope and "recent codex threads" in text and "local project" in text


def _requires_connected_app_tool_proof(text: str) -> bool:
    return _contains_any(text, CONNECTED_APP_TOOL_PROOF_TERMS)


def _requests_chrome_control(text: str) -> bool:
    if _contains_any(text, CHROME_CONTROL_TERMS):
        return True
    return "chrome" in text and any(term in text for term in ("extension", "profile", "tab"))


def _requests_computer_control(text: str) -> bool:
    return any(
        term in text
        for term in (
            "computer use",
            "computer control",
            "visible desktop",
            "visible app",
            "foreground app",
            "active desktop",
            "mouse",
            "keyboard",
        )
    )


def _requests_visible_desktop_or_chrome_authority(text: str) -> bool:
    if _requests_chrome_control(text):
        return True
    if _requests_computer_control(text):
        return True
    return any(term in text for term in ("visible chrome", "visible browser", "active tab", "foreground window"))


def _requests_non_interrupting_app_route(text: str) -> bool:
    return _contains_any(text, NON_INTERRUPTING_APP_ROUTE_TERMS) and any(
        term in text for term in ("app", "claude", "desktop", "task chat", "transcript", "ui")
    )


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


def _domain_for_text(lowered: str, read_only_discovery: bool = False) -> str:
    if read_only_discovery:
        return "discovery"
    if _requests_non_interrupting_app_route(lowered):
        return "browser"
    if any(term in lowered for term in ("gmail", "email", "calendar", "drive", "docs", "sheet", "canva", "github")):
        return "connected_app"
    if _requests_chrome_control(lowered) or _requests_computer_control(lowered):
        return "browser"
    if _contains_any(lowered, OPENAI_TERMS):
        return "codex"
    if any(term in lowered for term in ("browser", "localhost", "website", "tab")):
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


def _confirmation_state(lowered: str, domain: str, mutates: bool, confidence: float) -> tuple[str, str | None]:
    if _contains_any(lowered, CONFIRMATION_TERMS):
        return "confirmed", None
    return "not_required", None


def _authority_checks(lowered: str, inventory: SkillInventory, mutates: bool, confirmation_state: str) -> list[AuthorityCheck]:
    checks = [_inventory_check(inventory)]
    checks.extend(_hard_deny_authority_checks(lowered))
    if _requests_non_interrupting_app_route(lowered):
        checks.append(
            AuthorityCheck(
                name="non_interrupting_app_route_order",
                status="required",
                reason=(
                    "App UI/runtime proof must try connector/API/export, custom URI or protocol activation, app-local "
                    "session artifacts, app transcript readback, headless or isolated browser, and targeted UI Automation "
                    "InvokePattern before visible Computer Use. Visible control requires a scoped lease or explicit idle-window condition."
                ),
            )
        )
    if _requests_visible_desktop_or_chrome_authority(lowered):
        checks.append(
            AuthorityCheck(
                name="desktop_control_lease",
                status="required",
                reason=(
                    "Browser or computer-control authority requested. Prefer connector/API/export/headless or isolated routes "
                    "when they satisfy the task. Use Chrome/browser control or Computer Use only inside a scoped lease, "
                    "or when Matt is not actively using this desktop UI and the idle-window condition is explicit."
                ),
            )
        )
    else:
        checks.append(AuthorityCheck(name="desktop_control_lease", status="passed", reason="No visible browser or computer-control route requested."))
    if _requires_connected_app_tool_proof(lowered):
        checks.append(
            AuthorityCheck(
                name="connected_app_tool_proof",
                status="required",
                reason="Connected app work requires callable tool proof before live data claims.",
                required_tool="tool_search",
            )
        )
    if confirmation_state == "confirmed":
        checks.append(AuthorityCheck(name="route_confirmation", status="passed", reason="Prompt explicitly confirmed the interpreted skill route."))
    return checks


def _hard_deny_authority_checks(lowered: str) -> list[AuthorityCheck]:
    checks: list[AuthorityCheck] = []
    if any(re.search(pattern, lowered) for pattern in FORBIDDEN_METERED_ROUTE_PATTERNS):
        checks.append(
            AuthorityCheck(
                name="forbidden_metered_route",
                status="blocked",
                reason="Owner ruleset blocks metered API/provider routes; use subscription, flat-rate, local, or owner-approved budget lanes.",
            )
        )
    if any(re.search(pattern, lowered) for pattern in UNAPPROVED_EXTERNAL_SEND_PATTERNS):
        checks.append(
            AuthorityCheck(
                name="unapproved_external_send",
                status="blocked",
                reason="Owner ruleset blocks unapproved external sends; produce an owner-facing packet or draft-only artifact instead.",
            )
        )
    if any(re.search(pattern, lowered) for pattern in FP_009_PATTERNS):
        checks.append(
            AuthorityCheck(
                name="fp_009_non_read_tool_limit",
                status="blocked",
                reason="Owner ruleset blocks FP-009 plans with more than two non-read tool calls before approval.",
            )
        )
    if any(re.search(pattern, lowered) for pattern in GATE_0_PATTERNS):
        checks.append(
            AuthorityCheck(
                name="gate_0_read_before_write",
                status="blocked",
                reason="Owner ruleset blocks Gate 0 violations: read the live authority before mutating.",
            )
        )
    if any(re.search(pattern, lowered) for pattern in GATE_6_PATTERNS):
        checks.append(
            AuthorityCheck(
                name="gate_6_predecessor_retirement",
                status="blocked",
                reason="Owner ruleset blocks Gate 6 violations: retire predecessors in the same change.",
            )
        )
    return checks


def _append_if_missing(selected: list[SelectedSkill], name: str, inventory: SkillInventory, reason: str) -> None:
    if name not in {skill.name for skill in selected}:
        selected.append(_selected(name, inventory, reason))


def _select_domain_skills(lowered: str, domain: str, selected: list[SelectedSkill], inventory: SkillInventory) -> None:
    if domain == "browser":
        if _requests_non_interrupting_app_route(lowered):
            return
        if any(term in lowered for term in ("regression", "screenshot", "snapshot", "playwright")):
            _append_if_missing(selected, "playwright", inventory, "Browser regression or screenshot proof requires terminal-driven Playwright authority.")
        elif "localhost" in lowered or "page renders" in lowered or "render" in lowered:
            _append_if_missing(selected, "control-in-app-browser", inventory, "Localhost render checks should use the in-app browser surface, not a shell URL open.")
        elif _requests_chrome_control(lowered):
            _append_if_missing(selected, "control-chrome", inventory, "Credential-bearing Chrome extension or active-tab work requires the Chrome control skill.")
        elif _requests_computer_control(lowered):
            _append_if_missing(selected, "computer-use", inventory, "Visible desktop or app interaction requires the Computer Use route with idle-window or scoped lease authority.")

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
    read_only_discovery = _is_read_only_discovery_prompt(lowered)
    mutates = False if read_only_discovery else _contains_any(lowered, MUTATING_TERMS)
    domain = _domain_for_text(lowered, read_only_discovery=read_only_discovery)
    interaction_type = "investigate" if read_only_discovery else _interaction_for_text(lowered, mutates, domain)
    confidence = 0.88 if read_only_discovery else _confidence_for_text(lowered, domain, mutates)
    confirmation_state, question = _confirmation_state(lowered, domain, mutates, confidence)
    terminal_state = _terminal_state(domain, interaction_type, mutates)

    selected = [_selected("codex-capability-router", inventory, "Nontrivial request requires route classification before action.")]
    if domain == "codex" and not read_only_discovery:
        selected.append(_selected("openai-docs", inventory, "Codex/OpenAI behavior must be checked against official docs."))
    _select_domain_skills(lowered, domain, selected, inventory)
    if domain in {"code", "codex"} and mutates:
        selected.append(_selected("superpowers:test-driven-development", inventory, "Implementation or repair requires failing tests before production code."))
        selected.append(_selected("superpowers:verification-before-completion", inventory, "Completion claims require verification output."))
    if not read_only_discovery and any(term in lowered for term in ("debug", "diagnose", "root cause", "test fail", "failing test")):
        selected.append(_selected("superpowers:systematic-debugging", inventory, "Debugging requires root-cause tracing before repair."))

    action = InterpretedAction(
        verb="discover" if read_only_discovery else _verb_for_text(prompt),
        target="suggestions" if read_only_discovery else domain,
        domain=domain,
        mutates=mutates,
        consequence="medium" if mutates else "low",
    )
    checks = _authority_checks(lowered, inventory, mutates, confirmation_state)
    if any(check.status == "blocked" for check in checks):
        confirmation_state = "blocked"
        question = question or "Repair the blocked authority surface before proceeding?"
    enforcement_mode = "hard_deny" if any(check.status == "blocked" and check.name in HARD_DENY_CHECK_NAMES for check in checks) else "advisory"

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
        enforcement_mode=enforcement_mode,
    )
