from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from orchestrator.config import Settings, ensure_runtime_dirs
from orchestrator.models import Capability, Intent, Notification, RoutingDecision, Selection, ServiceInfo


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


class StateStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.state_path

    async def initialize(self) -> None:
        ensure_runtime_dirs(self.settings)
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(schema)
            await self._ensure_scheduler_task_columns(db)
            await db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, iso()),
            )
            await db.commit()

    async def _ensure_scheduler_task_columns(self, db: aiosqlite.Connection) -> None:
        rows = await (await db.execute("PRAGMA table_info(scheduler_tasks)")).fetchall()
        columns = {str(row[1]) for row in rows}
        additions = {
            "review_state": "ALTER TABLE scheduler_tasks ADD COLUMN review_state TEXT DEFAULT 'not_required'",
            "reviewed_at": "ALTER TABLE scheduler_tasks ADD COLUMN reviewed_at TEXT",
            "reviewed_by": "ALTER TABLE scheduler_tasks ADD COLUMN reviewed_by TEXT",
            "review_note": "ALTER TABLE scheduler_tasks ADD COLUMN review_note TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                await db.execute(statement)

    async def _audit_in_db(self, db: aiosqlite.Connection, actor: str, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
        await db.execute(
            "INSERT INTO audit_log(id, ts, actor, action, target, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (f"aud_{uuid.uuid4().hex[:16]}", iso(), actor, action, target, json.dumps(detail or {})),
        )

    async def upsert_services(self, services: list[ServiceInfo]) -> None:
        async with aiosqlite.connect(self.path) as db:
            for s in services:
                now = iso()
                await db.execute(
                    """
                    INSERT INTO services(id, name, service_group, adapter_name, protocol, install_path, version, health_state, last_probe_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      name=excluded.name, service_group=excluded.service_group, adapter_name=excluded.adapter_name,
                      protocol=excluded.protocol, install_path=excluded.install_path, version=excluded.version,
                      health_state=excluded.health_state, last_probe_at=excluded.last_probe_at, updated_at=excluded.updated_at
                    """,
                    (
                        s.id,
                        s.name,
                        s.service_group,
                        s.adapter_name,
                        s.protocol,
                        s.install_path,
                        s.version,
                        s.health_state.value,
                        iso(s.last_probe_at),
                        now,
                        now,
                    ),
                )
            await db.commit()

    async def upsert_capabilities(self, capabilities: list[Capability]) -> None:
        async with aiosqlite.connect(self.path) as db:
            capability_ids = {c.id for c in capabilities}
            adapter_names = {c.adapter_name for c in capabilities}
            for c in capabilities:
                await db.execute(
                    """
                    INSERT INTO capabilities(id, adapter_name, capability_id, rating_instruction, rating_quality, latency_band, consequence_max, billing_class, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      rating_instruction=excluded.rating_instruction, rating_quality=excluded.rating_quality,
                      latency_band=excluded.latency_band, consequence_max=excluded.consequence_max,
                      billing_class=excluded.billing_class, enabled=excluded.enabled
                    """,
                    (
                        c.id,
                        c.adapter_name,
                        c.capability_id,
                        c.rating_instruction,
                        c.rating_quality,
                        c.latency_band,
                        c.consequence_max.value,
                        c.billing_class.value,
                        int(c.enabled),
                    ),
                )
            if capability_ids and adapter_names:
                id_marks = ",".join("?" for _ in capability_ids)
                adapter_marks = ",".join("?" for _ in adapter_names)
                await db.execute(
                    f"DELETE FROM capabilities WHERE adapter_name IN ({adapter_marks}) AND id NOT IN ({id_marks})",
                    (*adapter_names, *capability_ids),
                )
            await db.commit()

    async def list_services(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM services ORDER BY service_group, name")).fetchall()
            return [dict(r) for r in rows]

    async def list_capabilities(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM capabilities WHERE enabled=1 ORDER BY adapter_name, capability_id")).fetchall()
            return [dict(r) for r in rows]

    async def upsert_projects(self, projects: list[dict[str, Any]]) -> None:
        async with aiosqlite.connect(self.path) as db:
            for project in projects:
                now = iso()
                await db.execute(
                    """
                    INSERT INTO projects(id, name, root_path, domain, consequence_tier, policy_file_path, discovered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(root_path) DO UPDATE SET
                      name=excluded.name, domain=excluded.domain, consequence_tier=excluded.consequence_tier,
                      policy_file_path=excluded.policy_file_path, updated_at=excluded.updated_at
                    """,
                    (
                        project["id"],
                        project["name"],
                        project["root_path"],
                        project.get("domain", "general"),
                        project.get("consequence_tier", "low"),
                        project.get("policy_file_path"),
                        now,
                        now,
                    ),
                )
                await db.execute(
                    """
                    INSERT OR IGNORE INTO working_memory(project_id, last_activity_at, in_flight_intents, recent_outputs, pending_approvals, open_threads)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (project["id"], now, "[]", "[]", "[]", "[]"),
                )
            await db.commit()

    async def list_projects(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM projects ORDER BY updated_at DESC, name")).fetchall()
            return [dict(r) for r in rows]

    async def find_project_id_for_path(self, target_path: Path) -> str | None:
        target = target_path.expanduser()
        try:
            target_resolved = target.resolve()
        except OSError:
            target_resolved = target.absolute()
        target_text = str(target_resolved).lower()
        projects = await self.list_projects()
        matches: list[tuple[int, str]] = []
        for project in projects:
            root = Path(str(project.get("root_path") or "")).expanduser()
            try:
                root_resolved = root.resolve()
            except OSError:
                root_resolved = root.absolute()
            root_text = str(root_resolved).lower()
            if target_text == root_text or target_text.startswith(root_text.rstrip("\\/") + "\\") or target_text.startswith(root_text.rstrip("\\/") + "/"):
                matches.append((len(root_text), str(project["id"])))
        if not matches:
            return None
        matches.sort(reverse=True)
        return matches[0][1]

    async def refresh_working_memory(self, project_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))).fetchone()
            if row is None:
                return None
            memory = await self._refresh_working_memory_in_db(db, project_id)
            await db.commit()
            return memory

    async def _refresh_working_memory_in_db(self, db: aiosqlite.Connection, project_id: str) -> dict[str, Any]:
        db.row_factory = aiosqlite.Row
        in_flight_rows = await (
            await db.execute(
                """
                SELECT id, raw_text, state, created_at
                FROM intents
                WHERE project_id = ? AND state NOT IN ('completed', 'failed', 'rejected', 'interrupted', 'cancelled')
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (project_id,),
            )
        ).fetchall()
        output_rows = await (
            await db.execute(
                """
                SELECT d.id AS dispatch_id, d.intent_id, d.adapter_name, d.completed_at, d.output_path
                FROM dispatches d
                JOIN intents i ON i.id = d.intent_id
                WHERE i.project_id = ? AND d.state = 'completed'
                ORDER BY d.completed_at DESC
                LIMIT 20
                """,
                (project_id,),
            )
        ).fetchall()
        approval_rows = await (
            await db.execute(
                """
                SELECT n.id, n.intent_id, n.title, n.created_at
                FROM notifications n
                LEFT JOIN intents i ON i.id = n.intent_id
                WHERE n.severity = 'approval_request'
                  AND n.acknowledged = 0
                  AND (n.project_id = ? OR i.project_id = ?)
                ORDER BY n.created_at DESC
                LIMIT 20
                """,
                (project_id, project_id),
            )
        ).fetchall()
        memory = {
            "project_id": project_id,
            "last_activity_at": iso(),
            "in_flight_intents": [dict(row) for row in in_flight_rows],
            "recent_outputs": [dict(row) for row in output_rows],
            "pending_approvals": [dict(row) for row in approval_rows],
            "open_threads": [],
        }
        await db.execute(
            """
            INSERT INTO working_memory(project_id, last_activity_at, in_flight_intents, recent_outputs, pending_approvals, open_threads)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
              last_activity_at=excluded.last_activity_at,
              in_flight_intents=excluded.in_flight_intents,
              recent_outputs=excluded.recent_outputs,
              pending_approvals=excluded.pending_approvals,
              open_threads=excluded.open_threads
            """,
            (
                project_id,
                memory["last_activity_at"],
                json.dumps(memory["in_flight_intents"]),
                json.dumps(memory["recent_outputs"]),
                json.dumps(memory["pending_approvals"]),
                json.dumps(memory["open_threads"]),
            ),
        )
        await self._audit_in_db(db, "memory", "working_memory_refreshed", project_id, memory)
        return memory

    async def _refresh_intent_project_memory_in_db(self, db: aiosqlite.Connection, intent_id: str) -> None:
        row = await (await db.execute("SELECT project_id FROM intents WHERE id = ?", (intent_id,))).fetchone()
        if row and row[0]:
            await self._refresh_working_memory_in_db(db, str(row[0]))

    async def list_working_memory(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT wm.*, p.name AS project_name, p.root_path
                    FROM working_memory wm
                    LEFT JOIN projects p ON p.id = wm.project_id
                    ORDER BY wm.last_activity_at DESC
                    """
                )
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                for field in ["in_flight_intents", "recent_outputs", "pending_approvals", "open_threads"]:
                    try:
                        item[field] = json.loads(str(item.get(field) or "[]"))
                    except json.JSONDecodeError:
                        item[field] = []
                result.append(item)
            return result

    async def add_selection(self, kind: str, payload: dict[str, Any], grouped_with: str | None = None) -> Selection:
        selection = Selection(id=f"sel_{uuid.uuid4().hex[:12]}", kind=kind, payload=payload, grouped_with=grouped_with)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO selections(id, added_at, kind, payload, grouped_with) VALUES (?, ?, ?, ?, ?)",
                (selection.id, iso(selection.added_at), selection.kind, json.dumps(selection.payload), selection.grouped_with),
            )
            await self._audit_in_db(db, "user", "selection_added", selection.id, {"kind": kind, "payload": payload})
            await db.commit()
        return selection

    async def list_selections(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM selections ORDER BY added_at DESC")).fetchall()
            selections: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                try:
                    item["payload"] = json.loads(str(item.get("payload") or "{}"))
                except json.JSONDecodeError:
                    item["payload"] = {}
                selections.append(item)
            return selections

    async def create_intent(self, intent: Intent) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO intents(id, parent_intent_id, source, raw_text, parsed_payload, project_id, selections, consequence_tier, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.id,
                    None,
                    intent.source,
                    intent.raw_text,
                    json.dumps(intent.parsed_payload),
                    intent.project_id,
                    json.dumps([s.model_dump(mode="json") for s in intent.selections]),
                    intent.consequence_tier.value,
                    intent.state,
                    iso(intent.created_at),
                ),
            )
            await self._audit_in_db(db, str(intent.source), "intent_created", intent.id, intent.model_dump(mode="json"))
            if intent.project_id:
                await self._refresh_working_memory_in_db(db, intent.project_id)
            await db.commit()

    async def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM intents WHERE id = ?", (intent_id,))).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["parsed_payload"] = json.loads(str(item.get("parsed_payload") or "{}"))
            item["selections"] = json.loads(str(item.get("selections") or "[]"))
            return item

    async def update_intent_state(self, intent_id: str, state: str, completed: bool = False) -> None:
        completed_at = iso() if completed else None
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE intents SET state = ?, completed_at = COALESCE(?, completed_at) WHERE id = ?",
                (state, completed_at, intent_id),
            )
            await self._audit_in_db(db, "orchestrator", "intent_state", intent_id, {"state": state, "completed": completed})
            await self._refresh_intent_project_memory_in_db(db, intent_id)
            await db.commit()

    async def record_routing(self, decision: RoutingDecision) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO routing_decisions(intent_id, decided_at, chosen_adapter, candidates_considered, candidates_rejected, reasoning) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision.intent_id,
                    iso(decision.decided_at),
                    decision.chosen_adapter,
                    json.dumps(decision.candidates_considered),
                    json.dumps(decision.candidates_rejected),
                    decision.reasoning,
                ),
            )
            await self._audit_in_db(db, "router", "routing_decision", decision.intent_id, decision.model_dump(mode="json"))
            await db.commit()

    async def record_dispatch(self, dispatch: dict[str, Any], receipt: dict[str, Any] | None = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO dispatches(id, intent_id, adapter_name, envelope, state, started_at, completed_at, output_path, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  intent_id=excluded.intent_id,
                  adapter_name=excluded.adapter_name,
                  envelope=excluded.envelope,
                  state=excluded.state,
                  completed_at=excluded.completed_at,
                  output_path=excluded.output_path,
                  error=excluded.error
                """,
                (
                    dispatch["id"],
                    dispatch["intent_id"],
                    dispatch["adapter_name"],
                    json.dumps(dispatch.get("envelope", {})),
                    dispatch["state"],
                    dispatch.get("started_at", iso()),
                    dispatch.get("completed_at"),
                    dispatch.get("output_path"),
                    dispatch.get("error"),
                ),
            )
            if receipt:
                await db.execute(
                    """
                    INSERT INTO receipts(dispatch_id, service, capability, model, tokens_in, tokens_out, cost_class, success, output_summary, full_receipt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dispatch_id) DO UPDATE SET
                      service=excluded.service,
                      capability=excluded.capability,
                      model=excluded.model,
                      tokens_in=excluded.tokens_in,
                      tokens_out=excluded.tokens_out,
                      cost_class=excluded.cost_class,
                      success=excluded.success,
                      output_summary=excluded.output_summary,
                      full_receipt=excluded.full_receipt
                    """,
                    (
                        dispatch["id"],
                        receipt.get("service"),
                        receipt.get("capability"),
                        receipt.get("model"),
                        receipt.get("tokens_in", 0),
                        receipt.get("tokens_out", 0),
                        receipt.get("cost_class"),
                        int(bool(receipt.get("success"))),
                        receipt.get("output_summary", ""),
                        json.dumps(receipt),
                    ),
                )
            await self._audit_in_db(db, str(dispatch.get("adapter_name") or "dispatcher"), "dispatch_state", str(dispatch["id"]), dispatch)
            await self._refresh_intent_project_memory_in_db(db, str(dispatch["intent_id"]))
            await db.commit()

    async def record_dispatch_attempt(self, attempt: dict[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO dispatch_attempts(id, dispatch_id, intent_id, adapter_name, attempt_number, state, started_at, completed_at, error, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt["id"],
                    attempt["dispatch_id"],
                    attempt["intent_id"],
                    attempt["adapter_name"],
                    int(attempt["attempt_number"]),
                    attempt["state"],
                    attempt.get("started_at", iso()),
                    attempt.get("completed_at"),
                    attempt.get("error"),
                    json.dumps(attempt.get("detail", {})),
                ),
            )
            await self._audit_in_db(db, str(attempt.get("adapter_name") or "dispatcher"), "dispatch_attempt", str(attempt["id"]), attempt)
            await db.commit()

    async def list_dispatch_attempts(self, dispatch_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM dispatch_attempts"
        params: tuple[Any, ...]
        if dispatch_id:
            query += " WHERE dispatch_id = ?"
            params = (dispatch_id, limit)
        else:
            params = (limit,)
        query += " ORDER BY started_at DESC LIMIT ?"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, params)).fetchall()
            return [dict(r) for r in rows]

    async def list_dispatches(self, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute("SELECT * FROM dispatches ORDER BY started_at DESC LIMIT ?", (limit,))
            ).fetchall()
            return [dict(r) for r in rows]

    async def recover_interrupted_dispatches(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute("SELECT * FROM dispatches WHERE state IN ('running', 'started')")
            ).fetchall()
            recovered: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                detail = {
                    "dispatch_id": item["id"],
                    "intent_id": item["intent_id"],
                    "adapter_name": item["adapter_name"],
                    "started_at": item["started_at"],
                }
                await db.execute(
                    "UPDATE dispatches SET state = ?, completed_at = ?, error = ? WHERE id = ?",
                    ("interrupted", iso(), "Interrupted by Orchestrator restart before final receipt.", item["id"]),
                )
                await db.execute(
                    "UPDATE intents SET state = ?, completed_at = COALESCE(completed_at, ?) WHERE id = ?",
                    ("interrupted", iso(), item["intent_id"]),
                )
                repair_id = f"rep_{uuid.uuid4().hex[:12]}"
                await db.execute(
                    """
                    INSERT INTO repair_queue(id, created_at, failure_source, failure_detail, suggested_action, resolved)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (
                        repair_id,
                        iso(),
                        str(item["adapter_name"]),
                        f"Dispatch {item['id']} was running when the Orchestrator restarted.",
                        "Review dispatch envelope and rerun the intent if still needed.",
                    ),
                )
                detail["repair_id"] = repair_id
                await self._audit_in_db(db, "orchestrator", "dispatch_interrupted", str(item["id"]), detail)
                recovered.append(detail)
            await db.commit()
            return recovered

    async def add_notification(self, notification: Notification, delivered_channels: list[str] | None = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO notifications(id, created_at, severity, project_id, intent_id, title, body, actions, delivered_channels, acknowledged) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    notification.id,
                    iso(notification.ts),
                    notification.severity,
                    notification.project_id,
                    notification.intent_id,
                    notification.title,
                    notification.body,
                    json.dumps(notification.actions),
                    json.dumps(delivered_channels or []),
                ),
            )
            await self._audit_in_db(db, "notification_spine", "notification_added", notification.id, notification.model_dump(mode="json"))
            if notification.project_id:
                await self._refresh_working_memory_in_db(db, notification.project_id)
            elif notification.intent_id:
                await self._refresh_intent_project_memory_in_db(db, notification.intent_id)
            await db.commit()

    async def list_pending_approvals(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT n.*, i.raw_text, i.consequence_tier
                    FROM notifications n
                    LEFT JOIN intents i ON i.id = n.intent_id
                    WHERE n.severity = 'approval_request' AND n.acknowledged = 0
                    ORDER BY n.created_at DESC
                    """
                )
            ).fetchall()
            approvals: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["actions"] = json.loads(str(item.get("actions") or "[]"))
                approvals.append(item)
            return approvals

    async def acknowledge_approval(self, intent_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE notifications SET acknowledged = 1 WHERE intent_id = ? AND severity = 'approval_request'",
                (intent_id,),
            )
            await self._refresh_intent_project_memory_in_db(db, intent_id)
            await db.commit()

    async def record_quota_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
        if not snapshots:
            return
        async with aiosqlite.connect(self.path) as db:
            for snapshot in snapshots:
                await db.execute(
                    """
                    INSERT INTO quota_ledger(provider, window_start, window_duration_seconds, units_consumed, units_limit, last_dispatch_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(snapshot.get("provider")),
                        str(snapshot.get("reset_at") or iso()),
                        0,
                        int(snapshot.get("units_consumed") or 0),
                        int(snapshot.get("units_limit") or 0),
                        snapshot.get("last_dispatch_id"),
                    ),
                )
            await db.commit()

    async def list_quota_ledger(self, limit: int = 50) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT rowid, * FROM quota_ledger ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                )
            ).fetchall()
            return [dict(r) for r in rows]

    async def latest_quota_state(self) -> dict[str, dict[str, Any]]:
        rows = await self.list_quota_ledger(limit=500)
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            provider = str(row.get("provider") or "")
            if not provider or provider in latest:
                continue
            limit = int(row.get("units_limit") or 0)
            consumed = int(row.get("units_consumed") or 0)
            remaining = max(limit - consumed, 0) if limit > 0 else 0
            latest[provider] = {
                "provider": provider,
                "units_consumed": consumed,
                "units_limit": limit,
                "remaining": remaining,
                "percent_remaining": (remaining / limit * 100) if limit > 0 else None,
                "window_start": row.get("window_start"),
                "last_dispatch_id": row.get("last_dispatch_id"),
            }
        return latest

    async def add_repair_item(self, failure_source: str, failure_detail: str, suggested_action: str) -> str:
        repair_id = f"rep_{uuid.uuid4().hex[:12]}"
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO repair_queue(id, created_at, failure_source, failure_detail, suggested_action, resolved)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (repair_id, iso(), failure_source, failure_detail, suggested_action),
            )
            await db.commit()
        return repair_id

    async def add_or_get_open_repair_item(self, failure_source: str, failure_detail: str, suggested_action: str) -> str:
        open_items = [item for item in await self.list_repair_queue() if item.get("failure_source") == failure_source]
        if open_items:
            return str(open_items[0].get("id") or "")
        return await self.add_repair_item(failure_source, failure_detail, suggested_action)

    async def resolve_repair_items(self, failure_source: str, detail: dict[str, Any] | None = None) -> int:
        resolved_at = iso()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE repair_queue SET resolved = 1, resolved_at = ? WHERE failure_source = ? AND resolved = 0",
                (resolved_at, failure_source),
            )
            count = cursor.rowcount
            if count:
                await self._audit_in_db(db, "repair_queue", "repair_items_resolved", failure_source, {"count": count, **(detail or {})})
            await db.commit()
            return int(count or 0)

    async def list_repair_queue(self, include_resolved: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM repair_queue"
        params: tuple[Any, ...]
        if include_resolved:
            params = (limit,)
        else:
            query += " WHERE resolved = 0"
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, params)).fetchall()
            return [dict(r) for r in rows]

    async def record_discovery(self, scope: str, delta: dict[str, Any], summary: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO discovery_log(id, ran_at, scope, delta, summary) VALUES (?, ?, ?, ?, ?)",
                (f"disc_{uuid.uuid4().hex[:16]}", iso(), scope, json.dumps(delta), summary),
            )
            await self._audit_in_db(db, "discovery", "discovery_recorded", scope, {"delta": delta, "summary": summary})
            await db.commit()

    async def upsert_standing_order(self, folder_path: str, policy_yaml: str, enabled: bool = False, order_id: str | None = None) -> str:
        standing_order_id = order_id or f"so_{uuid.uuid4().hex[:12]}"
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO standing_orders(id, folder_path, policy_yaml, enabled, last_fired_at)
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                  folder_path=excluded.folder_path,
                  policy_yaml=excluded.policy_yaml,
                  enabled=excluded.enabled
                """,
                (standing_order_id, folder_path, policy_yaml, int(enabled)),
            )
            await self._audit_in_db(db, "scheduler", "standing_order_upserted", standing_order_id, {"folder_path": folder_path, "enabled": enabled})
            await db.commit()
        return standing_order_id

    async def list_standing_orders(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM standing_orders ORDER BY folder_path")).fetchall()
            return [dict(r) for r in rows]

    async def mark_standing_order_fired(self, order_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE standing_orders SET last_fired_at = ? WHERE id = ?", (iso(), order_id))
            await self._audit_in_db(db, "scheduler", "standing_order_fired", order_id, {})
            await db.commit()

    async def upsert_scheduler_task(
        self,
        name: str,
        task_type: str,
        target_ref: str,
        payload: dict[str, Any] | None = None,
        schedule_kind: str = "manual",
        interval_seconds: int = 0,
        enabled: bool = False,
        task_id: str | None = None,
        next_run_at: str | None = None,
        review_state: str = "not_required",
    ) -> str:
        scheduler_task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO scheduler_tasks(id, name, task_type, target_ref, payload, schedule_kind, interval_seconds, enabled, review_state, next_run_at, last_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  task_type=excluded.task_type,
                  target_ref=excluded.target_ref,
                  payload=excluded.payload,
                  schedule_kind=excluded.schedule_kind,
                  interval_seconds=excluded.interval_seconds,
                  enabled=excluded.enabled,
                  review_state=CASE
                    WHEN scheduler_tasks.review_state IS NULL OR scheduler_tasks.review_state = 'not_required' THEN excluded.review_state
                    ELSE scheduler_tasks.review_state
                  END,
                  next_run_at=excluded.next_run_at,
                  updated_at=excluded.updated_at
                """,
                (
                    scheduler_task_id,
                    name,
                    task_type,
                    target_ref,
                    json.dumps(payload or {}),
                    schedule_kind,
                    int(interval_seconds),
                    int(enabled),
                    review_state,
                    next_run_at,
                    now,
                    now,
                ),
            )
            await self._audit_in_db(db, "scheduler", "scheduler_task_upserted", scheduler_task_id, {"name": name, "task_type": task_type, "target_ref": target_ref, "enabled": enabled})
            await db.commit()
        return scheduler_task_id

    async def list_scheduler_tasks(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM scheduler_tasks ORDER BY enabled DESC, name")).fetchall()
            tasks: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                try:
                    item["payload"] = json.loads(str(item.get("payload") or "{}"))
                except json.JSONDecodeError:
                    item["payload"] = {}
                tasks.append(item)
            return tasks

    async def get_scheduler_task(self, task_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM scheduler_tasks WHERE id = ?", (task_id,))).fetchone()
            if row is None:
                return None
            item = dict(row)
            try:
                item["payload"] = json.loads(str(item.get("payload") or "{}"))
            except json.JSONDecodeError:
                item["payload"] = {}
            return item

    async def set_scheduler_task_enabled(self, task_id: str, enabled: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM scheduler_tasks WHERE id = ?", (task_id,))).fetchone()
            if row is None:
                await self._audit_in_db(db, "scheduler", "scheduler_task_enable_failed", task_id, {"reason": "missing", "enabled": enabled})
                await db.commit()
                raise ValueError(f"scheduler task not found: {task_id}")
            item = dict(row)
            if enabled and item.get("task_type") == "legacy_claude_scheduled_task" and item.get("review_state") != "owner_approved":
                detail = {
                    "enabled": enabled,
                    "task_type": item.get("task_type"),
                    "review_state": item.get("review_state"),
                    "reason": "legacy_task_requires_owner_approval",
                }
                await self._audit_in_db(db, "scheduler", "scheduler_task_enable_denied", task_id, detail)
                await db.commit()
                raise PermissionError("legacy scheduler task requires owner approval before enabling")
            await db.execute("UPDATE scheduler_tasks SET enabled = ?, updated_at = ? WHERE id = ?", (int(enabled), iso(), task_id))
            await self._audit_in_db(db, "scheduler", "scheduler_task_enabled", task_id, {"enabled": enabled})
            await db.commit()

    async def review_scheduler_task(self, task_id: str, review_state: str, reviewer: str = "owner", note: str = "") -> dict[str, Any]:
        allowed = {"needs_review", "owner_approved", "rejected"}
        if review_state not in allowed:
            raise ValueError(f"review_state must be one of {sorted(allowed)}")
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM scheduler_tasks WHERE id = ?", (task_id,))).fetchone()
            if row is None:
                await self._audit_in_db(db, "scheduler", "scheduler_task_review_failed", task_id, {"reason": "missing"})
                await db.commit()
                raise ValueError(f"scheduler task not found: {task_id}")
            now = iso()
            await db.execute(
                "UPDATE scheduler_tasks SET review_state = ?, reviewed_at = ?, reviewed_by = ?, review_note = ?, updated_at = ? WHERE id = ?",
                (review_state, now, reviewer, note, now, task_id),
            )
            detail = {"review_state": review_state, "reviewed_by": reviewer, "review_note": note}
            await self._audit_in_db(db, "scheduler", "scheduler_task_reviewed", task_id, detail)
            await db.commit()
            return {"id": task_id, **detail, "reviewed_at": now}

    async def activate_scheduler_task(self, task_id: str, reviewer: str = "owner", note: str = "") -> dict[str, Any]:
        await self.review_scheduler_task(task_id, "owner_approved", reviewer=reviewer, note=note)
        await self.set_scheduler_task_enabled(task_id, True)
        return {"id": task_id, "enabled": True, "review_state": "owner_approved"}

    async def mark_scheduler_task_run(self, task_id: str, interval_seconds: int = 0) -> None:
        now_dt = datetime.now(timezone.utc)
        next_run_at = (now_dt + timedelta(seconds=interval_seconds)).isoformat() if interval_seconds > 0 else None
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE scheduler_tasks SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
                (now_dt.isoformat(), next_run_at, now_dt.isoformat(), task_id),
            )
            await self._audit_in_db(db, "scheduler", "scheduler_task_ran", task_id, {"next_run_at": next_run_at})
            await db.commit()

    async def migrate_standing_orders_to_scheduler_tasks(self) -> dict[str, Any]:
        orders = await self.list_standing_orders()
        created = 0
        existing = {task["target_ref"] for task in await self.list_scheduler_tasks() if task.get("task_type") == "standing_order_scan"}
        for order in orders:
            order_id = str(order["id"])
            if order_id in existing:
                continue
            await self.upsert_scheduler_task(
                name=f"Standing order: {Path(str(order.get('folder_path') or '')).name or order_id}",
                task_type="standing_order_scan",
                target_ref=order_id,
                payload={"folder_path": order.get("folder_path")},
                schedule_kind="manual",
                interval_seconds=0,
                enabled=bool(order.get("enabled")),
                task_id=f"task_{order_id}",
            )
            created += 1
        result = {"standing_orders": len(orders), "created": created, "existing": len(existing)}
        await self.audit("scheduler", "standing_orders_migrated", "scheduler_tasks", result)
        return result

    async def list_activity(self, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            notifications = await (
                await db.execute(
                    "SELECT created_at AS ts, 'notification' AS kind, severity AS action, title AS target, body AS detail FROM notifications"
                )
            ).fetchall()
            routing = await (
                await db.execute(
                    "SELECT decided_at AS ts, 'routing' AS kind, chosen_adapter AS action, intent_id AS target, reasoning AS detail FROM routing_decisions"
                )
            ).fetchall()
            dispatches = await (
                await db.execute(
                    "SELECT completed_at AS ts, 'dispatch' AS kind, state AS action, adapter_name AS target, COALESCE(output_path, error, '') AS detail FROM dispatches"
                )
            ).fetchall()
        rows = [dict(r) for r in [*notifications, *routing, *dispatches] if dict(r).get("ts")]
        rows.sort(key=lambda r: str(r["ts"]), reverse=True)
        return rows[:limit]

    async def audit(self, actor: str, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await self._audit_in_db(db, actor, action, target, detail)
            await db.commit()

    async def list_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))).fetchall()
            return [dict(r) for r in rows]
