from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from orchestrator.config import Settings


_MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9][a-z0-9_]*)\.sql$")
_TRANSACTION_KEYWORDS = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
_BUSY_TIMEOUT_MS = 5_000


class MigrationError(RuntimeError):
    """Base error for a rejected or failed state migration."""


class MigrationCatalogError(MigrationError):
    """The on-disk migration catalog cannot be applied safely."""


class MigrationChecksumError(MigrationCatalogError):
    """An applied migration no longer matches its immutable checksum."""


class MigrationBackupError(MigrationError):
    """A readable pre-migration backup could not be published."""


class MigrationApplyError(MigrationError):
    """A migration failed and its transaction was rolled back."""


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    filename: str
    sql: str
    checksum: str
    statements: tuple[str, ...]


class MigrationRunner:
    def __init__(self, settings: Settings, migrations_dir: Path | str | None = None) -> None:
        self.settings = settings
        self.migrations_dir = Path(migrations_dir) if migrations_dir is not None else None

    async def apply(self, db: aiosqlite.Connection) -> list[int]:
        """Validate and atomically apply every pending numbered migration."""
        catalog = self._load_catalog()
        await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        await self._ensure_catalog_shape(db)
        await db.commit()

        failed_version: int | None = None
        backup_path: Path | None = None
        try:
            await db.execute("BEGIN IMMEDIATE")
            applied = await self._read_applied(db)
            self._validate_applied(catalog, applied)
            pending = [migration for migration in catalog if migration.version not in applied]
            if not pending:
                await db.commit()
                return []

            failed_version = pending[0].version
            try:
                backup_task = asyncio.create_task(
                    asyncio.to_thread(self._create_validated_backup, pending[0].version)
                )
                try:
                    backup_path = await asyncio.shield(backup_task)
                except asyncio.CancelledError:
                    try:
                        backup_path = await backup_task
                    except BaseException:
                        backup_path = None
                    raise
            except MigrationBackupError:
                raise
            except Exception as exc:  # pragma: no cover - defensive normalization
                raise MigrationBackupError(f"pre-migration backup failed: {exc}") from exc

            applied_versions: list[int] = []
            for migration in pending:
                failed_version = migration.version
                for statement in migration.statements:
                    await db.execute(statement)
                await db.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at, name, status, checksum)
                    VALUES (?, ?, ?, 'applied', ?)
                    """,
                    (migration.version, _now(), migration.name, migration.checksum),
                )
                applied_versions.append(migration.version)
            await db.commit()
            return applied_versions
        except asyncio.CancelledError as exc:
            await self._cleanup_failure(db, failed_version, exc, backup_path)
            raise
        except Exception as exc:
            await self._cleanup_failure(db, failed_version, exc, backup_path)
            if isinstance(exc, MigrationError):
                raise
            label = f"migration {failed_version}" if failed_version is not None else "migration batch"
            raise MigrationApplyError(f"{label} failed and was rolled back: {exc}") from exc

    def _load_catalog(self) -> list[_Migration]:
        entries: Iterable[Any]
        if self.migrations_dir is None:
            root = files("orchestrator.state").joinpath("migrations")
            entries = list(root.iterdir())
        else:
            if not self.migrations_dir.is_dir():
                raise MigrationCatalogError(f"migration catalog directory is missing: {self.migrations_dir}")
            entries = list(self.migrations_dir.iterdir())

        migrations: list[_Migration] = []
        seen: set[int] = set()
        for entry in sorted(entries, key=lambda item: item.name):
            if not entry.name.lower().endswith(".sql"):
                continue
            match = _MIGRATION_NAME.fullmatch(entry.name)
            if match is None:
                raise MigrationCatalogError(f"malformed migration filename: {entry.name}")
            version = int(match.group("version"))
            if version in seen:
                raise MigrationCatalogError(f"duplicate migration version: {version}")
            seen.add(version)
            sql = entry.read_text(encoding="utf-8")
            statements = tuple(split_sql_statements(sql))
            if not statements:
                raise MigrationCatalogError(f"migration {entry.name} contains no statements")
            for statement in statements:
                keyword = _first_keyword(statement)
                if keyword in _TRANSACTION_KEYWORDS:
                    raise MigrationCatalogError(
                        f"transaction-control statement {keyword} is forbidden in migration {entry.name}"
                    )
            migrations.append(
                _Migration(
                    version=version,
                    name=match.group("name"),
                    filename=entry.name,
                    sql=sql,
                    checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                    statements=statements,
                )
            )

        versions = [migration.version for migration in migrations]
        if versions:
            expected = list(range(2, versions[-1] + 1))
            if versions != expected:
                raise MigrationCatalogError(f"migration catalog gap: expected {expected}, found {versions}")
        return migrations

    async def _ensure_catalog_shape(self, db: aiosqlite.Connection) -> None:
        table = await (
            await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
        ).fetchone()
        if table is None:
            raise MigrationCatalogError("legacy schema_migrations table is missing")
        columns = {str(row[1]) for row in await (await db.execute("PRAGMA table_info(schema_migrations)")).fetchall()}
        additions = {
            "name": "ALTER TABLE schema_migrations ADD COLUMN name TEXT",
            "status": "ALTER TABLE schema_migrations ADD COLUMN status TEXT",
            "checksum": "ALTER TABLE schema_migrations ADD COLUMN checksum TEXT",
        }
        for column, sql in additions.items():
            if column not in columns:
                await db.execute(sql)
        await db.execute(
            """
            UPDATE schema_migrations
            SET name='legacy_baseline', status='applied', checksum=NULL
            WHERE version=1
            """
        )

    async def _read_applied(self, db: aiosqlite.Connection) -> dict[int, tuple[str | None, str | None, str | None]]:
        rows = await (
            await db.execute("SELECT version, name, status, checksum FROM schema_migrations ORDER BY version")
        ).fetchall()
        return {int(row[0]): (row[1], row[2], row[3]) for row in rows}

    def _validate_applied(
        self,
        catalog: list[_Migration],
        applied: dict[int, tuple[str | None, str | None, str | None]],
    ) -> None:
        baseline = applied.get(1)
        if baseline != ("legacy_baseline", "applied", None):
            raise MigrationCatalogError("version 1 must remain the legacy_baseline with a null checksum")
        by_version = {migration.version: migration for migration in catalog}
        for version, (name, status, checksum) in applied.items():
            if version == 1:
                continue
            migration = by_version.get(version)
            if migration is None:
                raise MigrationCatalogError(
                    f"applied migration version {version} is missing from catalog (unknown applied version)"
                )
            if status != "applied" or name != migration.name:
                raise MigrationCatalogError(f"applied migration {version} metadata does not match catalog")
            if checksum is None or checksum != migration.checksum:
                raise MigrationChecksumError(f"applied migration {version} checksum changed")

    def _create_validated_backup(self, next_version: int) -> Path:
        backup_dir = self.settings.home / "backups"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MigrationBackupError(f"cannot create backup directory: {exc}") from exc
        suffix = uuid.uuid4().hex
        final_path = backup_dir / f"state-pre-migration-v{next_version:04d}-{suffix}.sqlite"
        temp_path = backup_dir / f".{final_path.name}.tmp-{uuid.uuid4().hex}"
        try:
            with closing(sqlite3.connect(f"file:{self.settings.state_path.as_posix()}?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(temp_path)) as destination:
                    source.backup(destination)
                    destination.commit()
            with closing(sqlite3.connect(temp_path)) as check:
                integrity = check.execute("PRAGMA integrity_check").fetchone()
                foreign_keys = check.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != ("ok",):
                raise MigrationBackupError(f"backup integrity check failed: {integrity}")
            if foreign_keys:
                raise MigrationBackupError(f"backup foreign-key check failed: {foreign_keys[:3]}")
            os.replace(temp_path, final_path)
            return final_path
        except MigrationBackupError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise MigrationBackupError(f"pre-migration backup failed: {exc}") from exc
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _cleanup_failure(
        self,
        db: aiosqlite.Connection,
        version: int | None,
        exc: BaseException,
        backup_path: Path | None,
    ) -> None:
        async def cleanup() -> None:
            if db.in_transaction:
                await db.rollback()
            if db.in_transaction:
                raise MigrationApplyError("migration rollback did not end the transaction") from exc
            await self._record_repair(db, version, exc, backup_path)

        cleanup_task = asyncio.create_task(cleanup())
        cancellation: asyncio.CancelledError | None = None
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            cancellation = exc
            await cleanup_task
        if cancellation is not None:
            raise cancellation

    async def _record_repair(
        self,
        db: aiosqlite.Connection,
        version: int | None,
        exc: BaseException,
        backup_path: Path | None,
    ) -> None:
        backup_published = backup_path is not None
        backup_retained = bool(backup_path is not None and backup_path.is_file())
        fingerprint = hashlib.sha256(
            f"{version}|{type(exc).__name__}|{exc}".encode("utf-8", errors="replace")
        ).hexdigest()[:24]
        detail = json.dumps(
            {
                "version": version,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "backup_path": str(backup_path) if backup_path is not None else None,
                "backup_published": backup_published,
                "backup_retained": backup_retained,
                "auto_restore": False,
            },
            sort_keys=True,
        )
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                INSERT OR IGNORE INTO repair_queue(
                    id, created_at, failure_source, failure_detail, suggested_action, resolved
                ) VALUES (?, ?, 'state_migration', ?, ?, 0)
                """,
                (
                    f"state-migration-{fingerprint}",
                    _now(),
                    detail,
                    (
                        "Inspect the retained backup and migration catalog; repair forward, never auto-restore."
                        if backup_retained
                        else "Repair the migration or backup mechanism forward; no published backup is available."
                    ),
                ),
            )
            await db.commit()
        except Exception:
            if db.in_transaction:
                await db.rollback()
            raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if character == ";":
            candidate = "".join(buffer)
            if not sqlite3.complete_statement(candidate):
                continue
            if candidate.strip():
                statements.append(candidate.strip())
            buffer = []
    remainder = "".join(buffer)
    if remainder.strip():
        if _comment_only(remainder):
            return statements
        if not sqlite3.complete_statement(remainder):
            raise MigrationCatalogError("migration contains an incomplete SQL statement")
        statements.append(remainder.strip())
    return statements


def _comment_only(sql: str) -> bool:
    index = 0
    length = len(sql)
    while index < length:
        if sql[index].isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                return False
            index = end + 2
            continue
        return False
    return True


def _first_keyword(statement: str) -> str:
    text = statement.lstrip("\ufeff \t\r\n")
    while True:
        if text.startswith("--"):
            newline = text.find("\n")
            text = "" if newline < 0 else text[newline + 1 :].lstrip()
            continue
        if text.startswith("/*"):
            end = text.find("*/", 2)
            if end < 0:
                return ""
            text = text[end + 2 :].lstrip()
            continue
        break
    match = re.match(r"[A-Za-z]+", text)
    return match.group(0).upper() if match else ""
