from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator.autopilot.watchers import scan_autopilot_roots_once
from orchestrator.benchmarks.latency import run_selection_latency_benchmark
from orchestrator.config import Settings
from orchestrator.connectors import build_config_preview, connector_templates, validate_connector_config
from orchestrator.discovery.auth import probe_auth_and_quota, quota_snapshots
from orchestrator.discovery.budget_probes import probe_to_dict, run_all_probes
from orchestrator.discovery.projects import discover_projects
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.hermes.brain_bridge import complete_workspace_hermes_turn, prepare_workspace_hermes_turn
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.notifications.subscribers.runner import run_all_subscribers_once
from orchestrator.process.recovery import repair_core_services
from orchestrator.process.repair_retry import retry_open_repairs
from orchestrator.process.supervisor import run_supervisor_tick, start_supervisor_thread
from orchestrator.routing.brain import route_brain
from orchestrator.scheduler.migration import migrate_legacy_scheduled_tasks
from orchestrator.scheduler.tasks import run_scheduler_once, trigger_scheduler_task
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore
from orchestrator.discovery.subscription_usage import build_api_budget_payload, build_subscription_usage_payload
from orchestrator.ui.dashboard import build_failover_events_payload, build_governance_receipts_payload, build_quality_leaderboard_payload
from orchestrator.ui.dashboard_status import build_dashboard_status
from orchestrator.usage.accounting import build_token_accounting_payload
from orchestrator.usage.flow import build_token_flow_payload
from orchestrator.workspace_runtime import WorkspacePolicyError, build_platform_console_payload, workspace_connector_context


def _business_snapshot_generated_at(settings: Settings) -> str | None:
    """generated_at of the business snapshot the Command Center reads, for freshness checks."""
    path = settings.repo_root / "orchestrator" / "ui" / "static" / "business_snapshot.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("generated_at") if isinstance(payload, dict) else None
    return str(value) if value else None


class IntentRequest(BaseModel):
    text: str
    workspace_id: str
    project_root: str | None = None


class RouteRequest(BaseModel):
    text: str
    workspace_id: str
    job_class: str | None = None
    project_root: str | None = None


