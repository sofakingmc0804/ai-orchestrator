from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

import aiosqlite

from orchestrator.config import Settings, ensure_runtime_dirs
from orchestrator.gmail_response_agent.store import GMAIL_RESPONSE_AGENT_SCHEMA
from orchestrator.models import Capability, Intent, Notification, RoutingDecision, Selection, ServiceInfo
from orchestrator.state.migration_runner import MigrationRunner, split_sql_statements
from orchestrator.usage.tokens import estimate_tokens


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


class _StateStoreDbFacade:
    """Compatibility shim for consolidation code copied from a persistent DB API."""

    def __init__(self, store: "StateStore"):
        self.store = store

    def _params(self, params: tuple[Any, ...]) -> Any:
        if len(params) == 1 and isinstance(params[0], (tuple, list, dict)):
            return params[0]
        return params

    async def fetch(self, query: str, *params: Any) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.store.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, self._params(params))).fetchall()
            return [dict(row) for row in rows]

    async def fetchrow(self, query: str, *params: Any) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.store.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(query, self._params(params))).fetchone()
            return dict(row) if row is not None else None

    async def execute(self, query: str, *params: Any) -> None:
        async with aiosqlite.connect(self.store.path) as db:
            await db.execute(query, self._params(params))
            await db.commit()

    async def commit(self) -> None:
        return None


class StateStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.state_path
        self.db = _StateStoreDbFacade(self)

    async def close(self) -> None:
        return None

    async def initialize(self) -> None:
        ensure_runtime_dirs(self.settings)
        schema = files("orchestrator.state").joinpath("schema.sql").read_text(encoding="utf-8")
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await self._bootstrap_v1_schema(db, schema)
            try:
                await db.execute("BEGIN IMMEDIATE")
                await self._ensure_scheduler_task_columns(db)
                await self._ensure_service_columns(db)
                await self._ensure_worker_card_columns(db)
                await self._ensure_receipt_columns(db)
                await self._ensure_budget_lane_tables(db)
                await self._ensure_operation_quality_scores_table(db)
                await self._ensure_skill_hook_receipts_table(db)
                await self._ensure_gmail_response_agent_tables(db)
                await self._ensure_schema_migration_columns(db)
                await db.execute(
                    """
                    INSERT OR IGNORE INTO schema_migrations(version, applied_at, name, status, checksum)
                    VALUES (?, ?, 'legacy_baseline', 'applied', NULL)
                    """,
                    (1, iso()),
                )
                await db.commit()
            except Exception:
                if db.in_transaction:
                    await db.rollback()
                raise
            await MigrationRunner(self.settings).apply(db)

    async def _bootstrap_v1_schema(self, db: aiosqlite.Connection, schema: str) -> None:
        """Run the idempotent v1 bootstrap with a bounded fresh-database lock retry."""
        for attempt in range(5):
            try:
                await db.executescript(schema)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                if db.in_transaction:
                    await db.rollback()
                await asyncio.sleep(0.05 * (2**attempt))

    async def _ensure_schema_migration_columns(self, db: aiosqlite.Connection) -> None:
        rows = await (await db.execute("PRAGMA table_info(schema_migrations)")).fetchall()
        columns = {str(row[1]) for row in rows}
        additions = {
            "name": "ALTER TABLE schema_migrations ADD COLUMN name TEXT",
            "status": "ALTER TABLE schema_migrations ADD COLUMN status TEXT",
            "checksum": "ALTER TABLE schema_migrations ADD COLUMN checksum TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                await db.execute(statement)
        await db.execute(
            """
            UPDATE schema_migrations
            SET name='legacy_baseline', status='applied', checksum=NULL
            WHERE version=1
            """
        )

    @staticmethod
    def _legacy_orchestrator_home() -> Path:
        configured = os.getenv("ORCHESTRATOR_LEGACY_HOME")
        return Path(configured).expanduser() if configured else Path.home() / ".orchestrator"

    @staticmethod
    def _repo_adapter_proof_path(path_text: str, legacy_root: Path, repo_root: Path) -> str | None:
        if not path_text:
            return None
        try:
            path = Path(path_text).expanduser().resolve(strict=False)
            old_root = legacy_root.expanduser().resolve(strict=False)
            relative = path.relative_to(old_root)
        except (OSError, ValueError):
            return None
        candidate = repo_root / relative
        return str(candidate) if candidate.exists() else None

    async def migrate_legacy_adapter_proof_paths(self, legacy_home: Path | None = None) -> dict[str, int]:
        legacy_root = (legacy_home or self._legacy_orchestrator_home()) / "adapter-proof"
        repo_root = self.settings.home / "adapter-proof"
        try:
            if legacy_root.resolve(strict=False) == repo_root.resolve(strict=False):
                return {"dispatches": 0, "receipts": 0}
        except OSError:
            return {"dispatches": 0, "receipts": 0}
        if not repo_root.exists():
            return {"dispatches": 0, "receipts": 0}

        dispatch_updates = 0
        receipt_updates = 0
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT d.id, d.output_path, r.full_receipt
                    FROM dispatches d
                    LEFT JOIN receipts r ON r.dispatch_id = d.id
                    WHERE d.output_path IS NOT NULL
                    """
                )
            ).fetchall()
            for row in rows:
                dispatch_id = str(row["id"])
                new_output = self._repo_adapter_proof_path(str(row["output_path"] or ""), legacy_root, repo_root)
                if new_output and new_output != str(row["output_path"]):
                    await db.execute("UPDATE dispatches SET output_path = ? WHERE id = ?", (new_output, dispatch_id))
                    dispatch_updates += 1

                full_receipt = row["full_receipt"]
                if not full_receipt:
                    continue
                try:
                    payload = json.loads(str(full_receipt))
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                changed = False
                for key in ("output_path", "receipt_path"):
                    value = payload.get(key)
                    if not isinstance(value, str):
                        continue
                    replacement = self._repo_adapter_proof_path(value, legacy_root, repo_root)
                    if replacement and replacement != value:
                        payload[key] = replacement
                        changed = True
                if changed:
                    await db.execute(
                        "UPDATE receipts SET full_receipt = ? WHERE dispatch_id = ?",
                        (json.dumps(payload), dispatch_id),
                    )
                    receipt_updates += 1
            if dispatch_updates or receipt_updates:
                await self._audit_in_db(
                    db,
                    "runtime_migration",
                    "legacy_adapter_proof_paths_migrated",
                    str(repo_root),
                    {"dispatches": dispatch_updates, "receipts": receipt_updates, "legacy_root": str(legacy_root)},
                )
            await db.commit()
        return {"dispatches": dispatch_updates, "receipts": receipt_updates}

    async def _ensure_service_columns(self, db: aiosqlite.Connection) -> None:
        rows = await (await db.execute("PRAGMA table_info(services)")).fetchall()
        columns = {str(row[1]) for row in rows}
        additions = {
            "detail": "ALTER TABLE services ADD COLUMN detail TEXT",
            "repair_action": "ALTER TABLE services ADD COLUMN repair_action TEXT",
            "optional": "ALTER TABLE services ADD COLUMN optional INTEGER DEFAULT 0",
        }
        for column, statement in additions.items():
            if column not in columns:
                await db.execute(statement)

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

    async def _ensure_budget_lane_tables(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_usage_snapshots (
                id TEXT PRIMARY KEY,
                service_id TEXT,
                account_id TEXT,
                profile_id TEXT,
                subscription_name TEXT,
                plan_name TEXT,
                source_type TEXT,
                source_command TEXT,
                tokens_limit INTEGER,
                tokens_used_total INTEGER,
                tokens_remaining INTEGER,
                tokens_used_by_app INTEGER,
                tokens_used_elsewhere INTEGER,
                reset_at TEXT,
                checked_at TEXT,
                ok BOOLEAN,
                confidence TEXT,
                status TEXT,
                error TEXT,
                usage_windows_json TEXT,
                raw_json TEXT
            )
            """
        )
        subscription_rows = await (await db.execute("PRAGMA table_info(subscription_usage_snapshots)")).fetchall()
        subscription_columns = {str(row[1]) for row in subscription_rows}
        subscription_additions = {
            "status": "ALTER TABLE subscription_usage_snapshots ADD COLUMN status TEXT",
            "usage_windows_json": "ALTER TABLE subscription_usage_snapshots ADD COLUMN usage_windows_json TEXT",
        }
        for column, statement in subscription_additions.items():
            if column not in subscription_columns:
                await db.execute(statement)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS api_budget_policies (
                id TEXT PRIMARY KEY,
                provider_id TEXT,
                account_id TEXT,
                policy_name TEXT,
                budget_limit_usd REAL,
                budget_window TEXT,
                current_spend_usd REAL,
                reset_at TEXT,
                enabled BOOLEAN DEFAULT 0,
                updated_at TEXT,
                note TEXT
            )
            """
        )

        token_rows = await (await db.execute("PRAGMA table_info(token_usage)")).fetchall()
        token_columns = {str(row[1]) for row in token_rows}
        if "success" not in token_columns:
            await db.execute("ALTER TABLE token_usage ADD COLUMN success INTEGER")

    async def _ensure_worker_card_columns(self, db: aiosqlite.Connection) -> None:
        rows = await (await db.execute("PRAGMA table_info(worker_cards)")).fetchall()
        columns = {str(row[1]) for row in rows}
        additions = {
            "badges_json": "ALTER TABLE worker_cards ADD COLUMN badges_json TEXT DEFAULT '[]'",
            "marginal_cost_json": "ALTER TABLE worker_cards ADD COLUMN marginal_cost_json TEXT DEFAULT '{}'",
            "dynamic_state_json": "ALTER TABLE worker_cards ADD COLUMN dynamic_state_json TEXT DEFAULT '{}'",
            "updated_at": "ALTER TABLE worker_cards ADD COLUMN updated_at TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                await db.execute(statement)

    async def _ensure_receipt_columns(self, db: aiosqlite.Connection) -> None:
        rows = await (await db.execute("PRAGMA table_info(receipts)")).fetchall()
        columns = {str(row[1]) for row in rows}
        additions = {
            "worker_id": "ALTER TABLE receipts ADD COLUMN worker_id TEXT",
            "job_class": "ALTER TABLE receipts ADD COLUMN job_class TEXT",
            "routing_reasoning": "ALTER TABLE receipts ADD COLUMN routing_reasoning TEXT",
            "budget_state_json": "ALTER TABLE receipts ADD COLUMN budget_state_json TEXT",
            "raw_output": "ALTER TABLE receipts ADD COLUMN raw_output TEXT",
            "proof_kind": "ALTER TABLE receipts ADD COLUMN proof_kind TEXT",
            "created_at": "ALTER TABLE receipts ADD COLUMN created_at TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                await db.execute(statement)

    async def _ensure_gmail_response_agent_tables(self, db: aiosqlite.Connection) -> None:
        for statement in split_sql_statements(GMAIL_RESPONSE_AGENT_SCHEMA):
            await db.execute(statement)
        rows = await (await db.execute("PRAGMA table_info(gmail_response_candidates)")).fetchall()
        columns = {str(row[1]) for row in rows}
        if "authority_evidence_json" not in columns:
            await db.execute("ALTER TABLE gmail_response_candidates ADD COLUMN authority_evidence_json TEXT")
        preflight_rows = await (await db.execute("PRAGMA table_info(gmail_draft_preflights)")).fetchall()
        preflight_columns = {str(row[1]) for row in preflight_rows}
        if "retrieval_steps_json" not in preflight_columns:
            await db.execute("ALTER TABLE gmail_draft_preflights ADD COLUMN retrieval_steps_json TEXT")
        learned_rows = await (await db.execute("PRAGMA table_info(gmail_learned_precedents)")).fetchall()
        learned_columns = {str(row[1]) for row in learned_rows}
        if "precedent_type" not in learned_columns:
            await db.execute("ALTER TABLE gmail_learned_precedents ADD COLUMN precedent_type TEXT DEFAULT 'approved'")
        if "approval_state" not in learned_columns:
            await db.execute("ALTER TABLE gmail_learned_precedents ADD COLUMN approval_state TEXT DEFAULT 'approved'")

    async def _ensure_skill_hook_receipts_table(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_hook_receipts (
                id TEXT PRIMARY KEY,
                plan_id TEXT,
                session_id TEXT,
                turn_id TEXT,
                hook_event_name TEXT,
                cwd TEXT,
                prompt TEXT,
                tool_name TEXT,
                decision TEXT,
                confidence REAL,
                selected_skills_json TEXT,
                interpreted_actions_json TEXT,
                authority_checks_json TEXT,
                confirmation_state TEXT,
                terminal_state_requirement TEXT,
                reason TEXT,
                raw_event_json TEXT,
                created_at TEXT
            )
            """
        )

    async def _ensure_operation_quality_scores_table(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_quality_scores (
                id TEXT PRIMARY KEY,
                dispatch_id TEXT REFERENCES dispatches(id),
                worker_id TEXT,
                operation_domain TEXT,
                validator_name TEXT,
                composite_score REAL,
                dimensional_scores_json TEXT,
                task_id TEXT,
                proof_kind TEXT DEFAULT 'live',
                validation_json TEXT,
                created_at TEXT
            )
            """
        )

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
                    INSERT INTO services(id, name, service_group, adapter_name, protocol, install_path, version, health_state, detail, repair_action, optional, last_probe_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      name=excluded.name, service_group=excluded.service_group, adapter_name=excluded.adapter_name,
                      protocol=excluded.protocol, install_path=excluded.install_path, version=excluded.version,
                      health_state=excluded.health_state, detail=excluded.detail, repair_action=excluded.repair_action,
                      optional=excluded.optional, last_probe_at=excluded.last_probe_at, updated_at=excluded.updated_at
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
                        s.detail,
                        s.repair_action,
                        1 if s.optional else 0,
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

    @staticmethod
    def _delegated_work_item_from_row(row: aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target, default in (
            ("operation_task_json", "operation_task", {}),
            ("validation_json", "validation", {}),
        ):
            try:
                parsed = json.loads(str(item.get(source) or "{}"))
            except json.JSONDecodeError:
                parsed = default
            item[target] = parsed if isinstance(parsed, dict) else default
        return item

    async def enqueue_delegated_work_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_text = str(payload.get("raw_text") or "").strip()
        consumer = str(payload.get("consumer") or "").strip()
        job_class = str(payload.get("job_class") or "").strip()
        mode = str(payload.get("mode") or "immediate").strip().lower()
        operation_task = payload.get("operation_task")
        if not raw_text:
            raise ValueError("delegated work item requires raw_text")
        if not consumer:
            raise ValueError("delegated work item requires a named consumer")
        if not job_class:
            raise ValueError("delegated work item requires job_class")
        if mode not in {"immediate", "discretionary"}:
            raise ValueError("delegated work item mode must be immediate or discretionary")
        if not isinstance(operation_task, dict) or not operation_task:
            raise ValueError("delegated work item requires an operation_task validator")
        min_quality_score = float(payload.get("min_quality_score", 1.0))
        if not 0.0 <= min_quality_score <= 1.0:
            raise ValueError("min_quality_score must be between 0 and 1")

        now = iso()
        item = {
            "id": f"wrk_{uuid.uuid4().hex[:16]}",
            "source": str(payload.get("source") or "owner"),
            "raw_text": raw_text,
            "consumer": consumer,
            "job_class": job_class,
            "project_root": str(payload.get("project_root") or "") or None,
            "mode": mode,
            "operation_task_json": json.dumps(operation_task, sort_keys=True),
            "min_quality_score": min_quality_score,
            "state": "queued",
            "dispatch_id": None,
            "output_path": None,
            "receipt_path": None,
            "validation_json": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO delegated_work_items(
                    id, source, raw_text, consumer, job_class, project_root, mode,
                    operation_task_json, min_quality_score, state, dispatch_id,
                    output_path, receipt_path, validation_json, error, created_at,
                    updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(item.values()),
            )
            await self._audit_in_db(db, "delegated_work", "work_item_enqueued", item["id"], {
                "consumer": consumer,
                "job_class": job_class,
                "mode": mode,
                "min_quality_score": min_quality_score,
            })
            await db.commit()
        return self._delegated_work_item_from_row(item)

    async def list_delegated_work_items(
        self,
        states: set[str] | None = None,
        limit: int = 100,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM delegated_work_items"
        params: list[Any] = []
        if states:
            normalized = sorted({str(state) for state in states})
            query += " WHERE state IN (" + ",".join("?" for _ in normalized) + ")"
            params.extend(normalized)
        query += " ORDER BY created_at " + ("DESC" if newest_first else "ASC") + " LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, tuple(params))).fetchall()
            return [self._delegated_work_item_from_row(row) for row in rows]

    async def get_delegated_work_item(self, item_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM delegated_work_items WHERE id = ?", (item_id,))).fetchone()
            return self._delegated_work_item_from_row(row) if row is not None else None

    async def update_delegated_work_item(
        self,
        item_id: str,
        state: str,
        *,
        dispatch_id: str | None = None,
        output_path: str | None = None,
        receipt_path: str | None = None,
        validation: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        allowed_states = {"queued", "running", "held", "awaiting_approval", "completed", "failed"}
        if state not in allowed_states:
            raise ValueError(f"unsupported delegated work state: {state}")
        now = iso()
        completed_at = now if state in {"completed", "failed"} else None
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                UPDATE delegated_work_items
                SET state = ?, dispatch_id = COALESCE(?, dispatch_id),
                    output_path = COALESCE(?, output_path), receipt_path = COALESCE(?, receipt_path),
                    validation_json = COALESCE(?, validation_json), error = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    state,
                    dispatch_id,
                    output_path,
                    receipt_path,
                    json.dumps(validation, sort_keys=True) if validation is not None else None,
                    error,
                    now,
                    completed_at,
                    item_id,
                ),
            )
            row = await (await db.execute("SELECT * FROM delegated_work_items WHERE id = ?", (item_id,))).fetchone()
            if row is None:
                return None
            await self._audit_in_db(db, "delegated_work", "work_item_state_changed", item_id, {
                "state": state,
                "dispatch_id": dispatch_id,
                "error": error,
            })
            await db.commit()
            return self._delegated_work_item_from_row(row)

    @staticmethod
    def _discovered_work_item_from_row(row: aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        try:
            payload = json.loads(str(item.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload = {}
        item["payload"] = payload if isinstance(payload, dict) else {}
        return item

    async def upsert_discovered_work_item(self, candidate: dict[str, Any]) -> dict[str, Any]:
        required = ("fingerprint", "kind", "source_path", "project_root", "title", "description", "source_status")
        missing = [key for key in required if not str(candidate.get(key) or "").strip()]
        if missing:
            raise ValueError(f"discovered work item missing required fields: {', '.join(missing)}")
        now = iso()
        payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            existing = await (
                await db.execute("SELECT * FROM discovered_work_items WHERE fingerprint = ?", (str(candidate["fingerprint"]),))
            ).fetchone()
            if existing is None:
                item = {
                    "id": f"bkl_{uuid.uuid4().hex[:16]}",
                    "fingerprint": str(candidate["fingerprint"]),
                    "kind": str(candidate["kind"]),
                    "source_path": str(candidate["source_path"]),
                    "source_line": candidate.get("source_line"),
                    "source_task_id": str(candidate.get("source_task_id") or "") or None,
                    "project_root": str(candidate["project_root"]),
                    "title": str(candidate["title"]),
                    "description": str(candidate["description"]),
                    "source_status": str(candidate["source_status"]),
                    "priority": str(candidate.get("priority") or "") or None,
                    "state": str(candidate.get("state") or "discovered"),
                    "delegated_work_id": None,
                    "error": str(candidate.get("error") or "") or None,
                    "payload_json": json.dumps(payload, sort_keys=True),
                    "created_at": now,
                    "updated_at": now,
                }
                await db.execute(
                    """
                    INSERT INTO discovered_work_items(
                        id, fingerprint, kind, source_path, source_line, source_task_id,
                        project_root, title, description, source_status, priority, state,
                        delegated_work_id, error, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(item.values()),
                )
                await self._audit_in_db(db, "backlog_discovery", "candidate_discovered", item["id"], {
                    "source_path": item["source_path"],
                    "source_task_id": item["source_task_id"],
                    "kind": item["kind"],
                })
            else:
                existing_item = dict(existing)
                next_state = str(existing_item.get("state") or "discovered")
                next_delegated_work_id = existing_item.get("delegated_work_id")
                next_error = str(candidate.get("error") or "") or None
                if next_state == "promoted" and next_delegated_work_id:
                    delegated = await (
                        await db.execute(
                            "SELECT state, error FROM delegated_work_items WHERE id = ?",
                            (str(next_delegated_work_id),),
                        )
                    ).fetchone()
                    if delegated is not None and str(delegated["state"] or "") == "failed":
                        next_state = "discovered"
                        next_delegated_work_id = None
                        failure = str(delegated["error"] or "runtime_failure")
                        next_error = f"retry_after_delegated_failure:{failure}"
                await db.execute(
                    """
                    UPDATE discovered_work_items
                    SET title = ?, description = ?, source_status = ?, priority = ?,
                        state = ?, delegated_work_id = ?, error = ?, payload_json = ?, updated_at = ?
                    WHERE fingerprint = ?
                    """,
                    (
                        str(candidate["title"]),
                        str(candidate["description"]),
                        str(candidate["source_status"]),
                        str(candidate.get("priority") or "") or None,
                        next_state,
                        next_delegated_work_id,
                        next_error,
                        json.dumps(payload, sort_keys=True),
                        now,
                        str(candidate["fingerprint"]),
                    ),
                )
                if next_state == "discovered" and existing_item.get("delegated_work_id"):
                    await self._audit_in_db(
                        db,
                        "backlog_discovery",
                        "candidate_requeued_after_runtime_failure",
                        str(existing_item["id"]),
                        {"previous_delegated_work_id": existing_item["delegated_work_id"], "error": next_error},
                    )
                item = existing_item
                item.update(
                    {
                        "title": str(candidate["title"]),
                        "description": str(candidate["description"]),
                        "source_status": str(candidate["source_status"]),
                        "priority": str(candidate.get("priority") or "") or None,
                        "state": next_state,
                        "delegated_work_id": next_delegated_work_id,
                        "error": next_error,
                        "payload_json": json.dumps(payload, sort_keys=True),
                        "updated_at": now,
                    }
                )
            await db.commit()
            return self._discovered_work_item_from_row(item)

    async def list_discovered_work_items(
        self,
        states: set[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM discovered_work_items"
        params: list[Any] = []
        if states:
            normalized = sorted({str(state) for state in states})
            query += " WHERE state IN (" + ",".join("?" for _ in normalized) + ")"
            params.extend(normalized)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, tuple(params))).fetchall()
            return [self._discovered_work_item_from_row(row) for row in rows]

    async def mark_discovered_work_promoted(self, item_id: str, delegated_work_id: str) -> dict[str, Any] | None:
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "UPDATE discovered_work_items SET state = 'promoted', delegated_work_id = ?, updated_at = ? WHERE id = ?",
                (delegated_work_id, now, item_id),
            )
            row = await (await db.execute("SELECT * FROM discovered_work_items WHERE id = ?", (item_id,))).fetchone()
            if row is None:
                return None
            await self._audit_in_db(db, "backlog_discovery", "candidate_promoted", item_id, {"delegated_work_id": delegated_work_id})
            await db.commit()
            return self._discovered_work_item_from_row(row)

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
                    INSERT INTO receipts(dispatch_id, service, capability, model, tokens_in, tokens_out, cost_class, success, output_summary, full_receipt, worker_id, job_class, routing_reasoning, budget_state_json, raw_output, proof_kind, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dispatch_id) DO UPDATE SET
                      service=excluded.service,
                      capability=excluded.capability,
                      model=excluded.model,
                      tokens_in=excluded.tokens_in,
                      tokens_out=excluded.tokens_out,
                      cost_class=excluded.cost_class,
                      success=excluded.success,
                      output_summary=excluded.output_summary,
                      full_receipt=excluded.full_receipt,
                      worker_id=excluded.worker_id,
                      job_class=excluded.job_class,
                      routing_reasoning=excluded.routing_reasoning,
                      budget_state_json=excluded.budget_state_json,
                      raw_output=excluded.raw_output,
                      proof_kind=excluded.proof_kind,
                      created_at=excluded.created_at
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
                        receipt.get("worker_id"),
                        receipt.get("job_class"),
                        receipt.get("routing_reasoning"),
                        receipt.get("budget_state_json"),
                        receipt.get("raw_output"),
                        receipt.get("proof_kind"),
                        receipt.get("created_at") or iso(),
                    ),
                )
            await self._audit_in_db(db, str(dispatch.get("adapter_name") or "dispatcher"), "dispatch_state", str(dispatch["id"]), dispatch)
            await self._refresh_intent_project_memory_in_db(db, str(dispatch["intent_id"]))
            await db.commit()

    async def record_operation_quality_score(self, score: dict[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO operation_quality_scores(
                    id, dispatch_id, worker_id, operation_domain, validator_name,
                    composite_score, dimensional_scores_json, task_id, proof_kind,
                    validation_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    dispatch_id=excluded.dispatch_id,
                    worker_id=excluded.worker_id,
                    operation_domain=excluded.operation_domain,
                    validator_name=excluded.validator_name,
                    composite_score=excluded.composite_score,
                    dimensional_scores_json=excluded.dimensional_scores_json,
                    task_id=excluded.task_id,
                    proof_kind=excluded.proof_kind,
                    validation_json=excluded.validation_json,
                    created_at=excluded.created_at
                """,
                (
                    score.get("id") or f"oqs_{uuid.uuid4().hex[:16]}",
                    score["dispatch_id"],
                    score["worker_id"],
                    score["operation_domain"],
                    score["validator_name"],
                    float(score.get("composite_score") or 0.0),
                    json.dumps(score.get("dimensional_scores") or score.get("dimensional_scores_json") or {}),
                    score.get("task_id"),
                    score.get("proof_kind") or "live",
                    json.dumps(score.get("validation") or {}),
                    score.get("created_at") or iso(),
                ),
            )
            await self._audit_in_db(db, "quality_loop", "operation_quality_score", str(score["dispatch_id"]), score)
            await db.commit()

    async def list_operation_quality_scores(self, limit: int = 200) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT * FROM operation_quality_scores ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def load_live_operation_quality_scores(self, limit: int = 1000) -> dict[str, dict[str, dict[str, Any]]]:
        rows = await self.list_operation_quality_scores(limit=limit)
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            if str(row.get("proof_kind") or "") != "live":
                continue
            worker_id = str(row.get("worker_id") or "")
            domain = str(row.get("operation_domain") or "")
            if not worker_id or not domain:
                continue
            item = grouped.setdefault(worker_id, {}).setdefault(
                domain,
                {
                    "worker_id": worker_id,
                    "operation_domain": domain,
                    "sample_count": 0,
                    "score_total": 0.0,
                    "latest_dispatch_id": row.get("dispatch_id"),
                    "latest_created_at": row.get("created_at"),
                },
            )
            item["sample_count"] += 1
            item["score_total"] += float(row.get("composite_score") or 0.0)
            if str(row.get("created_at") or "") > str(item.get("latest_created_at") or ""):
                item["latest_dispatch_id"] = row.get("dispatch_id")
                item["latest_created_at"] = row.get("created_at")
        for by_domain in grouped.values():
            for item in by_domain.values():
                count = max(int(item.get("sample_count") or 0), 1)
                item["composite_score"] = round(float(item.pop("score_total")) / count, 4)
        return grouped

    async def record_token_usage(self, usage: dict[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO token_usage(
                    id, dispatch_id, attempt_id, intent_id, adapter_name, provider, model,
                    tokens_in, tokens_out, tokens_total, success, token_source, confidence,
                    quota_provider, quota_remaining_before, quota_remaining_after_estimate,
                    quota_limit, quota_ratio_after_estimate, quota_probe_type, quota_probe_ok,
                    raw_usage_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tokens_in=excluded.tokens_in,
                    tokens_out=excluded.tokens_out,
                    tokens_total=excluded.tokens_total,
                    token_source=excluded.token_source,
                    confidence=excluded.confidence,
                    quota_remaining_after_estimate=excluded.quota_remaining_after_estimate,
                    raw_usage_json=excluded.raw_usage_json
                """,
                (
                    usage["id"],
                    usage.get("dispatch_id"),
                    usage.get("attempt_id"),
                    usage.get("intent_id"),
                    usage.get("adapter_name"),
                    usage.get("provider"),
                    usage.get("model"),
                    int(usage.get("tokens_in") or 0),
                    int(usage.get("tokens_out") or 0),
                    int(usage.get("tokens_total") or 0),
                    int(bool(usage.get("success"))),
                    usage.get("token_source"),
                    usage.get("confidence"),
                    usage.get("quota_provider"),
                    usage.get("quota_remaining_before"),
                    usage.get("quota_remaining_after_estimate"),
                    usage.get("quota_limit"),
                    usage.get("quota_ratio_after_estimate"),
                    usage.get("quota_probe_type"),
                    int(bool(usage.get("quota_probe_ok"))) if usage.get("quota_probe_ok") is not None else None,
                    usage.get("raw_usage_json"),
                    usage.get("created_at") or iso(),
                ),
            )
            await db.commit()

    async def list_token_usage(self, dispatch_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM token_usage"
        params: tuple[Any, ...]
        if dispatch_id:
            query += " WHERE dispatch_id = ?"
            params = (dispatch_id, limit)
        else:
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, params)).fetchall()
            return [dict(r) for r in rows]

    async def token_usage_summary(self, limit: int = 1000) -> dict[str, dict[str, Any]]:
        rows = await self.list_token_usage(limit=limit)
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = f"{row.get('provider') or ''}|{row.get('model') or ''}"
            item = grouped.setdefault(
                key,
                {
                    "provider": row.get("provider"),
                    "model": row.get("model"),
                    "attempts": 0,
                    "tokens_total": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )
            item["attempts"] += 1
            item["tokens_total"] += int(row.get("tokens_total") or 0)
            item["tokens_in"] += int(row.get("tokens_in") or 0)
            item["tokens_out"] += int(row.get("tokens_out") or 0)
            item["successes"] = int(item.get("successes") or 0) + int(bool(row.get("success")))
        for item in grouped.values():
            attempts = max(int(item["attempts"]), 1)
            item["avg_tokens_total"] = item["tokens_total"] / attempts
            item["success_rate"] = int(item.get("successes") or 0) / attempts
        return grouped

    async def token_usage_by_directive(self, limit: int = 200) -> list[dict[str, Any]]:
        query = """
            SELECT
              COALESCE(intent_id, dispatch_id, id) AS directive_id,
              intent_id,
              COUNT(*) AS attempts,
              COUNT(DISTINCT dispatch_id) AS dispatches,
              SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successful_attempts,
              SUM(COALESCE(tokens_in, 0)) AS tokens_in,
              SUM(COALESCE(tokens_out, 0)) AS tokens_out,
              SUM(COALESCE(tokens_total, 0)) AS tokens_total,
              MIN(created_at) AS first_seen_at,
              MAX(created_at) AS last_seen_at,
              GROUP_CONCAT(DISTINCT adapter_name) AS adapters_csv,
              GROUP_CONCAT(DISTINCT provider) AS providers_csv,
              GROUP_CONCAT(DISTINCT model) AS models_csv,
              GROUP_CONCAT(DISTINCT token_source) AS token_sources_csv,
              GROUP_CONCAT(DISTINCT confidence) AS confidences_csv
            FROM token_usage
            GROUP BY COALESCE(intent_id, dispatch_id, id), intent_id
            ORDER BY last_seen_at DESC
            LIMIT ?
        """

        def split_csv(value: Any) -> list[str]:
            return sorted({part for part in str(value or "").split(",") if part})

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, (limit,))).fetchall()

        directives: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            completed = int(item.get("successful_attempts") or 0) > 0
            item["completed"] = completed
            item["tokens_per_completed_directive"] = int(item.get("tokens_total") or 0) if completed else None
            item["adapters"] = split_csv(item.pop("adapters_csv", ""))
            item["providers"] = split_csv(item.pop("providers_csv", ""))
            item["models"] = split_csv(item.pop("models_csv", ""))
            item["token_sources"] = split_csv(item.pop("token_sources_csv", ""))
            item["confidences"] = split_csv(item.pop("confidences_csv", ""))
            directives.append(item)
        return directives

    async def list_worker_cards(self, limit: int = 500) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute("SELECT * FROM worker_cards ORDER BY contract_type, worker_id LIMIT ?", (limit,))
            ).fetchall()
            return [dict(row) for row in rows]

    async def list_receipts(self, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute("SELECT * FROM receipts ORDER BY COALESCE(created_at, dispatch_id) DESC LIMIT ?", (limit,))
            ).fetchall()
            return [dict(row) for row in rows]

    async def record_skill_hook_receipt(self, receipt: dict[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO skill_hook_receipts(
                    id, plan_id, session_id, turn_id, hook_event_name, cwd, prompt, tool_name,
                    decision, confidence, selected_skills_json, interpreted_actions_json,
                    authority_checks_json, confirmation_state, terminal_state_requirement,
                    reason, raw_event_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    decision=excluded.decision,
                    reason=excluded.reason,
                    raw_event_json=excluded.raw_event_json
                """,
                (
                    receipt["id"],
                    receipt.get("plan_id"),
                    receipt.get("session_id"),
                    receipt.get("turn_id"),
                    receipt.get("hook_event_name"),
                    receipt.get("cwd"),
                    receipt.get("prompt"),
                    receipt.get("tool_name"),
                    receipt.get("decision"),
                    receipt.get("confidence"),
                    json.dumps(receipt.get("selected_skills") or []),
                    json.dumps(receipt.get("interpreted_actions") or []),
                    json.dumps(receipt.get("authority_checks") or []),
                    receipt.get("confirmation_state"),
                    receipt.get("terminal_state_requirement"),
                    receipt.get("reason"),
                    json.dumps(receipt.get("raw_event") or {}),
                    receipt.get("created_at") or iso(),
                ),
            )
            await self._audit_in_db(db, "skill_hook_gate", "skill_hook_receipt", str(receipt["id"]), receipt)
            await db.commit()

    async def record_contract_evaluation(
        self,
        *,
        contract_run_id: str,
        plan_id: str | None,
        session_id: str | None,
        turn_id: str | None,
        hook_event_name: str,
        prompt: str,
        evaluation: Any,
        decision: str,
        evidence_items: list[dict[str, Any]] | None = None,
    ) -> None:
        now = iso()
        numeric_json = json.dumps(evaluation.numeric_dict(), sort_keys=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO contract_runs(
                    id, plan_id, session_id, turn_id, prompt, axis_version,
                    possibility_space_count, terminal_state, numeric_result_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    possibility_space_count=excluded.possibility_space_count,
                    terminal_state=excluded.terminal_state,
                    numeric_result_json=excluded.numeric_result_json,
                    updated_at=excluded.updated_at
                """,
                (
                    contract_run_id,
                    plan_id,
                    session_id,
                    turn_id,
                    prompt,
                    "axes_v1",
                    evaluation.possibility_space_count,
                    evaluation.terminal_state,
                    numeric_json,
                    now,
                    now,
                ),
            )
            await db.execute("DELETE FROM possibility_items WHERE contract_run_id = ?", (contract_run_id,))
            await db.execute("DELETE FROM equivalence_classes WHERE contract_run_id = ?", (contract_run_id,))
            await db.execute("DELETE FROM evidence_items WHERE contract_run_id = ?", (contract_run_id,))
            await db.execute("DELETE FROM counterexamples WHERE contract_run_id = ?", (contract_run_id,))
            for item in evaluation.possibility_items:
                await db.execute(
                    """
                    INSERT INTO possibility_items(
                        id, contract_run_id, axis_tuple_json, requirement_level,
                        proof_level_required, equivalent_key, satisfaction_action, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{contract_run_id}_{item.id}",
                        contract_run_id,
                        json.dumps(item.axis_tuple, sort_keys=True),
                        item.requirement_level,
                        item.proof_level_required,
                        item.equivalent_key,
                        item.axis_tuple.get("satisfaction_action"),
                        now,
                    ),
                )
            for item in evaluation.equivalence_classes:
                await db.execute(
                    """
                    INSERT INTO equivalence_classes(
                        id, contract_run_id, representative_id, member_ids_json,
                        member_authority_surfaces_json, rule, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{contract_run_id}_{item.id}",
                        contract_run_id,
                        item.representative_id,
                        json.dumps(list(item.member_ids)),
                        json.dumps(list(item.member_authority_surfaces)),
                        item.rule,
                        now,
                    ),
                )
            for index, item in enumerate(evidence_items or [], start=1):
                await db.execute(
                    """
                    INSERT INTO evidence_items(
                        id, contract_run_id, evidence_class, authority_surface,
                        subject, verified, detail, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{contract_run_id}_ev_{index:05d}",
                        contract_run_id,
                        item.get("evidence_class"),
                        item.get("authority_surface"),
                        item.get("subject"),
                        1 if item.get("verified", True) else 0,
                        item.get("detail"),
                        now,
                    ),
                )
            for item in evaluation.counterexamples:
                await db.execute(
                    """
                    INSERT INTO counterexamples(
                        id, contract_run_id, axis_tuple_json, why_relevant,
                        failed_predicate, required_resolution, consequence_if_ignored, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{contract_run_id}_{item.counterexample_id}"[:240],
                        contract_run_id,
                        json.dumps(item.axis_tuple, sort_keys=True),
                        item.why_relevant,
                        item.failed_predicate,
                        item.required_resolution,
                        item.consequence_if_ignored,
                        now,
                    ),
                )
            await db.execute(
                """
                INSERT INTO contract_decisions(id, contract_run_id, hook_event_name, decision, numeric_result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{contract_run_id}_{hook_event_name.lower()}_{now.replace(':', '').replace('.', '')}",
                    contract_run_id,
                    hook_event_name,
                    decision,
                    numeric_json,
                    now,
                ),
            )
            await self._audit_in_db(db, "contract_evaluator", "contract_evaluation_recorded", contract_run_id, evaluation.numeric_dict())
            await db.commit()

    async def list_skill_hook_receipts(self, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute("SELECT * FROM skill_hook_receipts ORDER BY created_at DESC LIMIT ?", (limit,))
            ).fetchall()
            receipts: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                for source, target in [
                    ("selected_skills_json", "selected_skills"),
                    ("interpreted_actions_json", "interpreted_actions"),
                    ("authority_checks_json", "authority_checks"),
                    ("raw_event_json", "raw_event"),
                ]:
                    default_json = "{}" if source == "raw_event_json" else "[]"
                    try:
                        item[target] = json.loads(str(item.get(source) or default_json))
                    except json.JSONDecodeError:
                        item[target] = {} if source == "raw_event_json" else []
                receipts.append(item)
            return receipts

    async def list_budget_probes(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute('SELECT * FROM budget_probes ORDER BY provider_id, probe_type')).fetchall()
            return [dict(row) for row in rows]

    async def upsert_budget_probes(self, probes: list[dict[str, Any]]) -> dict[str, int]:
        stored = 0
        failed = 0
        async with aiosqlite.connect(self.path) as db:
            for probe in probes:
                await db.execute(
                    """
                    INSERT INTO budget_probes (
                        id, provider_id, probe_type, remaining, "limit",
                        reset_at, probed_at, ok, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        remaining=excluded.remaining,
                        "limit"=excluded."limit",
                        reset_at=excluded.reset_at,
                        probed_at=excluded.probed_at,
                        ok=excluded.ok,
                        error=excluded.error
                    """,
                    (
                        probe["id"],
                        probe["provider_id"],
                        probe["probe_type"],
                        probe.get("remaining"),
                        probe.get("limit"),
                        probe.get("reset_at"),
                        probe.get("probed_at"),
                        int(bool(probe.get("ok"))),
                        probe.get("error"),
                    ),
                )
                if probe.get("ok"):
                    stored += 1
                else:
                    failed += 1
            await db.commit()
        return {"stored": stored, "failed": failed, "total": len(probes)}

    async def upsert_subscription_usage_snapshots(self, snapshots: list[dict[str, Any]]) -> dict[str, int]:
        stored = 0
        failed = 0
        async with aiosqlite.connect(self.path) as db:
            service_ids = sorted({str(item.get("service_id") or "") for item in snapshots if item.get("service_id")})
            if service_ids:
                marks = ",".join("?" for _ in service_ids)
                await db.execute(f"DELETE FROM subscription_usage_snapshots WHERE service_id IN ({marks})", service_ids)
            for item in snapshots:
                await db.execute(
                    """
                    INSERT INTO subscription_usage_snapshots(
                        id, service_id, account_id, profile_id, subscription_name, plan_name,
                        source_type, source_command, tokens_limit, tokens_used_total,
                        tokens_remaining, tokens_used_by_app, tokens_used_elsewhere,
                        reset_at, checked_at, ok, confidence, status, error,
                        usage_windows_json, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        subscription_name=excluded.subscription_name,
                        plan_name=excluded.plan_name,
                        source_type=excluded.source_type,
                        source_command=excluded.source_command,
                        tokens_limit=excluded.tokens_limit,
                        tokens_used_total=excluded.tokens_used_total,
                        tokens_remaining=excluded.tokens_remaining,
                        tokens_used_by_app=excluded.tokens_used_by_app,
                        tokens_used_elsewhere=excluded.tokens_used_elsewhere,
                        reset_at=excluded.reset_at,
                        checked_at=excluded.checked_at,
                        ok=excluded.ok,
                        confidence=excluded.confidence,
                        status=excluded.status,
                        error=excluded.error,
                        usage_windows_json=excluded.usage_windows_json,
                        raw_json=excluded.raw_json
                    """,
                    (
                        item["id"],
                        item.get("service_id"),
                        item.get("account_id"),
                        item.get("profile_id"),
                        item.get("subscription_name"),
                        item.get("plan_name"),
                        item.get("source_type"),
                        item.get("source_command"),
                        item.get("tokens_limit"),
                        item.get("tokens_used_total"),
                        item.get("tokens_remaining"),
                        item.get("tokens_used_by_app"),
                        item.get("tokens_used_elsewhere"),
                        item.get("reset_at"),
                        item.get("checked_at"),
                        int(bool(item.get("ok"))),
                        item.get("confidence"),
                        item.get("status"),
                        item.get("error"),
                        item.get("usage_windows_json"),
                        item.get("raw_json"),
                    ),
                )
                if item.get("ok"):
                    stored += 1
                else:
                    failed += 1
            await db.commit()
        return {"stored": stored, "failed": failed, "total": len(snapshots)}

    async def list_subscription_usage_snapshots(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT *
                    FROM subscription_usage_snapshots
                    ORDER BY subscription_name, account_id, profile_id
                    """
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def list_api_budget_policies(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT *
                    FROM api_budget_policies
                    ORDER BY provider_id, account_id, policy_name
                    """
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def token_usage_by_subscription_source(self) -> dict[str, int]:
        alias_groups = {
            "openai_chatgpt": ("openai", "codex", "chatgpt", "openai-api"),
            "anthropic_claude": ("claude", "claude-max", "anthropic", "anthropic-api"),
            "github_copilot": ("github_copilot", "github-copilot", "copilot", "copilot-gh"),
            "google_gemini": ("gemini", "google", "google-gemini"),
            "nous_hermes": ("nous", "hermes", "nous-portal"),
        }
        totals = {key: 0 for key in alias_groups}
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT provider, adapter_name, model, quota_provider, SUM(tokens_total) AS tokens_total
                    FROM token_usage
                    GROUP BY provider, adapter_name, model, quota_provider
                    """
                )
            ).fetchall()
        for row in rows:
            haystack = " ".join(str(row[key] or "").lower() for key in ("provider", "adapter_name", "model", "quota_provider"))
            tokens = int(row["tokens_total"] or 0)
            for source_id, aliases in alias_groups.items():
                if any(alias in haystack for alias in aliases):
                    totals[source_id] += tokens
        return totals

    async def backfill_token_usage_from_receipts(self, limit: int = 10000) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT
                      r.*,
                      d.intent_id AS dispatch_intent_id,
                      d.adapter_name AS dispatch_adapter_name,
                      d.envelope AS dispatch_envelope,
                      d.completed_at AS dispatch_completed_at
                    FROM receipts r
                    JOIN dispatches d ON d.id = r.dispatch_id
                    LEFT JOIN token_usage tu ON tu.dispatch_id = r.dispatch_id
                    WHERE tu.id IS NULL
                    ORDER BY COALESCE(r.created_at, d.completed_at, r.dispatch_id) ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ).fetchall()

        created = 0
        skipped = 0
        for row in rows:
            item = dict(row)
            dispatch_id = str(item.get("dispatch_id") or "")
            if not dispatch_id:
                skipped += 1
                continue
            try:
                envelope = json.loads(str(item.get("dispatch_envelope") or "{}"))
            except json.JSONDecodeError:
                envelope = {}
            try:
                full_receipt = json.loads(str(item.get("full_receipt") or "{}"))
            except json.JSONDecodeError:
                full_receipt = {}

            prompt = ""
            raw_intent = envelope.get("intent") if isinstance(envelope, dict) else {}
            if isinstance(raw_intent, dict):
                prompt = str(raw_intent.get("raw_text") or "")
            output_summary = str(item.get("output_summary") or full_receipt.get("output_summary") or "")
            tokens_in = int(item.get("tokens_in") or 0) or estimate_tokens(prompt)
            tokens_out = int(item.get("tokens_out") or 0) or estimate_tokens(output_summary)
            usage = {
                "id": f"tok_backfill_{dispatch_id}",
                "dispatch_id": dispatch_id,
                "attempt_id": f"backfill:{dispatch_id}",
                "intent_id": item.get("dispatch_intent_id"),
                "adapter_name": item.get("service") or item.get("dispatch_adapter_name"),
                "provider": item.get("service") or item.get("dispatch_adapter_name"),
                "model": item.get("model"),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_total": tokens_in + tokens_out,
                "success": bool(item.get("success")),
                "token_source": "receipt_backfill",
                "confidence": "estimated",
                "quota_provider": item.get("service") or item.get("dispatch_adapter_name"),
                "raw_usage_json": json.dumps(
                    {
                        "source": "receipt_backfill",
                        "receipt_tokens_in": item.get("tokens_in"),
                        "receipt_tokens_out": item.get("tokens_out"),
                        "estimated_prompt": bool(not int(item.get("tokens_in") or 0)),
                        "estimated_output": bool(not int(item.get("tokens_out") or 0)),
                    },
                    sort_keys=True,
                ),
                "created_at": item.get("created_at") or item.get("dispatch_completed_at") or iso(),
            }
            await self.record_token_usage(usage)
            created += 1

        if created or skipped:
            await self.audit("token_flow", "backfilled_receipts", "token_usage", {"created": created, "skipped": skipped, "limit": limit})
        return {"created": created, "skipped": skipped, "limit": limit}

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

    async def list_live_proven_dispatches(self, limit: int = 500) -> list[dict[str, Any]]:
        """Completed dispatches whose receipt carries proof_kind='live'.

        This is the integrity floor for spec-status: a dispatch counts as
        'proven' only when this code path persisted real adapter evidence
        (raw_output), never when a bare completed row or hand-imported proof
        was written. Joins intents so callers can tell whether a queued
        selection actually drove the dispatch (selection -> dispatch round trip).
        """
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT d.id AS id, d.adapter_name AS adapter_name, d.output_path AS output_path,
                           r.proof_kind AS proof_kind, r.raw_output AS raw_output,
                           i.source AS intent_source, i.selections AS intent_selections
                    FROM dispatches d
                    JOIN receipts r ON r.dispatch_id = d.id
                    LEFT JOIN intents i ON i.id = d.intent_id
                    WHERE d.state = 'completed' AND r.proof_kind = 'live'
                    ORDER BY d.started_at DESC LIMIT ?
                    """,
                    (limit,),
                )
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

    async def set_scheduler_task_next_run(self, task_id: str, next_run_at: str, reason: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE scheduler_tasks SET next_run_at = ?, updated_at = ? WHERE id = ?",
                (next_run_at, iso(), task_id),
            )
            await self._audit_in_db(db, "scheduler", "scheduler_task_rescheduled", task_id, {"next_run_at": next_run_at, "reason": reason})
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
        if created:
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
