from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from orchestrator.config import Settings, ensure_runtime_dirs
from orchestrator.gmail_response_agent.store import GMAIL_RESPONSE_AGENT_SCHEMA
from orchestrator.models import Capability, Intent, Notification, RoutingDecision, Selection, ServiceInfo
from orchestrator.usage.tokens import estimate_tokens
from orchestrator.workspace_runtime import DEFAULT_MODE_PACKS, WORKSPACE_ROOTS, WorkspacePolicyError


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
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(schema)
            await self._ensure_scheduler_task_columns(db)
            await self._ensure_resource_event_columns(db)
            await self._ensure_service_columns(db)
            await self._ensure_worker_card_columns(db)
            await self._ensure_receipt_columns(db)
            await self._ensure_budget_lane_tables(db)
            await self._ensure_operation_quality_scores_table(db)
            await self._ensure_skill_hook_receipts_table(db)
            await self._ensure_gmail_response_agent_tables(db)
            await db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, iso()),
            )
            await db.commit()

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
            "workspace_id": "ALTER TABLE scheduler_tasks ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'unclassified_legacy'",
            "review_state": "ALTER TABLE scheduler_tasks ADD COLUMN review_state TEXT DEFAULT 'not_required'",
            "reviewed_at": "ALTER TABLE scheduler_tasks ADD COLUMN reviewed_at TEXT",
            "reviewed_by": "ALTER TABLE scheduler_tasks ADD COLUMN reviewed_by TEXT",
            "review_note": "ALTER TABLE scheduler_tasks ADD COLUMN review_note TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                await db.execute(statement)

    async def _ensure_resource_event_columns(self, db: aiosqlite.Connection) -> None:
        """Bring metering receipts forward without rewriting historic events."""
        rows = await (await db.execute("PRAGMA table_info(resource_events)")).fetchall()
        columns = {str(row[1]) for row in rows}
        if "measurement_source" not in columns:
            await db.execute(
                "ALTER TABLE resource_events ADD COLUMN measurement_source TEXT NOT NULL DEFAULT 'reported_runtime'"
            )

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
        await db.executescript(GMAIL_RESPONSE_AGENT_SCHEMA)
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
        workspace_id: str = "unclassified_legacy",
    ) -> str:
        scheduler_task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO scheduler_tasks(id, workspace_id, name, task_type, target_ref, payload, schedule_kind, interval_seconds, enabled, review_state, next_run_at, last_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  workspace_id=excluded.workspace_id,
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
                    workspace_id,
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
            await self._audit_in_db(db, "scheduler", "scheduler_task_upserted", scheduler_task_id, {"workspace_id": workspace_id, "name": name, "task_type": task_type, "target_ref": target_ref, "enabled": enabled})
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
                workspace_id="unclassified_legacy",
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

    @staticmethod
    def _decode_json(value: object, fallback: Any) -> Any:
        if value is None:
            return fallback
        try:
            return json.loads(str(value))
        except json.JSONDecodeError:
            return fallback

    @classmethod
    def _decode_workspace_row(cls, row: aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for column, output, fallback in (
            ("policy_json", "policy", {}),
            ("payload_json", "payload", {}),
            ("receipt_json", "receipt", {}),
            ("allowed_workspaces_json", "allowed_workspaces", []),
            ("preloaded_skills_json", "preloaded_skills", []),
            ("model_requirements_json", "model_requirements", {}),
            ("budget_json", "budget", {}),
            ("completion_test_json", "completion_test", {}),
            ("frozen_mode_pack_json", "frozen_mode_pack", {}),
            ("connector_policy_json", "connector_policy", {}),
            ("delivery_policy_json", "delivery_policy", {}),
            ("skill_checks_json", "skill_checks", {}),
            ("roles_json", "roles", []),
            ("evidence_json", "evidence", {}),
            ("tools_json", "tools", []),
            ("episode_ids_json", "episode_ids", []),
            ("replay_json", "replay", {}),
            ("rollback_json", "rollback", {}),
            ("quota_state_json", "quota_state", {}),
        ):
            if column in item:
                item[output] = cls._decode_json(item.get(column), fallback)
        return item

    async def _require_workspace_in_db(self, db: aiosqlite.Connection, workspace_id: str) -> dict[str, Any]:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM workspaces WHERE id = ? AND state = 'active'", (workspace_id,))).fetchone()
        if row is None:
            raise WorkspacePolicyError(f"workspace is not active: {workspace_id}")
        return self._decode_workspace_row(row)

    async def bootstrap_workspace_roots(self) -> list[dict[str, Any]]:
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            for workspace in WORKSPACE_ROOTS:
                await db.execute(
                    """
                    INSERT INTO workspaces(id, label, classification, policy_json, state, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        workspace["id"],
                        workspace["label"],
                        workspace["classification"],
                        json.dumps(workspace["policy"], sort_keys=True),
                        now,
                        now,
                    ),
                )
            await self._audit_in_db(db, "workspace", "workspace_roots_bootstrapped", "workspaces", {"ids": [row["id"] for row in WORKSPACE_ROOTS]})
            await db.commit()
        return await self.list_workspaces()

    async def list_workspaces(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM workspaces ORDER BY id")).fetchall()
            return [self._decode_workspace_row(row) for row in rows]

    async def create_work_packet(
        self,
        *,
        workspace_id: str,
        intent: str,
        payload: dict[str, Any],
        mode_pack_id: str | None,
        consumer: str,
        source_workspace_id: str | None = None,
        state: str = "queued",
    ) -> dict[str, Any]:
        packet_id = f"wp_{uuid.uuid4().hex[:16]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await self._require_workspace_in_db(db, workspace_id)
            if source_workspace_id:
                await self._require_workspace_in_db(db, source_workspace_id)
            await db.execute(
                """
                INSERT INTO work_packets(id, workspace_id, source_workspace_id, intent, payload_json, mode_pack_id, consumer, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (packet_id, workspace_id, source_workspace_id, intent, json.dumps(payload, sort_keys=True), mode_pack_id, consumer, state, now),
            )
            await self._audit_in_db(
                db,
                "workspace",
                "work_packet_created",
                packet_id,
                {"workspace_id": workspace_id, "source_workspace_id": source_workspace_id, "mode_pack_id": mode_pack_id, "consumer": consumer},
            )
            await db.commit()
        packet = await self.get_work_packet(packet_id)
        assert packet is not None
        return packet

    async def get_work_packet(self, packet_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM work_packets WHERE id = ?", (packet_id,))).fetchone()
            return self._decode_workspace_row(row) if row is not None else None

    async def list_work_packets(self, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute("SELECT * FROM work_packets WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?", (workspace_id, limit))
            ).fetchall()
            return [self._decode_workspace_row(row) for row in rows]

    async def propose_transfer(self, *, work_packet_id: str, target_workspace_id: str, summary: str) -> dict[str, Any]:
        proposal_id = f"xfer_{uuid.uuid4().hex[:16]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            packet_row = await (await db.execute("SELECT * FROM work_packets WHERE id = ?", (work_packet_id,))).fetchone()
            if packet_row is None:
                raise WorkspacePolicyError(f"work packet not found: {work_packet_id}")
            packet = self._decode_workspace_row(packet_row)
            source_workspace_id = str(packet["workspace_id"])
            await self._require_workspace_in_db(db, target_workspace_id)
            if (source_workspace_id, target_workspace_id) != ("personal", "example"):
                raise WorkspacePolicyError("only Personal-to-Example transfer proposals are permitted by this foundation")
            await db.execute(
                """
                INSERT INTO transfer_proposals(id, source_workspace_id, target_workspace_id, source_work_packet_id, summary, state, created_at)
                VALUES (?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (proposal_id, source_workspace_id, target_workspace_id, work_packet_id, summary, now),
            )
            await self._audit_in_db(
                db,
                "workspace",
                "transfer_proposed",
                proposal_id,
                {"source_workspace_id": source_workspace_id, "target_workspace_id": target_workspace_id, "source_work_packet_id": work_packet_id},
            )
            await db.commit()
        proposal = await self.get_transfer_proposal(proposal_id)
        assert proposal is not None
        return proposal

    async def get_transfer_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM transfer_proposals WHERE id = ?", (proposal_id,))).fetchone()
            return self._decode_workspace_row(row) if row is not None else None

    async def list_transfer_proposals(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM transfer_proposals"
        params: tuple[Any, ...] = ()
        if state:
            query += " WHERE state = ?"
            params = (state,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params = (*params, limit)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, params)).fetchall()
            return [self._decode_workspace_row(row) for row in rows]

    async def decide_transfer(self, proposal_id: str, *, approve: bool, decided_by: str) -> dict[str, Any]:
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            proposal_row = await (await db.execute("SELECT * FROM transfer_proposals WHERE id = ?", (proposal_id,))).fetchone()
            if proposal_row is None:
                raise WorkspacePolicyError(f"transfer proposal not found: {proposal_id}")
            proposal = self._decode_workspace_row(proposal_row)
            if proposal["state"] != "proposed":
                raise WorkspacePolicyError(f"transfer proposal is already decided: {proposal_id}")
            if not approve:
                receipt = {"decision": "rejected", "target_written": False, "decided_by": decided_by, "decided_at": now}
                await db.execute(
                    "UPDATE transfer_proposals SET state = 'rejected', decided_by = ?, decided_at = ?, receipt_json = ? WHERE id = ?",
                    (decided_by, now, json.dumps(receipt, sort_keys=True), proposal_id),
                )
                await self._audit_in_db(db, "owner", "transfer_rejected", proposal_id, receipt)
                await db.commit()
            else:
                packet_row = await (
                    await db.execute("SELECT * FROM work_packets WHERE id = ?", (proposal["source_work_packet_id"],))
                ).fetchone()
                if packet_row is None:
                    raise WorkspacePolicyError("source work packet disappeared before transfer approval")
                source = self._decode_workspace_row(packet_row)
                target_packet_id = f"wp_{uuid.uuid4().hex[:16]}"
                await db.execute(
                    """
                    INSERT INTO work_packets(id, workspace_id, source_workspace_id, intent, payload_json, mode_pack_id, consumer, state, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        target_packet_id,
                        proposal["target_workspace_id"],
                        proposal["source_workspace_id"],
                        source["intent"],
                        json.dumps(source["payload"], sort_keys=True),
                        source.get("mode_pack_id"),
                        source["consumer"],
                        now,
                    ),
                )
                receipt = {
                    "decision": "approved",
                    "target_written": True,
                    "target_work_packet_id": target_packet_id,
                    "decided_by": decided_by,
                    "decided_at": now,
                }
                await db.execute(
                    """
                    UPDATE transfer_proposals
                    SET state = 'approved', decided_by = ?, decided_at = ?, target_work_packet_id = ?, receipt_json = ?
                    WHERE id = ?
                    """,
                    (decided_by, now, target_packet_id, json.dumps(receipt, sort_keys=True), proposal_id),
                )
                await self._audit_in_db(db, "owner", "transfer_approved", proposal_id, receipt)
                await db.commit()
        result = await self.get_transfer_proposal(proposal_id)
        assert result is not None
        return result

    async def seed_default_mode_packs(self) -> list[dict[str, Any]]:
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            for pack in DEFAULT_MODE_PACKS:
                await db.execute(
                    """
                    INSERT INTO mode_packs(
                        id, version, name, allowed_workspaces_json, source_authority, preloaded_skills_json,
                        model_requirements_json, evaluator, budget_json, completion_test_json, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        pack["id"],
                        pack["version"],
                        pack["name"],
                        json.dumps(pack["allowed_workspaces"], sort_keys=True),
                        pack["source_authority"],
                        json.dumps(pack["preloaded_skills"], sort_keys=True),
                        json.dumps(pack["model_requirements"], sort_keys=True),
                        pack["evaluator"],
                        json.dumps(pack["budget"], sort_keys=True),
                        json.dumps(pack["completion_test"], sort_keys=True),
                        now,
                        now,
                    ),
                )
            await self._audit_in_db(db, "workspace", "mode_packs_seeded", "mode_packs", {"ids": [pack["id"] for pack in DEFAULT_MODE_PACKS]})
            await db.commit()
        return await self.list_mode_packs()

    async def list_mode_packs(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM mode_packs WHERE state = 'active' ORDER BY id")).fetchall()
            return [self._decode_workspace_row(row) for row in rows]

    async def start_workspace_session(
        self,
        *,
        workspace_id: str,
        mode_pack_id: str,
        skill_capability_checks: dict[str, bool],
        connector_policy: dict[str, Any] | None = None,
        delivery_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = f"ws_{uuid.uuid4().hex[:16]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await self._require_workspace_in_db(db, workspace_id)
            db.row_factory = aiosqlite.Row
            pack_row = await (await db.execute("SELECT * FROM mode_packs WHERE id = ? AND state = 'active'", (mode_pack_id,))).fetchone()
            if pack_row is None:
                raise WorkspacePolicyError(f"mode pack not found: {mode_pack_id}")
            pack = self._decode_workspace_row(pack_row)
            if workspace_id not in pack["allowed_workspaces"]:
                raise WorkspacePolicyError(f"mode pack {mode_pack_id} is not allowed in workspace {workspace_id}")
            failed = [skill for skill in pack["preloaded_skills"] if skill_capability_checks.get(skill) is not True]
            if failed:
                raise WorkspacePolicyError(f"mode pack capability check failed: {', '.join(failed)}")
            frozen = {
                "id": pack["id"],
                "version": pack["version"],
                "name": pack["name"],
                "source_authority": pack["source_authority"],
                "preloaded_skills": pack["preloaded_skills"],
                "model_requirements": pack["model_requirements"],
                "evaluator": pack["evaluator"],
                "budget": pack["budget"],
                "completion_test": pack["completion_test"],
            }
            await db.execute(
                """
                INSERT INTO workspace_sessions(
                    id, workspace_id, mode_pack_id, frozen_mode_pack_json, connector_policy_json,
                    delivery_policy_json, skill_checks_json, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    session_id,
                    workspace_id,
                    mode_pack_id,
                    json.dumps(frozen, sort_keys=True),
                    json.dumps(connector_policy or {"workspace": workspace_id}, sort_keys=True),
                    json.dumps(delivery_policy or {"consumer_required": True}, sort_keys=True),
                    json.dumps(skill_capability_checks, sort_keys=True),
                    now,
                ),
            )
            await self._audit_in_db(db, "workspace", "workspace_session_started", session_id, {"workspace_id": workspace_id, "mode_pack_id": mode_pack_id})
            await db.commit()
        return await self.get_workspace_session(session_id)

    async def get_workspace_session(self, session_id: str) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM workspace_sessions WHERE id = ?", (session_id,))).fetchone()
            if row is None:
                raise WorkspacePolicyError(f"workspace session not found: {session_id}")
            return self._decode_workspace_row(row)

    async def record_resource_event(
        self,
        *,
        workspace_id: str,
        work_packet_id: str | None,
        provider: str,
        model: str,
        route: str,
        tokens_in: int,
        tokens_out: int,
        estimated_cost_usd: float | None,
        actual_cost_usd: float | None,
        quota_source: str,
        context_pressure: float | None,
        dispatch_id: str | None = None,
        quota_state: dict[str, Any] | None = None,
        measurement_source: str = "reported_runtime",
    ) -> dict[str, Any]:
        event_id = f"res_{uuid.uuid4().hex[:16]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await self._require_workspace_in_db(db, workspace_id)
            if work_packet_id:
                db.row_factory = aiosqlite.Row
                packet_row = await (await db.execute("SELECT workspace_id FROM work_packets WHERE id = ?", (work_packet_id,))).fetchone()
                if packet_row is None or str(packet_row["workspace_id"]) != workspace_id:
                    raise WorkspacePolicyError("resource event work packet must belong to its workspace")
            await db.execute(
                """
                INSERT INTO resource_events(
                    id, workspace_id, work_packet_id, dispatch_id, provider, model, route, tokens_in,
                    tokens_out, tokens_total, estimated_cost_usd, actual_cost_usd, quota_source,
                    measurement_source, quota_state_json, context_pressure, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    workspace_id,
                    work_packet_id,
                    dispatch_id,
                    provider,
                    model,
                    route,
                    tokens_in,
                    tokens_out,
                    tokens_in + tokens_out,
                    estimated_cost_usd,
                    actual_cost_usd,
                    quota_source,
                    measurement_source or "unknown",
                    json.dumps(quota_state or {}, sort_keys=True),
                    context_pressure,
                    now,
                ),
            )
            await self._audit_in_db(db, "meter", "resource_event_recorded", event_id, {"workspace_id": workspace_id, "provider": provider, "model": model, "quota_source": quota_source, "measurement_source": measurement_source})
            await db.commit()
        events = await self.list_resource_events(limit=1, event_id=event_id)
        return events[0]

    async def correct_resource_event(
        self,
        event_id: str,
        *,
        provider: str,
        model: str,
        quota_source: str,
        measurement_source: str,
        estimated_cost_usd: float | None,
        actual_cost_usd: float | None,
        quota_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Correct attribution only from a stronger runtime receipt, with an audit trail."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM resource_events WHERE id = ?", (event_id,))).fetchone()
            if row is None:
                raise WorkspacePolicyError(f"resource event not found: {event_id}")
            previous = dict(row)
            await db.execute(
                """
                UPDATE resource_events
                SET provider = ?, model = ?, quota_source = ?, measurement_source = ?,
                    estimated_cost_usd = ?, actual_cost_usd = ?, quota_state_json = ?
                WHERE id = ?
                """,
                (
                    provider or "unknown",
                    model or "unknown",
                    quota_source or "unknown",
                    measurement_source or "unknown",
                    estimated_cost_usd,
                    actual_cost_usd,
                    json.dumps(quota_state or {}, sort_keys=True),
                    event_id,
                ),
            )
            await self._audit_in_db(
                db,
                "meter",
                "resource_event_corrected",
                event_id,
                {
                    "workspace_id": previous["workspace_id"],
                    "previous_provider": previous["provider"],
                    "previous_model": previous["model"],
                    "provider": provider,
                    "model": model,
                    "quota_source": quota_source,
                    "measurement_source": measurement_source,
                    "estimated_cost_usd": estimated_cost_usd,
                    "actual_cost_usd": actual_cost_usd,
                    "quota_state": quota_state or {},
                },
            )
            await db.commit()
        events = await self.list_resource_events(limit=1, event_id=event_id)
        return events[0]

    async def list_resource_events(
        self,
        limit: int = 200,
        event_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM resource_events"
        clauses: list[str] = []
        params: list[Any] = []
        if event_id:
            clauses.append("id = ?")
            params.append(event_id)
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, tuple(params))).fetchall()
            return [self._decode_workspace_row(row) for row in rows]

    async def workspace_meter(self, workspace_id: str) -> dict[str, Any]:
        """Aggregate only the selected workspace's resource events for Hermes.

        The meter deliberately excludes work-packet text, payloads, memories and
        transfer content.  It is a resource readout, not a second task surface.
        """
        async with aiosqlite.connect(self.path) as db:
            await self._require_workspace_in_db(db, workspace_id)
        events = await self.list_resource_events(limit=1000, workspace_id=workspace_id)
        by_model: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events:
            key = (str(event.get("provider") or "unknown"), str(event.get("model") or "unknown"))
            row = by_model.setdefault(
                key,
                {
                    "provider": key[0],
                    "model": key[1],
                    "events": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "tokens_total": 0,
                    "estimated_cost_usd": 0.0,
                    "actual_cost_usd": 0.0,
                    "estimated_cost_unknown_events": 0,
                    "actual_cost_unknown_events": 0,
                    "quota_sources": set(),
                    "measurement_sources": set(),
                    "context_pressure_max": 0.0,
                    "last_event_at": None,
                },
            )
            row["events"] += 1
            row["tokens_in"] += int(event.get("tokens_in") or 0)
            row["tokens_out"] += int(event.get("tokens_out") or 0)
            row["tokens_total"] += int(event.get("tokens_total") or 0)
            if event.get("estimated_cost_usd") is None:
                row["estimated_cost_unknown_events"] += 1
            else:
                row["estimated_cost_usd"] += float(event["estimated_cost_usd"])
            if event.get("actual_cost_usd") is None:
                row["actual_cost_unknown_events"] += 1
            else:
                row["actual_cost_usd"] += float(event["actual_cost_usd"])
            row["quota_sources"].add(str(event.get("quota_source") or "unknown"))
            row["measurement_sources"].add(str(event.get("measurement_source") or "unknown"))
            row["context_pressure_max"] = max(row["context_pressure_max"], float(event.get("context_pressure") or 0.0))
            row["last_event_at"] = max(str(row["last_event_at"] or ""), str(event.get("created_at") or "")) or None

        provider_models = []
        for row in by_model.values():
            row["estimated_cost_usd"] = None if row["estimated_cost_unknown_events"] else round(row["estimated_cost_usd"], 6)
            row["actual_cost_usd"] = None if row["actual_cost_unknown_events"] else round(row["actual_cost_usd"], 6)
            row["quota_sources"] = sorted(row["quota_sources"])
            row["measurement_sources"] = sorted(row["measurement_sources"])
            provider_models.append(row)
        provider_models.sort(key=lambda item: (-int(item["tokens_total"]), str(item["provider"]), str(item["model"])))
        return {
            "workspace_id": workspace_id,
            "events": sum(int(row["events"]) for row in provider_models),
            "tokens_total": sum(int(row["tokens_total"]) for row in provider_models),
            "estimated_cost_usd": None if any(row["estimated_cost_usd"] is None for row in provider_models) else round(sum(float(row["estimated_cost_usd"]) for row in provider_models), 6),
            "actual_cost_usd": None if any(row["actual_cost_usd"] is None for row in provider_models) else round(sum(float(row["actual_cost_usd"]) for row in provider_models), 6),
            "quota_sources": sorted({source for row in provider_models for source in row["quota_sources"]}) or ["unknown"],
            "provider_models": provider_models,
        }

    async def record_evaluation_episode(
        self,
        *,
        workspace_id: str,
        work_packet_id: str,
        mode_pack_id: str,
        selected_model: str,
        tools: list[str],
        evidence: dict[str, Any],
        reviewer_outcome: str,
        delivery_receipt: str,
        cost_usd: float | None,
        failure_class: str | None,
        score: float | None,
        held_out: bool,
    ) -> dict[str, Any]:
        episode_id = f"eval_{uuid.uuid4().hex[:16]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await self._require_workspace_in_db(db, workspace_id)
            db.row_factory = aiosqlite.Row
            packet_row = await (await db.execute("SELECT workspace_id FROM work_packets WHERE id = ?", (work_packet_id,))).fetchone()
            if packet_row is None or str(packet_row["workspace_id"]) != workspace_id:
                raise WorkspacePolicyError("evaluation episode work packet must belong to its workspace")
            pack_row = await (await db.execute("SELECT id FROM mode_packs WHERE id = ?", (mode_pack_id,))).fetchone()
            if pack_row is None:
                raise WorkspacePolicyError(f"mode pack not found: {mode_pack_id}")
            await db.execute(
                """
                INSERT INTO evaluation_episodes(
                    id, workspace_id, work_packet_id, mode_pack_id, selected_model, tools_json, evidence_json,
                    reviewer_outcome, delivery_receipt, cost_usd, failure_class, score, held_out, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    workspace_id,
                    work_packet_id,
                    mode_pack_id,
                    selected_model,
                    json.dumps(tools, sort_keys=True),
                    json.dumps(evidence, sort_keys=True),
                    reviewer_outcome,
                    delivery_receipt,
                    cost_usd,
                    failure_class,
                    score,
                    int(held_out),
                    now,
                ),
            )
            await self._audit_in_db(db, "evaluation", "evaluation_episode_recorded", episode_id, {"workspace_id": workspace_id, "mode_pack_id": mode_pack_id, "held_out": held_out, "score": score})
            await db.commit()
        episodes = await self.list_evaluation_episodes(limit=1, episode_id=episode_id)
        return episodes[0]

    async def list_evaluation_episodes(
        self,
        limit: int = 200,
        episode_id: str | None = None,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM evaluation_episodes"
        clauses: list[str] = []
        params: list[Any] = []
        if episode_id:
            clauses.append("id = ?")
            params.append(episode_id)
        if not include_superseded:
            clauses.append("reviewer_outcome != 'superseded'")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, tuple(params))).fetchall()
            return [self._decode_workspace_row(row) for row in rows]

    async def supersede_nonruntime_evaluation_fixtures(self, run_id: str) -> dict[str, int]:
        """Retire simulated proof fixtures before recording runtime evaluation evidence."""
        superseded_episode_ids: list[str] = []
        superseded_policy_ids: list[str] = []
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            episode_rows = await (await db.execute("SELECT id, evidence_json FROM evaluation_episodes")).fetchall()
            for row in episode_rows:
                try:
                    evidence = json.loads(str(row["evidence_json"] or "{}"))
                except json.JSONDecodeError:
                    continue
                if isinstance(evidence, dict) and evidence.get("run_id") == run_id and evidence.get("review") == "held-out local replay":
                    superseded_episode_ids.append(str(row["id"]))
            for episode_id in superseded_episode_ids:
                await db.execute(
                    "UPDATE evaluation_episodes SET reviewer_outcome = 'superseded', failure_class = 'nonruntime_fixture_retired' WHERE id = ?",
                    (episode_id,),
                )

            if superseded_episode_ids:
                candidate_rows = await (await db.execute("SELECT id, episode_ids_json FROM policy_candidates")).fetchall()
                retired_set = set(superseded_episode_ids)
                for row in candidate_rows:
                    try:
                        episode_ids = set(json.loads(str(row["episode_ids_json"] or "[]")))
                    except json.JSONDecodeError:
                        continue
                    if episode_ids & retired_set:
                        policy_id = str(row["id"])
                        await db.execute(
                            "UPDATE policy_candidates SET state = 'superseded', updated_at = ? WHERE id = ?",
                            (iso(), policy_id),
                        )
                        superseded_policy_ids.append(policy_id)
            if superseded_episode_ids or superseded_policy_ids:
                await self._audit_in_db(
                    db,
                    "evaluation",
                    "nonruntime_evaluation_fixtures_superseded",
                    run_id,
                    {"episode_ids": superseded_episode_ids, "policy_candidate_ids": superseded_policy_ids},
                )
            await db.commit()
        return {"episodes": len(superseded_episode_ids), "policy_candidates": len(superseded_policy_ids)}

    async def create_policy_candidate(
        self,
        *,
        workspace_id: str,
        policy: dict[str, Any],
        baseline_score: float | None,
        episode_ids: list[str],
        sandbox_only: bool,
    ) -> dict[str, Any]:
        candidate_id = f"pol_{uuid.uuid4().hex[:16]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await self._require_workspace_in_db(db, workspace_id)
            if not episode_ids:
                raise WorkspacePolicyError("policy candidate requires evaluation episodes")
            await db.execute(
                """
                INSERT INTO policy_candidates(
                    id, workspace_id, policy_json, baseline_score, episode_ids_json, sandbox_only,
                    requires_approval, state, rollback_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
                """,
                (
                    candidate_id,
                    workspace_id,
                    json.dumps(policy, sort_keys=True),
                    baseline_score,
                    json.dumps(episode_ids, sort_keys=True),
                    int(sandbox_only),
                    int(not sandbox_only),
                    json.dumps({"action": "restore_prior_policy"}, sort_keys=True),
                    now,
                    now,
                ),
            )
            await self._audit_in_db(db, "evaluation", "policy_candidate_created", candidate_id, {"workspace_id": workspace_id, "sandbox_only": sandbox_only, "episode_count": len(episode_ids)})
            await db.commit()
        candidate = await self.get_policy_candidate(candidate_id)
        assert candidate is not None
        return candidate

    async def get_policy_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM policy_candidates WHERE id = ?", (candidate_id,))).fetchone()
            return self._decode_workspace_row(row) if row is not None else None

    async def replay_policy_candidate(
        self,
        candidate_id: str,
        *,
        candidate_score: float,
        held_out_episode_ids: list[str],
        replay_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            candidate_row = await (await db.execute("SELECT * FROM policy_candidates WHERE id = ?", (candidate_id,))).fetchone()
            if candidate_row is None:
                raise WorkspacePolicyError(f"policy candidate not found: {candidate_id}")
            candidate = self._decode_workspace_row(candidate_row)
            if candidate["state"] != "candidate":
                raise WorkspacePolicyError(f"policy candidate is already replayed: {candidate_id}")
            if not held_out_episode_ids:
                raise WorkspacePolicyError("policy candidate replay requires held-out episodes")
            placeholders = ",".join("?" for _ in held_out_episode_ids)
            held_out_rows = await (
                await db.execute(
                    f"SELECT id FROM evaluation_episodes WHERE id IN ({placeholders}) AND held_out = 1",
                    tuple(held_out_episode_ids),
                )
            ).fetchall()
            if {str(row["id"]) for row in held_out_rows} != set(held_out_episode_ids):
                raise WorkspacePolicyError("policy candidate replay requires held-out evaluation evidence")
            baseline_score = candidate.get("baseline_score")
            if replay_evidence is not None:
                try:
                    evidence_candidate_score = float(replay_evidence["candidate_mean"])
                    evidence_baseline_score = float(replay_evidence["baseline_mean"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise WorkspacePolicyError("policy candidate replay evidence must include baseline_mean and candidate_mean") from exc
                if abs(evidence_candidate_score - candidate_score) > 0.000001:
                    raise WorkspacePolicyError("policy candidate score must match held-out replay evidence")
                if baseline_score is not None and abs(evidence_baseline_score - float(baseline_score)) > 0.000001:
                    raise WorkspacePolicyError("policy candidate baseline must match held-out replay evidence")
            better = baseline_score is not None and candidate_score > float(baseline_score)
            if better and bool(candidate.get("sandbox_only")):
                state, decision = "promoted_sandbox", "promote"
            elif better:
                state, decision = "approval_required", "approval_required"
            else:
                state, decision = "rejected", "reject"
            replay = {
                "held_out_episode_ids": held_out_episode_ids,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
                "decision": decision,
                "replayed_at": now,
            }
            if replay_evidence is not None:
                replay["suite_id"] = str(replay_evidence.get("suite_id") or "unknown")
                replay["sparring"] = replay_evidence
            await db.execute(
                "UPDATE policy_candidates SET state = ?, replay_json = ?, updated_at = ? WHERE id = ?",
                (state, json.dumps(replay, sort_keys=True), now, candidate_id),
            )
            await self._audit_in_db(db, "evaluation", "policy_candidate_replayed", candidate_id, replay)
            await db.commit()
        candidate = await self.get_policy_candidate(candidate_id)
        assert candidate is not None
        return candidate

    async def start_swarm_run(
        self,
        *,
        workspace_id: str,
        work_packet_id: str,
        roles: list[dict[str, Any]],
        budget: dict[str, Any],
        reviewer: str,
        output_consumer: str,
        stop_rule: str,
    ) -> dict[str, Any]:
        if len(roles) < 3:
            raise WorkspacePolicyError("governed swarm requires at least three roles")
        role_names = [str(role.get("name") or "").strip() for role in roles]
        if not all(role_names) or len(set(role_names)) != len(role_names):
            raise WorkspacePolicyError("governed swarm roles must have unique names")
        if int(budget.get("max_tokens") or 0) <= 0:
            raise WorkspacePolicyError("governed swarm requires a positive token budget")
        if not reviewer.strip() or not output_consumer.strip() or not stop_rule.strip():
            raise WorkspacePolicyError("governed swarm requires reviewer, consumer, and stop rule")
        swarm_id = f"swm_{uuid.uuid4().hex[:16]}"
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            await self._require_workspace_in_db(db, workspace_id)
            db.row_factory = aiosqlite.Row
            packet_row = await (await db.execute("SELECT workspace_id FROM work_packets WHERE id = ?", (work_packet_id,))).fetchone()
            if packet_row is None or str(packet_row["workspace_id"]) != workspace_id:
                raise WorkspacePolicyError("swarm work packet must belong to its workspace")
            await db.execute(
                """
                INSERT INTO swarm_runs(
                    id, workspace_id, work_packet_id, roles_json, budget_json, reviewer, output_consumer,
                    stop_rule, side_effect_policy, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approval_required', 'running', ?)
                """,
                (
                    swarm_id,
                    workspace_id,
                    work_packet_id,
                    json.dumps(roles, sort_keys=True),
                    json.dumps(budget, sort_keys=True),
                    reviewer,
                    output_consumer,
                    stop_rule,
                    now,
                ),
            )
            await self._audit_in_db(
                db,
                "swarm",
                "swarm_started",
                swarm_id,
                {"workspace_id": workspace_id, "roles": role_names, "reviewer": reviewer, "consumer": output_consumer},
            )
            await db.commit()
        swarm = await self.get_swarm_run(swarm_id)
        assert swarm is not None
        return swarm

    async def get_swarm_run(self, swarm_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM swarm_runs WHERE id = ?", (swarm_id,))).fetchone()
            return self._decode_workspace_row(row) if row is not None else None

    async def complete_swarm_run(
        self,
        swarm_id: str,
        *,
        reviewer_outcome: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        now = iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM swarm_runs WHERE id = ?", (swarm_id,))).fetchone()
            if row is None:
                raise WorkspacePolicyError(f"swarm run not found: {swarm_id}")
            swarm = self._decode_workspace_row(row)
            if swarm["state"] != "running":
                raise WorkspacePolicyError(f"swarm run is not running: {swarm_id}")
            if str(receipt.get("consumer") or "") != str(swarm["output_consumer"]):
                raise WorkspacePolicyError("swarm receipt consumer must match the named output consumer")
            if str(reviewer_outcome).strip() not in {"accepted", "rejected"}:
                raise WorkspacePolicyError("swarm reviewer outcome must be accepted or rejected")
            await db.execute(
                """
                UPDATE swarm_runs
                SET state = 'completed', reviewer_outcome = ?, receipt_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (reviewer_outcome, json.dumps(receipt, sort_keys=True), now, swarm_id),
            )
            await self._audit_in_db(
                db,
                "swarm",
                "swarm_completed",
                swarm_id,
                {"reviewer_outcome": reviewer_outcome, "consumer": swarm["output_consumer"]},
            )
            await db.commit()
        swarm = await self.get_swarm_run(swarm_id)
        assert swarm is not None
        return swarm