class AdapterProofRequest(BaseModel):
    adapter_name: str
    prompt: str = "Adapter proof: answer with OK."
    capability: str | None = None
    allow_subscription: bool = False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    store = StateStore(settings)
    notifications = NotificationSpine(settings.notifications_path, store)
    dispatcher = Dispatcher(settings, store, notifications)
    project_roots = [settings.repo_root]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await store.initialize()
        await store.bootstrap_workspace_roots()
        await store.seed_default_mode_packs()
        services, caps = await discover_services_and_capabilities()
        await store.upsert_services(services)
        await store.upsert_capabilities(caps)
        projects = discover_projects(project_roots, max_depth=3)
        await store.upsert_projects(projects)

        async def defer_startup_repair(_store: StateStore) -> dict[str, object]:
            return {"state": "skipped", "reason": "deferred_to_supervisor_thread"}

        app.state.supervisor_startup_receipt = await run_supervisor_tick(settings, store, repair_core=defer_startup_repair)
        app.state.supervisor_thread = start_supervisor_thread(settings)
        try:
            yield
        finally:
            thread = getattr(app.state, "supervisor_thread", None)
            stop_event = getattr(thread, "stop_event", None)
            if stop_event is not None:
                stop_event.set()
            join = getattr(thread, "join", None)
            if callable(join):
                join(timeout=5)

    app = FastAPI(title="Platform Console", version="0.2.0", lifespan=lifespan)
    # Hermes' development renderer is an isolated local origin.  Production
    # Electron builds use the local ``null`` file origin.  No remote browser
    # origin is admitted, and these endpoints do not carry credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(null|http://(127\.0\.0\.1|localhost):5174)$",
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )
    static_dir = Path(__file__).with_suffix("").parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/workers", response_class=HTMLResponse)
    async def workers_page() -> str:
        return (static_dir / "workers.html").read_text(encoding="utf-8")

    @app.get("/budget", response_class=HTMLResponse)
    async def budget_page() -> str:
        return (static_dir / "budget.html").read_text(encoding="utf-8")

    @app.get("/connectors", response_class=HTMLResponse)
    async def connectors_page() -> str:
        return (static_dir / "connectors.html").read_text(encoding="utf-8")

    @app.get("/receipts", response_class=HTMLResponse)
    async def receipts_page() -> str:
        return (static_dir / "receipts.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    async def dashboard_status() -> dict[str, object]:
        quota_state = await store.latest_quota_state()
        return {
            "dispatches": await store.list_dispatches(limit=100),
            "auth_quota": {
                "source": "cached_quota_state",
                "providers": quota_state,
            },
            "state_path": str(settings.state_path),
            "notifications_path": str(settings.notifications_path),
        }

    @app.get("/api/platform-console")
    async def platform_console() -> dict[str, object]:
        return await build_platform_console_payload(store)

    @app.post("/api/workspaces/bootstrap")
    async def bootstrap_workspaces() -> dict[str, object]:
        workspaces = await store.bootstrap_workspace_roots()
        packs = await store.seed_default_mode_packs()
        return {"workspaces": workspaces, "mode_packs": packs}

    @app.get("/api/workspaces")
    async def workspaces() -> list[dict[str, object]]:
        return await store.list_workspaces()

    @app.post("/api/work-packets")
    async def create_work_packet(payload: dict[str, object]) -> dict[str, object]:
        try:
            raw_payload = payload.get("payload")
            return await store.create_work_packet(
                workspace_id=str(payload.get("workspace_id") or ""),
                intent=str(payload.get("intent") or ""),
                payload=raw_payload if isinstance(raw_payload, dict) else {},
                mode_pack_id=str(payload.get("mode_pack_id") or "") or None,
                consumer=str(payload.get("consumer") or ""),
            )
        except WorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/transfer-proposals")
    async def create_transfer_proposal(payload: dict[str, object]) -> dict[str, object]:
        try:
            return await store.propose_transfer(
                work_packet_id=str(payload.get("work_packet_id") or ""),
                target_workspace_id=str(payload.get("target_workspace_id") or ""),
                summary=str(payload.get("summary") or ""),
            )
        except WorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/transfer-proposals/{proposal_id}/decision")
    async def decide_transfer_proposal(proposal_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            decided_by = str(payload.get("decided_by") or "").strip()
            if not decided_by:
                raise WorkspacePolicyError("a transfer decision requires a named owner")
            return await store.decide_transfer(
                proposal_id,
                approve=bool(payload.get("approve")),
                decided_by=decided_by,
            )
        except WorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/hermes/workspace-context")
    async def hermes_workspace_context(workspace_id: str | None = None) -> dict[str, object]:
        if workspace_id not in {"personal", "example"}:
            raise HTTPException(status_code=409, detail="Hermes requires a Personal or Example workspace selection")
        # A proposal is rendered in its source workspace only.  Example receives
        # no source summary, packet id, payload, or memory before the owner
        # approves the transfer.
        proposals = [
            proposal
            for proposal in await store.list_transfer_proposals(state="proposed")
            if proposal.get("source_workspace_id") == workspace_id
        ]
        return {
            "workspaces": [row for row in await store.list_workspaces() if row.get("id") in {"personal", "example"}],
            "mode_packs": await store.list_mode_packs(),
            "transfer_cards": proposals,
            "connector_access": workspace_connector_context(workspace_id, len(await store.list_services())),
        }

    @app.get("/api/hermes/workspace-meter")
    async def hermes_workspace_meter(workspace_id: str | None = None) -> dict[str, object]:
        try:
            if workspace_id not in {"personal", "example"}:
                raise WorkspacePolicyError("Hermes meter requires a Personal or Example workspace selection")
            return await store.workspace_meter(workspace_id)
        except WorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/hermes/workspace-turns/prepare")
    async def prepare_hermes_workspace_turn(payload: dict[str, object]) -> dict[str, object]:
        checks_raw = payload.get("skill_capability_checks")
        checks = {str(key): bool(value) for key, value in checks_raw.items()} if isinstance(checks_raw, dict) else {}
        argv_raw = payload.get("argv")
        argv = [str(item) for item in argv_raw] if isinstance(argv_raw, list) else []
        try:
            return await prepare_workspace_hermes_turn(
                settings,
                workspace_id=str(payload.get("workspace_id") or ""),
                mode_pack_id=str(payload.get("mode_pack_id") or ""),
                skill_capability_checks=checks,
                text=str(payload.get("text") or ""),
                job_class=str(payload.get("job_class") or "") or None,
                argv=argv,
                consumer=str(payload.get("consumer") or "Hermes user"),
            )
        except WorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/hermes/workspace-turns/{work_packet_id}/complete")
    async def complete_hermes_workspace_turn(work_packet_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            return await complete_workspace_hermes_turn(
                settings,
                workspace_id=str(payload.get("workspace_id") or ""),
                work_packet_id=work_packet_id,
                provider=str(payload.get("provider") or "unknown"),
                model=str(payload.get("model") or "unknown"),
                route=str(payload.get("route") or "unknown"),
                tokens_in=int(payload.get("tokens_in") or 0),
                tokens_out=int(payload.get("tokens_out") or 0),
                estimated_cost_usd=float(payload["estimated_cost_usd"]) if payload.get("estimated_cost_usd") is not None else None,
                actual_cost_usd=float(payload["actual_cost_usd"]) if payload.get("actual_cost_usd") is not None else None,
                quota_source=str(payload.get("quota_source") or "unknown"),
                context_pressure=float(payload["context_pressure"]) if payload.get("context_pressure") is not None else None,
                quota_state=payload.get("quota_state") if isinstance(payload.get("quota_state"), dict) else None,
                measurement_source=str(payload.get("measurement_source") or "hermes_runtime_state"),
            )
        except (ValueError, WorkspacePolicyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/dashboard-status")
    async def operator_dashboard_status() -> dict[str, object]:
        route_api_ready = any(
            getattr(route, "path", None) == "/api/route" and "POST" in (getattr(route, "methods", set()) or set())
            for route in app.routes
        )
        services_rows = await store.list_services()
        subscription_rows = await store.list_subscription_usage_snapshots()
        subscription_checked = [str(r.get("checked_at")) for r in subscription_rows if r.get("checked_at")]
        data_sources: dict[str, object] = {
            "subscriptions": max(subscription_checked) if subscription_checked else None,
            "business_snapshot": _business_snapshot_generated_at(settings),
        }
        return build_dashboard_status(
            settings,
            write_receipt=False,
            in_process=True,
            route_api_ready=route_api_ready,
            services=services_rows,
            data_sources=data_sources,
        )

    @app.get("/api/workers")
    async def workers() -> dict[str, object]:
        return {"workers": await store.list_worker_cards()}

    @app.get("/api/receipts")
    async def receipts(limit: int = 50) -> dict[str, object]:
        return {"receipts": await store.list_receipts(limit=limit)}

    @app.get("/api/budget")
    async def budget() -> dict[str, object]:
        return {
            "state": "produced",
            "budget_model": "split",
            "subscriptions": await build_subscription_usage_payload(store, refresh=False),
            "api_budgets": await build_api_budget_payload(store),
            "legacy_probes": await store.list_budget_probes(),
        }

    @app.get("/api/subscriptions")
    async def subscriptions() -> dict[str, object]:
        return await build_subscription_usage_payload(store, refresh=False)

    @app.get("/api/api-budgets")
    async def api_budgets() -> dict[str, object]:
        return await build_api_budget_payload(store)

    @app.post("/api/budget/refresh")
    async def budget_refresh() -> dict[str, object]:
        probes = [probe_to_dict(probe) for probe in run_all_probes()]
        result = await store.upsert_budget_probes(probes)
        return {**result, "probes": probes}

    @app.post("/api/subscriptions/refresh")
    async def subscriptions_refresh() -> dict[str, object]:
        return await build_subscription_usage_payload(store, refresh=True)

    @app.get("/api/services")
    async def services() -> list[dict[str, object]]:
        return await store.list_services()

    @app.get("/api/capabilities")
    async def capabilities() -> list[dict[str, object]]:
        return await store.list_capabilities()

    @app.get("/api/connectors")
    async def connectors(workspace_id: str | None = None) -> dict[str, object]:
        if workspace_id not in {"personal", "example"}:
            raise HTTPException(status_code=409, detail="connector access requires a Personal or Example workspace selection")
        return workspace_connector_context(workspace_id, len(await store.list_services()))

    @app.get("/api/connectors/templates")
    async def connectors_templates(workspace_id: str | None = None) -> dict[str, object]:
        if workspace_id not in {"personal", "example"}:
            raise HTTPException(status_code=409, detail="connector access requires a Personal or Example workspace selection")
        return {"state": "produced", "templates": connector_templates()}

    @app.post("/api/connectors/validate")
    async def connectors_validate(payload: dict[str, object]) -> dict[str, object]:
        if payload.get("workspace_id") not in {"personal", "example"}:
            raise HTTPException(status_code=409, detail="connector access requires a Personal or Example workspace selection")
        return validate_connector_config(payload)

    @app.post("/api/connectors/config-preview")
    async def connectors_config_preview(payload: dict[str, object]) -> dict[str, object]:
        if payload.get("workspace_id") not in {"personal", "example"}:
            raise HTTPException(status_code=409, detail="connector access requires a Personal or Example workspace selection")
        validation = validate_connector_config(payload)
        if not validation.get("success"):
            raise HTTPException(status_code=400, detail=validation)
        return build_config_preview(payload)

    @app.get("/api/auth-quota")
    async def auth_quota() -> dict[str, object]:
        auth_state = probe_auth_and_quota()
        await store.record_quota_snapshots(quota_snapshots(auth_state))
        return auth_state

    @app.get("/api/quota-ledger")
    async def quota_ledger() -> list[dict[str, object]]:
        return await store.list_quota_ledger()

    @app.get("/api/quota-state")
    async def quota_state() -> dict[str, dict[str, object]]:
        return await store.latest_quota_state()

    @app.get("/api/repair-queue")
    async def repair_queue() -> list[dict[str, object]]:
        return await store.list_repair_queue()

    @app.get("/api/selections")
    async def selections() -> list[dict[str, object]]:
        return await store.list_selections()

    @app.get("/api/projects")
    async def projects(workspace_id: str) -> list[dict[str, object]]:
        if workspace_id in {"personal", "example"}:
            # Existing discoveries have no owner-assigned scope.  They remain
            # visible only through the isolated legacy lane until classified.
            return []
        if workspace_id != "unclassified_legacy":
            raise HTTPException(status_code=409, detail="project retrieval requires a work-bearing workspace")
        return await store.list_projects()

    @app.get("/api/activity")
    async def activity() -> list[dict[str, object]]:
        return await store.list_activity()

    @app.get("/api/dispatches")
    async def dispatches() -> list[dict[str, object]]:
        return await store.list_dispatches()

    @app.get("/api/dispatch-attempts")
    async def dispatch_attempts() -> list[dict[str, object]]:
        return await store.list_dispatch_attempts()

    @app.get("/api/audit-log")
    async def audit_log() -> list[dict[str, object]]:
        return await store.list_audit_log()

    @app.get("/api/standing-orders")
    async def standing_orders() -> list[dict[str, object]]:
        return await store.list_standing_orders()

    @app.get("/api/scheduler/tasks")
    async def scheduler_tasks() -> list[dict[str, object]]:
        return await store.list_scheduler_tasks()

    @app.get("/api/working-memory")
    async def working_memory(workspace_id: str) -> list[dict[str, object]]:
        if workspace_id in {"personal", "example"}:
            # No legacy memory is silently assigned to either workspace.
            return []
        if workspace_id != "unclassified_legacy":
            raise HTTPException(status_code=409, detail="memory retrieval requires a work-bearing workspace")
        return await store.list_working_memory()

    @app.get("/api/approvals")
    async def approvals() -> list[dict[str, object]]:
        return await store.list_pending_approvals()

    @app.get("/api/token-flow")
    async def token_flow(limit: int = 50) -> dict[str, object]:
        return await build_token_flow_payload(store, limit=limit)

    @app.get("/api/token-accounting")
    async def token_accounting(limit: int = 50) -> dict[str, object]:
        return await build_token_accounting_payload(store, limit=limit)

    @app.get("/api/quality-leaderboard")
    async def quality_leaderboard(limit: int = 2000) -> dict[str, object]:
        return await build_quality_leaderboard_payload(store, limit=limit)

    @app.get("/api/failover-events")
    async def failover_events(limit: int = 50) -> dict[str, object]:
        return await build_failover_events_payload(store, limit=limit)

    @app.get("/api/governance-receipts")
    async def governance_receipts(limit: int = 25) -> dict[str, object]:
        return await build_governance_receipts_payload(store, limit=limit)

    @app.get("/api/spec-status")
    async def spec_status() -> dict[str, object]:
        return await evaluate_spec_status(settings, store)

    @app.post("/api/selections")
    async def add_selection(payload: dict[str, object]) -> dict[str, object]:
        raw_payload = payload.get("payload")
        selection_payload = raw_payload if isinstance(raw_payload, dict) else {}
        selection = await store.add_selection(str(payload.get("kind") or "file"), selection_payload)
        return selection.model_dump(mode="json")

    @app.post("/api/dispatch")
    async def dispatch(req: IntentRequest) -> dict[str, object]:
        if req.workspace_id == "system":
            raise HTTPException(status_code=409, detail="System scope governs resources and cannot receive work dispatches")
        try:
            packet = await store.create_work_packet(
                workspace_id=req.workspace_id,
                intent=req.text,
                payload={"project_root": req.project_root} if req.project_root else {},
                mode_pack_id=None,
                consumer="legacy_dispatch_consumer",
                state="routing",
            )
        except WorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        project = Path(req.project_root) if req.project_root else None
        result = await dispatcher.dispatch_text(req.text, project)
        return {**result.model_dump(mode="json"), "workspace_id": req.workspace_id, "work_packet_id": packet["id"]}

    @app.post("/api/route")
    async def route(req: RouteRequest) -> dict[str, object]:
        if req.workspace_id == "system":
            raise HTTPException(status_code=409, detail="System scope governs resources and cannot receive route requests")
        try:
            packet = await store.create_work_packet(
                workspace_id=req.workspace_id,
                intent=req.text,
                payload={},
                mode_pack_id=None,
                consumer="legacy_route_consumer",
                state="routing",
            )
        except WorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {**(await route_brain(store, text=req.text, job_class=req.job_class)), "workspace_id": req.workspace_id, "work_packet_id": packet["id"]}

    @app.post("/api/prove-adapter")
    async def prove_adapter(req: AdapterProofRequest) -> dict[str, object]:
        result = await dispatcher.prove_adapter(
            req.adapter_name,
            req.prompt,
            capability_id=req.capability,
            allow_subscription=req.allow_subscription,
        )
        return result.model_dump(mode="json")

    @app.post("/api/approvals/{intent_id}/{action}")
    async def decide_approval(intent_id: str, action: str) -> dict[str, object]:
        if action not in {"approve", "reject"}:
            return {"state": "failed", "error": "action must be approve or reject"}
        result = await dispatcher.approve_intent(intent_id, action == "approve")
        return result.model_dump(mode="json")

    @app.post("/api/refresh")
    async def refresh() -> dict[str, int]:
        services, caps = await discover_services_and_capabilities()
        await store.upsert_services(services)
        await store.upsert_capabilities(caps)
        projects = discover_projects(project_roots, max_depth=3)
        await store.upsert_projects(projects)
        await store.record_discovery("refresh", {"services": len(services), "capabilities": len(caps), "projects": len(projects)}, "Manual API refresh completed.")
        return {"services": len(services), "capabilities": len(caps), "projects": len(projects)}

    @app.post("/api/refresh-services")
    async def refresh_services() -> dict[str, int]:
        services, caps = await discover_services_and_capabilities()
        await store.upsert_services(services)
        await store.upsert_capabilities(caps)
        await store.record_discovery("refresh_services", {"services": len(services), "capabilities": len(caps)}, "Fast service health re-probe (no project scan).")
        return {"services": len(services), "capabilities": len(caps)}

    @app.post("/api/repair-services")
    async def repair_services() -> dict[str, object]:
        return await repair_core_services(store)

    @app.post("/api/retry-repairs")
    async def retry_repairs() -> dict[str, object]:
        return await retry_open_repairs(settings, store)

    @app.post("/api/notify-once")
    async def notify_once(payload: dict[str, object] | None = None) -> dict[str, object]:
        payload = payload or {}
        result = await run_all_subscribers_once(settings, include_tray=bool(payload.get("include_tray")))
        for subscriber in result.get("subscribers", []):
            if not isinstance(subscriber, dict):
                continue
            if int(subscriber.get("delivered") or 0) > 0:
                await store.resolve_repair_items(
                    f"notification_subscriber:{subscriber.get('subscriber')}",
                    {"delivered": int(subscriber.get("delivered") or 0)},
                )
            if int(subscriber.get("failures") or 0) > 0:
                await store.add_repair_item(
                    f"notification_subscriber:{subscriber.get('subscriber')}",
                    f"{subscriber.get('failures')} notification delivery failure(s)",
                    "Inspect subscriber receipt JSONL and repair the local notification provider.",
                )
        await store.audit("subscriber", "notify_once", "notifications", result)
        return result

    @app.post("/api/benchmark-latency")
    async def benchmark_latency() -> dict[str, object]:
        return await run_selection_latency_benchmark(settings)

    @app.post("/api/autopilot-scan")
    async def autopilot_scan(payload: dict[str, object] | None = None) -> dict[str, object]:
        payload = payload or {}
        roots = payload.get("roots")
        root_paths = [Path(str(r)) for r in roots] if isinstance(roots, list) else []
        return await scan_autopilot_roots_once(settings, root_paths)

    @app.post("/api/scheduler/run-once")
    async def scheduler_run_once() -> dict[str, object]:
        return await run_scheduler_once(settings, store)

    @app.post("/api/scheduler/migrate-legacy")
    async def scheduler_migrate_legacy() -> dict[str, object]:
        return await migrate_legacy_scheduled_tasks(settings, store)

    @app.post("/api/scheduler/tasks")
    async def add_scheduler_task(payload: dict[str, object]) -> dict[str, object]:
        workspace_id = str(payload.get("workspace_id") or "")
        if workspace_id not in {"personal", "example", "unclassified_legacy"}:
            raise HTTPException(status_code=409, detail="scheduled execution requires an explicit workspace_id")
        folder = str(payload.get("folder_path") or "")
        policy = str(payload.get("policy_yaml") or "autopilot: enabled")
        enabled = bool(payload.get("enabled"))
        raw_interval = payload.get("interval_seconds")
        interval_seconds = int(raw_interval) if isinstance(raw_interval, int | float | str) and str(raw_interval).strip() else 0
        task_id = str(payload.get("id") or "") or None
        name = str(payload.get("name") or "") or None
        order_id = await store.upsert_standing_order(folder, policy, enabled)
        scheduler_task_id = await store.upsert_scheduler_task(
            name=name or f"Standing order: {Path(folder).name or order_id}",
            task_type="standing_order_scan",
            target_ref=order_id,
            payload={"folder_path": folder, "workspace_id": workspace_id},
            schedule_kind="interval" if interval_seconds > 0 else "manual",
            interval_seconds=interval_seconds,
            enabled=enabled,
            task_id=task_id,
            workspace_id=workspace_id,
        )
        return {"id": scheduler_task_id, "workspace_id": workspace_id, "standing_order_id": order_id, "folder_path": folder, "enabled": enabled, "interval_seconds": interval_seconds}

    @app.post("/api/scheduler/tasks/{task_id}")
    async def scheduler_task_action(task_id: str, payload: dict[str, object]) -> dict[str, object]:
        action = str(payload.get("action") or "")
        if action == "pause":
            await store.set_scheduler_task_enabled(task_id, False)
            return {"id": task_id, "enabled": False}
        if action == "resume":
            try:
                await store.set_scheduler_task_enabled(task_id, True)
                return {"id": task_id, "enabled": True}
            except PermissionError as exc:
                return {"id": task_id, "state": "denied", "error": str(exc)}
        if action == "activate":
            return await store.activate_scheduler_task(task_id, reviewer="owner", note=str(payload.get("note") or "Owner approved activation."))
        if action == "reject":
            return await store.review_scheduler_task(task_id, "rejected", reviewer="owner", note=str(payload.get("note") or "Owner rejected activation."))
        if action == "trigger":
            return await trigger_scheduler_task(settings, task_id, store)
        return {"id": task_id, "state": "failed", "error": "action must be pause, resume, activate, reject, or trigger"}

    @app.post("/api/standing-orders")
    async def add_standing_order(payload: dict[str, object]) -> dict[str, object]:
        folder = str(payload.get("folder_path") or "")
        policy = str(payload.get("policy_yaml") or "autopilot: disabled")
        enabled = bool(payload.get("enabled"))
        order_id = await store.upsert_standing_order(folder, policy, enabled)
        return {"id": order_id, "folder_path": folder, "enabled": enabled}

    return app
