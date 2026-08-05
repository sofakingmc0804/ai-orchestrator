from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from benchmarks.validators.operation_validators import validate_task

from orchestrator.adapters.builtins import build_adapters
from orchestrator.config import Settings
from orchestrator.dispatch.receipt_enhanced import build_receipt_data
from orchestrator.governance.job_classifier import classify_with_fallback
from orchestrator.intent.interpreter import parse_intent
from orchestrator.models import BillingClass, ConsequenceTier, DispatchResult, Intent, Notification, RoutingDecision, Selection
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.routing.engine import route_intent
from orchestrator.routing.project_policy import approval_required_for_tier, load_project_policy, output_base_for_project
from orchestrator.routing.worker_routing import route_intent_worker_aware
from orchestrator.skills.detector import detect_skill_route
from orchestrator.state.store import StateStore, iso
from orchestrator.usage.tokens import extract_token_usage, flowmeter_snapshot
from orchestrator.usage.conservation_report import prepare_dispatch as _conservation_prepare, analyze_dispatch as _conservation_analyze


def _load_conservation_mode(repo_root: Path) -> str:
    """Read conservation mode from governance_policy.json. Returns 'report_only' or 'active'."""
    try:
        policy_path = repo_root / "orchestrator" / "config" / "governance_policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        return policy.get("conservation", {}).get("mode", "report_only")
    except Exception:
        return "report_only"


