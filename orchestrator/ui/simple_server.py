from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from orchestrator.config import Settings
from orchestrator.connectors import build_config_preview, build_connectors_payload, connector_templates, validate_connector_config
from orchestrator.autopilot.watchers import scan_autopilot_roots_once
from orchestrator.benchmarks.latency import run_selection_latency_benchmark
from orchestrator.discovery.auth import probe_auth_and_quota, quota_snapshots
from orchestrator.discovery.budget_probes import probe_to_dict, run_all_probes
from orchestrator.discovery.projects import discover_projects
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.dispatch.dispatcher import Dispatcher
from orchestrator.notifications.spine import NotificationSpine
from orchestrator.notifications.subscribers.runner import run_all_subscribers_once
from orchestrator.process.recovery import repair_core_services
from orchestrator.process.repair_retry import retry_open_repairs
from orchestrator.scheduler.migration import migrate_legacy_scheduled_tasks
from orchestrator.scheduler.cron import start_due_scheduler_thread
from orchestrator.scheduler.tasks import run_scheduler_once, trigger_scheduler_task
from orchestrator.spec_status import evaluate_spec_status
from orchestrator.state.store import StateStore
from orchestrator.discovery.subscription_usage import build_api_budget_payload, build_subscription_usage_payload
from orchestrator.ui.dashboard import build_failover_events_payload, build_governance_receipts_payload, build_quality_leaderboard_payload
from orchestrator.usage.accounting import build_token_accounting_payload
from orchestrator.usage.flow import build_token_flow_payload


class OrchestratorRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = StateStore(settings)
        self.notifications = NotificationSpine(settings.notifications_path, self.store)
        self.dispatcher = Dispatcher(settings, self.store, self.notifications)

    async def initialize(self) -> None:
        await self.store.initialize()
        await self.store.recover_interrupted_dispatches()
        services, capabilities = await discover_services_and_capabilities()
        await self.store.upsert_services(services)
        await self.store.upsert_capabilities(capabilities)
        projects = discover_projects([Path.home(), Path.home() / "Documents", Path("D:/SharedRoot")], max_depth=3)
        await self.store.upsert_projects(projects)
        auth_state = probe_auth_and_quota()
        await self.store.record_quota_snapshots(quota_snapshots(auth_state))

    async def initialize_fast(self) -> None:
        await self.store.initialize()
        await self.store.recover_interrupted_dispatches()

    async def refresh(self) -> dict[str, int]:
        services, capabilities = await discover_services_and_capabilities()
        await self.store.upsert_services(services)
        await self.store.upsert_capabilities(capabilities)
        projects = discover_projects([Path.home(), Path.home() / "Documents", Path("D:/SharedRoot")], max_depth=3)
        await self.store.upsert_projects(projects)
        auth_state = probe_auth_and_quota()
        await self.store.record_quota_snapshots(quota_snapshots(auth_state))
        await self.store.record_discovery("refresh", {"services": len(services), "capabilities": len(capabilities), "projects": len(projects)}, "Manual fallback server refresh completed.")
        return {"services": len(services), "capabilities": len(capabilities), "projects": len(projects)}


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, default=str).encode("utf-8")


