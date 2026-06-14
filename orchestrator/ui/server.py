from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator.autopilot.watchers import scan_autopilot_roots_once
from orchestrator.benchmarks.latency import run_selection_latency_benchmark
from orchestrator.config import Settings
from orchestrator.discovery.auth import probe_auth_and_quota, quota_snapshots
from orchestrator.discovery.budget_probes import probe_to_dict, run_all_probes
from orchestrator.discovery.projects import discover_projects
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
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
from orchestrator.usage.flow import build_token_flow_payload


class IntentRequest(BaseModel):
    text: str
    project_root: str | None = None


class RouteRequest(BaseModel):
    text: str
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

    app = FastAPI(title="AI Orchestrator", version="0.1.0", lifespan=lifespan)
    static_dir = Path(__file__).with_suffix("").parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/workers", response_class=HTMLResponse)
    async def workers_page() -> str:
        return (static_dir / "workers.html").read_text(encoding="utf-8")

    @app.get("/budget", response_class=HTMLResponse)
    async def budget_page() -> str:
        return (static_dir / "budget.html").read_text(encoding="utf-8")

    @app.get("/receipts", response_class=HTMLResponse)
    async def receipts_page() -> str:
        return (static_dir / "receipts.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    async def dashboard_status() -> dict[str, object]:
        auth_state = probe_auth_and_quota()
        await store.record_quota_snapshots(quota_snapshots(auth_state))
        return {
            "dispatches": await store.list_dispatches(limit=100),
            "auth_quota": auth_state,
            "state_path": str(settings.state_path),
            "notifications_path": str(settings.notifications_path),
        }

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
    async def projects() -> list[dict[str, object]]:
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
    async def working_memory() -> list[dict[str, object]]:
        return await store.list_working_memory()

    @app.get("/api/approvals")
    async def approvals() -> list[dict[str, object]]:
        return await store.list_pending_approvals()

    @app.get("/api/token-flow")
    async def token_flow(limit: int = 50) -> dict[str, object]:
        return await build_token_flow_payload(store, limit=limit)

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
        project = Path(req.project_root) if req.project_root else None
        result = await dispatcher.dispatch_text(req.text, project)
        return result.model_dump(mode="json")

    @app.post("/api/route")
    async def route(req: RouteRequest) -> dict[str, object]:
        return await route_brain(store, text=req.text, job_class=req.job_class)

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
            payload={"folder_path": folder},
            schedule_kind="interval" if interval_seconds > 0 else "manual",
            interval_seconds=interval_seconds,
            enabled=enabled,
            task_id=task_id,
        )
        return {"id": scheduler_task_id, "standing_order_id": order_id, "folder_path": folder, "enabled": enabled, "interval_seconds": interval_seconds}

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