def _apply_conservation_levers(
    envelope: dict[str, Any],
    conservation_pre: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the 4 pre-dispatch conservation levers to the envelope (active mode).

    Levers applied:
    1. Prompt minimizer — replace raw_text with minimized_prompt
    2. Context harness — prepend prior receipt context
    3. Output directive — append directive to prompt
    4. Effort calibration — inject reasoning_effort + max_output_tokens
    """
    if not conservation_pre:
        return envelope

    out = dict(envelope)
    intent = out.get("intent", {})
    raw_text = intent.get("raw_text", "")

    # Lever 1: minimized prompt
    minimized = conservation_pre.get("minimized_prompt", "")
    if minimized and len(minimized) < len(raw_text):
        intent = dict(intent)
        intent["raw_text"] = minimized
        out["intent"] = intent

    # Lever 2: context harness (prepend)
    harness = conservation_pre.get("context_harness", "")
    if harness:
        intent = dict(out.get("intent", {}))
        intent["raw_text"] = f"{harness}\n\n{intent.get('raw_text', '')}"
        out["intent"] = intent

    # Lever 3: output directive (append)
    directive = conservation_pre.get("output_directive", "")
    if directive:
        intent = dict(out.get("intent", {}))
        intent["raw_text"] = f"{intent.get('raw_text', '')}\n\n{directive}"
        out["intent"] = intent

    # Lever 4: effort calibration
    effort = conservation_pre.get("effort", {})
    if effort:
        if effort.get("reasoning_effort"):
            out["reasoning_effort"] = effort["reasoning_effort"]
        if effort.get("max_output_tokens"):
            out["max_output_tokens"] = effort["max_output_tokens"]

    out["conservation_applied"] = True
    return out


class Dispatcher:
    def __init__(self, settings: Settings, store: StateStore, notifications: NotificationSpine):
        self.settings = settings
        self.store = store
        self.notifications = notifications
        self.adapters = build_adapters()
        self.max_attempts_per_adapter = 3
        self.retry_backoff_seconds = [0.2, 0.5]

    async def dispatch_text(
        self,
        raw_text: str,
        project_root: Path | None = None,
        job_class_override: str | None = None,
        source: str = "user",
        operation_task: dict[str, Any] | None = None,
        auto_approve_local: bool = False,
        preferred_adapter: str | None = None,
    ) -> DispatchResult:
        intent = parse_intent(raw_text)
        intent.source = source
        if operation_task is not None:
            intent.parsed_payload["operation_task"] = operation_task
        project_policy: dict[str, Any] = {}
        if project_root:
            intent.project_id = await self.store.find_project_id_for_path(project_root)
            project_policy = await self._project_policy(intent.project_id)
        await self.store.create_intent(intent)
        source_authorized_local = (
            auto_approve_local
            and source == "backlog_discovery"
            and project_root is not None
            and project_root.is_dir()
        )
        if source_authorized_local and preferred_adapter is None:
            preferred_adapter = "hermes-agent"
        if self._requires_approval(intent, project_policy) and not source_authorized_local:
            await self._request_approval(intent)
            return DispatchResult(dispatch_id="", intent_id=intent.id, adapter_name="", state="awaiting_approval")
        return await self._execute_intent(
            intent,
            project_root,
            project_policy,
            job_class_override=job_class_override,
            preferred_adapter=preferred_adapter,
        )

    async def approve_intent(self, intent_id: str, approved: bool) -> DispatchResult:
        row = await self.store.get_intent(intent_id)
        if not row:
            return DispatchResult(dispatch_id="", intent_id=intent_id, adapter_name="", state="failed", error="intent not found")
        await self.store.acknowledge_approval(intent_id)
        if not approved:
            await self.store.update_intent_state(intent_id, "rejected", completed=True)
            await self.notifications.publish(
                Notification(
                    id=f"ntf_{uuid.uuid4().hex[:12]}",
                    severity="info",
                    intent_id=intent_id,
                    title="Intent rejected",
                    body=f"Intent {intent_id} was rejected before dispatch.",
                    channels_requested=["in_app"],
                )
            )
            return DispatchResult(dispatch_id="", intent_id=intent_id, adapter_name="", state="rejected")
        intent = Intent(
            id=str(row["id"]),
            source=str(row["source"]),
            raw_text=str(row["raw_text"]),
            parsed_payload=dict(row["parsed_payload"]),
            project_id=row.get("project_id"),
            selections=[Selection.model_validate(s) for s in row["selections"]],
            consequence_tier=ConsequenceTier(str(row["consequence_tier"])),
            state="approved",
        )
        project_policy = await self._project_policy(intent.project_id)
        await self.store.update_intent_state(intent_id, "approved")
        project_root = await self._project_root(intent.project_id)
        return await self._execute_intent(intent, project_root, project_policy)

    async def _project_policy(self, project_id: str | None) -> dict[str, Any]:
        if not project_id:
            return {}
        for project in await self.store.list_projects():
            if project.get("id") == project_id:
                return load_project_policy(project.get("policy_file_path"))
        return {}

    async def _project_root(self, project_id: str | None) -> Path | None:
        if not project_id:
            return None
        for project in await self.store.list_projects():
            if project.get("id") == project_id and project.get("root_path"):
                return Path(str(project["root_path"]))
        return None

    def _requires_approval(self, intent: Intent, project_policy: dict[str, Any] | None = None) -> bool:
        return approval_required_for_tier(intent.consequence_tier.value, project_policy)

    async def _request_approval(self, intent: Intent) -> None:
        await self.notifications.publish(
            Notification(
                id=f"ntf_{uuid.uuid4().hex[:12]}",
                severity="approval_request",
                intent_id=intent.id,
                title="Approval needed",
                body=f"Intent {intent.id} requires approval before external or high-consequence action.",
                actions=[{"label": "Approve", "action_id": f"approve:{intent.id}"}, {"label": "Reject", "action_id": f"reject:{intent.id}"}],
                channels_requested=["in_app", "tray", "email"],
            )
        )

    async def _execute_intent(
        self,
        intent: Intent,
        project_root: Path | None = None,
        project_policy: dict[str, Any] | None = None,
        job_class_override: str | None = None,
        preferred_adapter: str | None = None,
    ) -> DispatchResult:
        # Phase 2: Classify intent → job_class
        classification = classify_with_fallback(intent.raw_text, override=job_class_override)
        job_class = classification["job_class"]
        skill_hook_plan = detect_skill_route(intent.raw_text, cwd=project_root or self.settings.repo_root, source_event="dispatcher")

        # Conservation report-only: prepare (measure input waste, do NOT modify envelope)
        _conservation_pre: dict[str, Any] | None = None
        _conservation_mode = _load_conservation_mode(self.settings.repo_root)
        try:
            _conservation_pre = _conservation_prepare(
                intent.raw_text, job_class, str(project_root) if project_root else None
            )
        except Exception:
            _conservation_pre = None

        # Phase 2: Load workers from DB
        workers = await self.store.db.fetch("SELECT * FROM worker_cards")
        job_class_spec = await self.store.db.fetchrow("SELECT * FROM job_classes WHERE job_class = ?", job_class)
        subscription_usage_snapshots = await self.store.list_subscription_usage_snapshots()
        budget_probes = await self.store.list_budget_probes()
        token_usage_summary = await self.store.token_usage_summary()
        operation_quality_scores = await self.store.load_live_operation_quality_scores()
        service_health = {
            str(row.get("adapter_name")): str(row.get("health_state") or "unknown")
            for row in await self.store.list_services()
            if row.get("adapter_name")
        }
        conservation_by_worker = await self.store.conservation_scores_by_worker()

        quota_state = await self.store.latest_quota_state()
        project_policy = project_policy or await self._project_policy(intent.project_id)
        if workers:
            decision = route_intent_worker_aware(
                intent,
                workers,
                job_class,
                quota_state,
                project_policy,
                job_class_spec=job_class_spec,
                budget_probes=budget_probes,
                subscription_usage_snapshots=subscription_usage_snapshots,
                token_usage_summary=token_usage_summary,
                operation_quality_scores=operation_quality_scores,
                service_health=service_health,
                conservation_by_worker=conservation_by_worker,
            )
        else:
            capabilities = await self.store.list_capabilities()
            decision = route_intent(intent, capabilities, quota_state, project_policy)

        if preferred_adapter:
            adapter = self.adapters.get(preferred_adapter)
            if adapter is None:
                await self.store.update_intent_state(intent.id, "failed", completed=True)
                return DispatchResult(
                    dispatch_id="",
                    intent_id=intent.id,
                    adapter_name=preferred_adapter,
                    state="failed",
                    error=f"preferred adapter is not registered: {preferred_adapter}",
                )
            decision.chosen_adapter = preferred_adapter
            decision.candidates_considered = [
                {
                    "adapter_name": preferred_adapter,
                    "worker_id": f"{preferred_adapter}@governed-desktop",
                    "model_id": "desktop-hermes",
                    "provider_id": "governed-desktop",
                    "billing_class": "subscription_quota",
                    "forced_for_source_backed_local_work": True,
                }
            ]
            decision.candidates_rejected = []
            decision.reasoning = f"Pinned {preferred_adapter} for source-backed local project work."

        await self.store.record_routing(decision)
        if not decision.chosen_adapter:
            await self.store.update_intent_state(intent.id, "failed", completed=True)
            await self.store.add_repair_item("router", decision.reasoning, "Review capability, cost, and quota policy; add quota or choose a local-capable route.")
            await self.notifications.publish(
                Notification(
                    id=f"ntf_{uuid.uuid4().hex[:12]}",
                    severity="error",
                    intent_id=intent.id,
                    title="No safe adapter found",
                    body=decision.reasoning,
                    channels_requested=["in_app", "tray"],
                )
            )
            return DispatchResult(dispatch_id="", intent_id=intent.id, adapter_name="", state="failed", error=decision.reasoning)
        dispatch_id = f"dsp_{uuid.uuid4().hex[:16]}"
        output_base = output_base_for_project(project_root, self.settings.home, self.settings.output_dirname, project_policy)
        out_root = output_base / iso()[:10] / intent.id
        out_root.mkdir(parents=True, exist_ok=True)
        chosen_candidate = next(
            (
                candidate
                for candidate in decision.candidates_considered
                if str(candidate.get("adapter_name") or "") == str(decision.chosen_adapter)
            ),
            {},
        )
        envelope: dict[str, Any] = {
            "intent": intent.model_dump(mode="json"),
            "dispatch_id": dispatch_id,
            "expected_output_shape": "text",
            "consequence_tier": intent.consequence_tier.value,
            "project_policy": project_policy,
            "skill_hook_plan": skill_hook_plan.receipt_payload(),
        }
        if project_root is not None:
            envelope["project_root"] = str(project_root)
        operation_task = intent.parsed_payload.get("operation_task")
        if isinstance(operation_task, dict):
            envelope["operation_task"] = operation_task
        model_hint = chosen_candidate.get("recommended_model") or chosen_candidate.get("model_id")
        if model_hint:
            envelope["model"] = str(model_hint)
        provider_hint = chosen_candidate.get("provider_id") or chosen_candidate.get("provider") or chosen_candidate.get("surface")
        if provider_hint:
            envelope["provider"] = str(provider_hint)
        await self.store.record_dispatch(
            {
                "id": dispatch_id,
                "intent_id": intent.id,
                "adapter_name": decision.chosen_adapter,
                "envelope": envelope,
                "state": "running",
                "started_at": iso(),
            }
        )
        attempts: list[dict[str, Any]] = []
        last_error = "adapter failed"
        attempted_routes: set[tuple[str, str, str]] = set()
        for candidate in decision.candidates_considered:
            adapter_name = str(candidate.get("adapter_name") or "")
            candidate_model = str(candidate.get("recommended_model") or candidate.get("model_id") or envelope.get("model") or "")
            candidate_provider = str(candidate.get("provider_id") or candidate.get("provider") or candidate.get("surface") or envelope.get("provider") or "")
            route_key = (adapter_name, candidate_model, candidate_provider)
            if route_key in attempted_routes:
                continue
            attempted_routes.add(route_key)
            adapter = self.adapters.get(adapter_name)
            if not adapter:
                last_error = f"{adapter_name} adapter is not registered"
                continue
            attempt_envelope = dict(envelope)
            if candidate_model:
                attempt_envelope["model"] = candidate_model
            if candidate_provider:
                attempt_envelope["provider"] = candidate_provider
            # Conservation active mode: apply levers to the envelope
            if _conservation_mode == "active" and _conservation_pre:
                attempt_envelope = _apply_conservation_levers(attempt_envelope, _conservation_pre)
            for attempt_number in range(1, self.max_attempts_per_adapter + 1):
                attempt_id = f"att_{uuid.uuid4().hex[:16]}"
                attempt_started = iso()
                stop_candidate_retries = False
                try:
                    result = await adapter.dispatch(attempt_envelope)
                    token_usage = self._token_usage_for_attempt(
                        result,
                        attempt_envelope,
                        adapter_name,
                        attempt_id,
                        dispatch_id,
                        intent.id,
                        budget_probes,
                    )
                    if result.get("ok"):
                        attempt = {
                            "id": attempt_id,
                            "dispatch_id": dispatch_id,
                            "intent_id": intent.id,
                            "adapter_name": adapter_name,
                            "attempt_number": attempt_number,
                            "state": "completed",
                            "started_at": attempt_started,
                            "completed_at": iso(),
                            "detail": {"model": result.get("model"), "token_usage": token_usage},
                        }
                        attempts.append(attempt)
                        await self.store.record_token_usage(token_usage)
                        await self.store.record_dispatch_attempt(attempt)
                        return await self._complete_dispatch(
                            dispatch_id,
                            intent,
                            adapter_name,
                            attempt_envelope,
                            out_root,
                            result,
                            decision,
                            attempts,
                            job_class,
                            budget_probes,
                            subscription_usage_snapshots,
                            _conservation_pre=_conservation_pre,
                        )
                    last_error = str(result.get("error") or "adapter returned ok=false").strip() or "adapter returned ok=false"
                    repair_action = str(result.get("repair_action") or "Inspect adapter logs and provider configuration.")
                except Exception as exc:
                    last_error = str(exc).strip() or exc.__class__.__name__
                    repair_action = "Inspect adapter logs and provider configuration."
                    if "timeout" in exc.__class__.__name__.lower() or "timed out" in last_error.lower():
                        repair_action = "Verify the provider command returns inside the adapter timeout and disable slow fallbacks."
                        stop_candidate_retries = True
                    token_usage = self._token_usage_for_attempt(
                        {"ok": False, "text": "", "error": last_error},
                        attempt_envelope,
                        adapter_name,
                        attempt_id,
                        dispatch_id,
                        intent.id,
                        budget_probes,
                    )
                attempt = {
                    "id": attempt_id,
                    "dispatch_id": dispatch_id,
                    "intent_id": intent.id,
                    "adapter_name": adapter_name,
                    "attempt_number": attempt_number,
                    "state": "failed",
                    "started_at": attempt_started,
                    "completed_at": iso(),
                    "error": last_error,
                    "detail": {"repair_action": repair_action, "token_usage": token_usage},
                }
                attempts.append(attempt)
                await self.store.record_token_usage(token_usage)
                await self.store.record_dispatch_attempt(attempt)
                if stop_candidate_retries:
                    break
                if attempt_number < self.max_attempts_per_adapter:
                    await asyncio.sleep(self.retry_backoff_seconds[min(attempt_number - 1, len(self.retry_backoff_seconds) - 1)])
            await self.store.add_repair_item(adapter_name, last_error, repair_action)
        await self.store.record_dispatch(
            {
                "id": dispatch_id,
                "intent_id": intent.id,
                "adapter_name": decision.chosen_adapter,
                "envelope": envelope,
                "state": "failed",
                "completed_at": iso(),
                "error": last_error,
            }
        )
        await self.store.update_intent_state(intent.id, "failed", completed=True)
        await self.notifications.publish(
            Notification(
                id=f"ntf_{uuid.uuid4().hex[:12]}",
                severity="error",
                intent_id=intent.id,
                title="Dispatch failed",
                body=f"{decision.chosen_adapter}: {last_error}",
                channels_requested=["in_app", "tray"],
            )
        )
        return DispatchResult(dispatch_id=dispatch_id, intent_id=intent.id, adapter_name=str(decision.chosen_adapter), state="failed", error=last_error)

    async def prove_adapter(
        self,
        adapter_name: str,
        prompt: str,
        capability_id: str | None = None,
        allow_subscription: bool = False,
        selections: list[Selection] | None = None,
    ) -> DispatchResult:
        capabilities = await self.store.list_capabilities()
        matching_caps = [
            dict(cap)
            for cap in capabilities
            if str(cap.get("adapter_name") or "") == adapter_name
            and (capability_id is None or str(cap.get("capability_id") or "") == capability_id)
            and int(cap.get("enabled") or 0) == 1
        ]
        if not matching_caps:
            return DispatchResult(dispatch_id="", intent_id="", adapter_name=adapter_name, state="failed", error=f"No enabled capability found for adapter {adapter_name}.")
        candidate = matching_caps[0]
        billing_class = str(candidate.get("billing_class") or BillingClass.UNKNOWN_COST.value)
        if billing_class in {BillingClass.METERED_EXTRA_COST.value, BillingClass.UNKNOWN_COST.value}:
            return DispatchResult(dispatch_id="", intent_id="", adapter_name=adapter_name, state="failed", error=f"Proof denied for {adapter_name}: forbidden billing class {billing_class}.")
        if billing_class == BillingClass.SUBSCRIPTION_QUOTA.value and not allow_subscription:
            return DispatchResult(dispatch_id="", intent_id="", adapter_name=adapter_name, state="failed", error=f"Proof denied for {adapter_name}: subscription quota requires --allow-subscription.")
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            await self.store.add_or_get_open_repair_item(adapter_name, "Adapter proof requested but adapter is not registered.", "Register the adapter before proof dispatch.")
            return DispatchResult(dispatch_id="", intent_id="", adapter_name=adapter_name, state="failed", error=f"{adapter_name} adapter is not registered.")

        intent = Intent(
            id=f"int_{uuid.uuid4().hex[:16]}",
            source="adapter_proof",
            raw_text=prompt,
            parsed_payload={"verb": "prove", "object": adapter_name, "required_capability": str(candidate.get("capability_id") or "local_chat")},
            selections=selections or [],
            consequence_tier=ConsequenceTier.LOW,
        )
        await self.store.create_intent(intent)
        decision = RoutingDecision(
            intent_id=intent.id,
            chosen_adapter=adapter_name,
            candidates_considered=[candidate],
            candidates_rejected=[],
            reasoning=f"Explicit adapter proof selected {adapter_name}; billing class {billing_class}; no fallback permitted.",
        )
        await self.store.record_routing(decision)
        dispatch_id = f"dsp_{uuid.uuid4().hex[:16]}"
        out_root = self.settings.home / "adapter-proof" / adapter_name / self.settings.output_dirname / iso()[:10] / intent.id
        out_root.mkdir(parents=True, exist_ok=True)
        envelope: dict[str, Any] = {
            "intent": intent.model_dump(mode="json"),
            "dispatch_id": dispatch_id,
            "expected_output_shape": "text",
            "consequence_tier": intent.consequence_tier.value,
            "project_policy": {"proof_mode": True, "forced_adapter": adapter_name},
        }
        await self.store.record_dispatch(
            {
                "id": dispatch_id,
                "intent_id": intent.id,
                "adapter_name": adapter_name,
                "envelope": envelope,
                "state": "running",
                "started_at": iso(),
            }
        )
        attempt_id = f"att_{uuid.uuid4().hex[:16]}"
        attempt_started = iso()
        try:
            result = await adapter.dispatch(envelope)
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "repair_action": "Inspect adapter proof logs and provider configuration."}
        if result.get("ok"):
            token_usage = self._token_usage_for_attempt(result, envelope, adapter_name, attempt_id, dispatch_id, intent.id, [])
            attempt = {
                "id": attempt_id,
                "dispatch_id": dispatch_id,
                "intent_id": intent.id,
                "adapter_name": adapter_name,
                "attempt_number": 1,
                "state": "completed",
                "started_at": attempt_started,
                "completed_at": iso(),
                "detail": {"model": result.get("model"), "proof_mode": True, "token_usage": token_usage},
            }
            await self.store.record_token_usage(token_usage)
            await self.store.record_dispatch_attempt(attempt)
            return await self._complete_dispatch(dispatch_id, intent, adapter_name, envelope, out_root, result, decision, [attempt], "adapter_proof", [])

        error = str(result.get("error") or "adapter proof returned ok=false")
        repair_action = str(result.get("repair_action") or "Inspect adapter logs and provider configuration.")
        token_usage = self._token_usage_for_attempt(result, envelope, adapter_name, attempt_id, dispatch_id, intent.id, [])
        attempt = {
            "id": attempt_id,
            "dispatch_id": dispatch_id,
            "intent_id": intent.id,
            "adapter_name": adapter_name,
            "attempt_number": 1,
            "state": "failed",
            "started_at": attempt_started,
            "completed_at": iso(),
            "error": error,
            "detail": {"repair_action": repair_action, "proof_mode": True, "token_usage": token_usage},
        }
        await self.store.record_token_usage(token_usage)
        await self.store.record_dispatch_attempt(attempt)
        await self.store.add_or_get_open_repair_item(adapter_name, error, repair_action)
        await self.store.record_dispatch(
            {
                "id": dispatch_id,
                "intent_id": intent.id,
                "adapter_name": adapter_name,
                "envelope": envelope,
                "state": "failed",
                "completed_at": iso(),
                "error": error,
            }
        )
        await self.store.update_intent_state(intent.id, "failed", completed=True)
        await self.notifications.publish(
            Notification(
                id=f"ntf_{uuid.uuid4().hex[:12]}",
                severity="error",
                intent_id=intent.id,
                title="Adapter proof failed",
                body=f"{adapter_name}: {error}",
                channels_requested=["in_app", "tray"],
            )
        )
        return DispatchResult(dispatch_id=dispatch_id, intent_id=intent.id, adapter_name=adapter_name, state="failed", error=error)

    def _token_usage_for_attempt(
        self,
        result: dict[str, Any],
        envelope: dict[str, Any],
        adapter_name: str,
        attempt_id: str,
        dispatch_id: str,
        intent_id: str,
        budget_probes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        usage = extract_token_usage(result, envelope, adapter_name)
        provider = usage.get("provider") or envelope.get("provider") or adapter_name
        probe = self._budget_probe_for_provider(str(provider), budget_probes)
        metered = flowmeter_snapshot(usage, probe)
        return {
            "id": f"tok_{uuid.uuid4().hex[:16]}",
            "dispatch_id": dispatch_id,
            "attempt_id": attempt_id,
            "intent_id": intent_id,
            "success": bool(result.get("ok")),
            **metered,
            "created_at": iso(),
        }

    @staticmethod
    def _budget_probe_for_provider(provider: str, budget_probes: list[dict[str, Any]]) -> dict[str, Any] | None:
        normalized = provider.strip()
        keys = {normalized, normalized.replace("-", "_"), normalized.replace("_", "-")}
        aliases = {
            "ollama-local": "ollama",
            "ollama-http": "ollama",
            "ollama-cli": "ollama",
            "ollama-cloud": "ollama",
            "github-copilot": "github_copilot",
            "copilot-gh": "github_copilot",
            "claude-max": "claude",
            "claude-code-cli": "claude",
            "gemini-cli": "gemini",
            "gemini-oauth": "gemini",
            "codex-chatgpt": "codex",
            "codex-cli": "codex",
            "hermes-nous": "nous",
            "hermes-agent": "nous",
        }
        alias = aliases.get(normalized) or aliases.get(normalized.replace("_", "-"))
        if alias:
            keys.update({alias, alias.replace("-", "_"), alias.replace("_", "-")})
        for probe in budget_probes:
            probe_provider = str(probe.get("provider_id") or "")
            if probe_provider in keys or probe_provider.replace("-", "_") in keys or probe_provider.replace("_", "-") in keys:
                return probe
        return None

    async def _complete_dispatch(
        self,
        dispatch_id: str,
        intent: Intent,
        adapter_name: str,
        envelope: dict[str, Any],
        out_root: Path,
        result: dict[str, Any],
        decision: Any,
        attempts: list[dict[str, Any]],
        job_class: str,
        budget_probes: list[dict[str, Any]],
        subscription_usage_snapshots: list[dict[str, Any]] | None = None,
        _conservation_pre: dict[str, Any] | None = None,
    ) -> DispatchResult:
        result_path = out_root / "result.txt"
        receipt_path = out_root / "receipt.json"
        context_path = out_root / "context.json"
        text = str(result.get("text") or "")
        result_path.write_text(text, encoding="utf-8")
        chosen_candidate: dict[str, Any] = next(
            (candidate for candidate in decision.candidates_considered if str(candidate.get("adapter_name") or "") == adapter_name),
            {},
        )
        receipt = {
            "dispatch_id": dispatch_id,
            "service": adapter_name,
            "capability": intent.parsed_payload.get("required_capability"),
            "model": result.get("model"),
            "tokens_in": sum(int(((attempt.get("detail") or {}).get("token_usage") or {}).get("tokens_in") or 0) for attempt in attempts),
            "tokens_out": sum(int(((attempt.get("detail") or {}).get("token_usage") or {}).get("tokens_out") or 0) for attempt in attempts),
            "cost_class": str(chosen_candidate.get("billing_class") or "unknown_cost"),
            "success": True,
            "output_summary": text[:500],
            "routing_decision": decision.model_dump(mode="json"),
            "failover_ladder": decision.candidates_considered,
            "attempts": attempts,
            "output_path": str(result_path),
            "receipt_path": str(receipt_path),
            "worker_id": chosen_candidate.get("worker_id"),
            "job_class": job_class,
            "routing_reasoning": decision.reasoning,
            "budget_state_json": json.dumps(
                {
                    "subscription_usage_snapshots": subscription_usage_snapshots or [],
                    "budget_probes_fallback": budget_probes,
                },
                default=str,
            ),
            # Integrity: this code path only runs after a real adapter.dispatch()
            # returned ok=True, so persist the raw provider evidence and stamp it
            # 'live'. spec-status counts a dispatch as proven only on this stamp.
            "raw_output": (json.dumps(result.get("raw"), default=str)[:20000] if result.get("raw") is not None else text[:20000]),
            "proof_kind": "live",
        }
        quality_score = await self._score_operation_dispatch(dispatch_id, envelope, result, chosen_candidate, job_class)
        if quality_score:
            receipt["operation_quality_score"] = quality_score
        skill_hook_plan = envelope.get("skill_hook_plan") if isinstance(envelope.get("skill_hook_plan"), dict) else {}
        receipt.update(
            {
                "skill_hook_plan_id": skill_hook_plan.get("id"),
                "selected_skills": skill_hook_plan.get("selected_skills", []),
                "interpreted_actions": skill_hook_plan.get("interpreted_actions", []),
                "authority_checks": skill_hook_plan.get("authority_checks", []),
                "terminal_state_requirement": skill_hook_plan.get("terminal_state_requirement"),
            }
        )
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        context_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        await self.store.resolve_repair_items(adapter_name, {"dispatch_id": dispatch_id, "intent_id": intent.id})
        await self.store.record_dispatch(
            {
                "id": dispatch_id,
                "intent_id": intent.id,
                "adapter_name": adapter_name,
                "envelope": envelope,
                "state": "completed",
                "completed_at": iso(),
                "output_path": str(result_path),
            },
            receipt=receipt,
        )
        await self.store.update_intent_state(intent.id, "completed", completed=True)
        await self.notifications.publish(
            Notification(
                id=f"ntf_{uuid.uuid4().hex[:12]}",
                severity="info",
                intent_id=intent.id,
                title="Dispatch completed",
                body=str(result_path),
                channels_requested=["in_app", "tray"],
            )
        )

        # Conservation report-only: analyze output and record the report
        try:
            _post = _conservation_analyze(text, job_class)
            _report = {
                "id": f"cr_{dispatch_id}",
                "dispatch_id": dispatch_id,
                "job_class": job_class,
                "original_length": (_conservation_pre or {}).get("original_length", len(text)),
                "minimized_length": (_conservation_pre or {}).get("minimized_length", len(text)),
                "conservation_score": _post.get("conservation_score", 1.0),
                "waste_modes": _post.get("waste_modes", []),
                "notes": _post.get("notes", ""),
            }
            await self.store.record_conservation_report(_report)
        except Exception:
            pass

        return DispatchResult(dispatch_id=dispatch_id, intent_id=intent.id, adapter_name=adapter_name, state="completed", output_path=result_path, result_text=text, receipt=receipt)

    async def _score_operation_dispatch(
        self,
        dispatch_id: str,
        envelope: dict[str, Any],
        result: dict[str, Any],
        chosen_candidate: dict[str, Any],
        job_class: str,
    ) -> dict[str, Any] | None:
        task = envelope.get("operation_task")
        if not isinstance(task, dict):
            return None
        raw_output = str(result.get("text") or "")
        validation = validate_task(task, raw_output)
        scores = validation.get("scores") if isinstance(validation.get("scores"), dict) else {}
        composite = float(scores.get("composite") or 0.0)
        worker_id = str(chosen_candidate.get("worker_id") or chosen_candidate.get("model_id") or chosen_candidate.get("adapter_name") or "unknown")
        score = {
            "dispatch_id": dispatch_id,
            "worker_id": worker_id,
            "operation_domain": str(task.get("domain_id") or job_class),
            "validator_name": str(validation.get("validator") or task.get("validator") or "unknown"),
            "composite_score": composite,
            "dimensional_scores": scores,
            "task_id": task.get("task_id"),
            "proof_kind": "live",
            "validation": validation,
            "created_at": iso(),
        }
        await self.store.record_operation_quality_score(score)
        return score