def make_handler(runtime: OrchestratorRuntime):
    static_dir = Path(__file__).with_suffix("").parent / "static"

    class Handler(BaseHTTPRequestHandler):
        server_version = "AIOrchestrator/0.1"

        def log_message(self, format: str, *args: object) -> None:
            return None

        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            return payload if isinstance(payload, dict) else {}

        def do_GET(self) -> None:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            query = parse_qs(parsed_url.query)
            if path == "/":
                self._send(200, (static_dir / "index.html").read_bytes(), "text/html; charset=utf-8")
                return
            pages = {
                "/workers": "workers.html",
                "/budget": "budget.html",
                "/connectors": "connectors.html",
                "/receipts": "receipts.html",
            }
            if path in pages:
                self._send(200, (static_dir / pages[path]).read_bytes(), "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                target = (static_dir / path.removeprefix("/static/")).resolve()
                if static_dir.resolve() not in target.parents and target != static_dir.resolve():
                    self._send(403, b"forbidden", "text/plain")
                    return
                if not target.exists():
                    self._send(404, b"not found", "text/plain")
                    return
                content_type = "text/css" if target.suffix == ".css" else "application/javascript" if target.suffix == ".js" else "text/plain"
                self._send(200, target.read_bytes(), content_type)
                return
            if path == "/api/services":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_services())))
                return
            if path == "/api/status":
                auth_state = probe_auth_and_quota()
                asyncio.run(runtime.store.record_quota_snapshots(quota_snapshots(auth_state)))
                self._send(
                    200,
                    _json_bytes(
                        {
                            "dispatches": asyncio.run(runtime.store.list_dispatches(limit=100)),
                            "auth_quota": auth_state,
                            "state_path": str(runtime.settings.state_path),
                            "notifications_path": str(runtime.settings.notifications_path),
                        }
                    ),
                )
                return
            if path == "/api/workers":
                self._send(200, _json_bytes({"workers": asyncio.run(runtime.store.list_worker_cards())}))
                return
            if path == "/api/receipts":
                raw_limit = (query.get("limit") or ["50"])[0]
                try:
                    limit = max(1, min(int(raw_limit), 1000))
                except ValueError:
                    limit = 50
                self._send(200, _json_bytes({"receipts": asyncio.run(runtime.store.list_receipts(limit=limit))}))
                return
            if path == "/api/budget":
                self._send(
                    200,
                    _json_bytes(
                        {
                            "state": "produced",
                            "budget_model": "split",
                            "subscriptions": asyncio.run(build_subscription_usage_payload(runtime.store, refresh=False)),
                            "api_budgets": asyncio.run(build_api_budget_payload(runtime.store)),
                            "legacy_probes": asyncio.run(runtime.store.list_budget_probes()),
                        }
                    ),
                )
                return
            if path == "/api/subscriptions":
                self._send(200, _json_bytes(asyncio.run(build_subscription_usage_payload(runtime.store, refresh=False))))
                return
            if path == "/api/api-budgets":
                self._send(200, _json_bytes(asyncio.run(build_api_budget_payload(runtime.store))))
                return
            if path == "/api/capabilities":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_capabilities())))
                return
            if path == "/api/connectors":
                self._send(
                    200,
                    _json_bytes(
                        build_connectors_payload(
                            runtime.settings.repo_root,
                            asyncio.run(runtime.store.list_services()),
                            asyncio.run(runtime.store.list_capabilities()),
                        )
                    ),
                )
                return
            if path == "/api/connectors/templates":
                self._send(200, _json_bytes({"state": "produced", "templates": connector_templates()}))
                return
            if path == "/api/auth-quota":
                auth_state = probe_auth_and_quota()
                asyncio.run(runtime.store.record_quota_snapshots(quota_snapshots(auth_state)))
                self._send(200, _json_bytes(auth_state))
                return
            if path == "/api/quota-ledger":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_quota_ledger())))
                return
            if path == "/api/quota-state":
                self._send(200, _json_bytes(asyncio.run(runtime.store.latest_quota_state())))
                return
            if path == "/api/repair-queue":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_repair_queue())))
                return
            if path == "/api/selections":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_selections())))
                return
            if path == "/api/projects":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_projects())))
                return
            if path == "/api/activity":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_activity())))
                return
            if path == "/api/dispatches":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_dispatches())))
                return
            if path == "/api/dispatch-attempts":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_dispatch_attempts())))
                return
            if path == "/api/audit-log":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_audit_log())))
                return
            if path == "/api/spec-status":
                self._send(200, _json_bytes(asyncio.run(evaluate_spec_status(runtime.settings, runtime.store))))
                return
            if path == "/api/standing-orders":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_standing_orders())))
                return
            if path == "/api/scheduler/tasks":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_scheduler_tasks())))
                return
            if path == "/api/working-memory":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_working_memory())))
                return
            if path == "/api/approvals":
                self._send(200, _json_bytes(asyncio.run(runtime.store.list_pending_approvals())))
                return
            if path == "/api/token-flow":
                raw_limit = (query.get("limit") or ["50"])[0]
                try:
                    limit = max(1, min(int(raw_limit), 1000))
                except ValueError:
                    limit = 50
                self._send(200, _json_bytes(asyncio.run(build_token_flow_payload(runtime.store, limit=limit))))
                return
            if path == "/api/token-accounting":
                raw_limit = (query.get("limit") or ["50"])[0]
                try:
                    limit = max(1, min(int(raw_limit), 1000))
                except ValueError:
                    limit = 50
                self._send(200, _json_bytes(asyncio.run(build_token_accounting_payload(runtime.store, limit=limit))))
                return
            if path == "/api/quality-leaderboard":
                self._send(200, _json_bytes(asyncio.run(build_quality_leaderboard_payload(runtime.store))))
                return
            if path == "/api/failover-events":
                self._send(200, _json_bytes(asyncio.run(build_failover_events_payload(runtime.store))))
                return
            if path == "/api/governance-receipts":
                self._send(200, _json_bytes(asyncio.run(build_governance_receipts_payload(runtime.store))))
                return
            self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            payload = self._read_json()
            if path == "/api/refresh":
                self._send(200, _json_bytes(asyncio.run(runtime.refresh())))
                return
            if path == "/api/budget/refresh":
                probes = [probe_to_dict(probe) for probe in run_all_probes()]
                result = asyncio.run(runtime.store.upsert_budget_probes(probes))
                self._send(200, _json_bytes({**result, "probes": probes}))
                return
            if path == "/api/subscriptions/refresh":
                self._send(200, _json_bytes(asyncio.run(build_subscription_usage_payload(runtime.store, refresh=True))))
                return
            if path == "/api/connectors/validate":
                self._send(200, _json_bytes(validate_connector_config(payload)))
                return
            if path == "/api/connectors/config-preview":
                validation = validate_connector_config(payload)
                if not validation.get("success"):
                    self._send(400, _json_bytes({"detail": validation}))
                    return
                self._send(200, _json_bytes(build_config_preview(payload)))
                return
            if path == "/api/repair-services":
                self._send(200, _json_bytes(asyncio.run(repair_core_services(runtime.store))))
                return
            if path == "/api/retry-repairs":
                self._send(200, _json_bytes(asyncio.run(retry_open_repairs(runtime.settings, runtime.store))))
                return
            if path == "/api/notify-once":
                include_tray = bool(payload.get("include_tray"))
                subscriber_result = asyncio.run(run_all_subscribers_once(runtime.settings, include_tray=include_tray))
                for subscriber in subscriber_result.get("subscribers", []):
                    if not isinstance(subscriber, dict):
                        continue
                    if int(subscriber.get("delivered") or 0) > 0:
                        asyncio.run(
                            runtime.store.resolve_repair_items(
                                f"notification_subscriber:{subscriber.get('subscriber')}",
                                {"delivered": int(subscriber.get("delivered") or 0)},
                            )
                        )
                    if int(subscriber.get("failures") or 0) > 0:
                        asyncio.run(
                            runtime.store.add_repair_item(
                                f"notification_subscriber:{subscriber.get('subscriber')}",
                                f"{subscriber.get('failures')} notification delivery failure(s)",
                                "Inspect subscriber receipt JSONL and repair the local notification provider.",
                            )
                        )
                asyncio.run(runtime.store.audit("subscriber", "notify_once", "notifications", subscriber_result))
                self._send(200, _json_bytes(subscriber_result))
                return
            if path == "/api/benchmark-latency":
                self._send(200, _json_bytes(asyncio.run(run_selection_latency_benchmark(runtime.settings))))
                return
            if path == "/api/autopilot-scan":
                roots = payload.get("roots")
                root_paths = [Path(str(r)) for r in roots] if isinstance(roots, list) else []
                self._send(200, _json_bytes(asyncio.run(scan_autopilot_roots_once(runtime.settings, root_paths))))
                return
            if path == "/api/scheduler/run-once":
                self._send(200, _json_bytes(asyncio.run(run_scheduler_once(runtime.settings, runtime.store))))
                return
            if path == "/api/scheduler/migrate-legacy":
                self._send(200, _json_bytes(asyncio.run(migrate_legacy_scheduled_tasks(runtime.settings, runtime.store))))
                return
            if path == "/api/scheduler/tasks":
                folder = str(payload.get("folder_path") or "")
                policy = str(payload.get("policy_yaml") or "autopilot: enabled")
                enabled = bool(payload.get("enabled"))
                interval_seconds = int(payload.get("interval_seconds") or 0)
                task_id = str(payload.get("id") or "") or None
                name = str(payload.get("name") or "") or None
                order_id = asyncio.run(runtime.store.upsert_standing_order(folder, policy, enabled))
                scheduler_task_id = asyncio.run(
                    runtime.store.upsert_scheduler_task(
                        name=name or f"Standing order: {Path(folder).name or order_id}",
                        task_type="standing_order_scan",
                        target_ref=order_id,
                        payload={"folder_path": folder},
                        schedule_kind="interval" if interval_seconds > 0 else "manual",
                        interval_seconds=interval_seconds,
                        enabled=enabled,
                        task_id=task_id,
                    )
                )
                self._send(200, _json_bytes({"id": scheduler_task_id, "standing_order_id": order_id, "folder_path": folder, "enabled": enabled, "interval_seconds": interval_seconds}))
                return
            if path.startswith("/api/scheduler/tasks/"):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[0] == "api" and parts[1] == "scheduler" and parts[2] == "tasks":
                    task_id = parts[3]
                    action = str(payload.get("action") or "")
                    if action == "pause":
                        asyncio.run(runtime.store.set_scheduler_task_enabled(task_id, False))
                        self._send(200, _json_bytes({"id": task_id, "enabled": False}))
                        return
                    if action == "resume":
                        try:
                            asyncio.run(runtime.store.set_scheduler_task_enabled(task_id, True))
                            self._send(200, _json_bytes({"id": task_id, "enabled": True}))
                        except PermissionError as exc:
                            self._send(409, _json_bytes({"id": task_id, "state": "denied", "error": str(exc)}))
                        return
                    if action == "activate":
                        note = str(payload.get("note") or "Owner approved activation.")
                        self._send(200, _json_bytes(asyncio.run(runtime.store.activate_scheduler_task(task_id, reviewer="owner", note=note))))
                        return
                    if action == "reject":
                        note = str(payload.get("note") or "Owner rejected activation.")
                        self._send(200, _json_bytes(asyncio.run(runtime.store.review_scheduler_task(task_id, "rejected", reviewer="owner", note=note))))
                        return
                    if action == "trigger":
                        self._send(200, _json_bytes(asyncio.run(trigger_scheduler_task(runtime.settings, task_id, runtime.store))))
                        return
            if path == "/api/standing-orders":
                folder = str(payload.get("folder_path") or "")
                policy = str(payload.get("policy_yaml") or "autopilot: disabled")
                enabled = bool(payload.get("enabled"))
                order_id = asyncio.run(runtime.store.upsert_standing_order(folder, policy, enabled))
                self._send(200, _json_bytes({"id": order_id, "folder_path": folder, "enabled": enabled}))
                return
            if path == "/api/selections":
                raw_payload = payload.get("payload")
                selection_payload = raw_payload if isinstance(raw_payload, dict) else {}
                selection = asyncio.run(runtime.store.add_selection(str(payload.get("kind") or "file"), selection_payload))
                self._send(200, _json_bytes(selection.model_dump(mode="json")))
                return
            if path == "/api/dispatch":
                result = asyncio.run(runtime.dispatcher.dispatch_text(str(payload.get("text") or "")))
                self._send(200, _json_bytes(result.model_dump(mode="json")))
                return
            if path == "/api/prove-adapter":
                result = asyncio.run(
                    runtime.dispatcher.prove_adapter(
                        str(payload.get("adapter_name") or ""),
                        str(payload.get("prompt") or "Adapter proof: answer with OK."),
                        capability_id=str(payload.get("capability") or "") or None,
                        allow_subscription=bool(payload.get("allow_subscription")),
                    )
                )
                self._send(200, _json_bytes(result.model_dump(mode="json")))
                return
            if path.startswith("/api/approvals/"):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[0] == "api" and parts[1] == "approvals":
                    intent_id = parts[2]
                    action = parts[3]
                    if action in {"approve", "reject"}:
                        result = asyncio.run(runtime.dispatcher.approve_intent(intent_id, action == "approve"))
                        self._send(200, _json_bytes(result.model_dump(mode="json")))
                        return
            self._send(404, b"not found", "text/plain")

    return Handler


def run_simple_server(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> None:
    runtime = OrchestratorRuntime(settings)
    asyncio.run(runtime.initialize_fast())
    start_due_scheduler_thread(settings)
    server = ThreadingHTTPServer((host, port), make_handler(runtime))
    server.serve_forever()
