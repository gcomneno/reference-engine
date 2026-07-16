"""Atomic, checksum-verified packaged SQLite schema migrations."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

from reference_engine.errors import MigrationError

_MIGRATION_PACKAGE = "reference_engine.resources.migrations"


@dataclass(frozen=True)
class AppliedMigration:
    """Persisted metadata for one applied migration."""

    version: int
    name: str
    sha256: str
    applied_at: str


@dataclass(frozen=True)
class MigrationReport:
    """The migrations newly applied and skipped during one call."""

    applied: tuple[AppliedMigration, ...]
    skipped: tuple[AppliedMigration, ...]


@dataclass(frozen=True)
class MigrationDefinition:
    """A trusted migration supplied by the package or an internal test."""

    version: int
    name: str
    sql: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.sql).hexdigest()


def _statements(script: str, definition: MigrationDefinition) -> tuple[str, ...]:
    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise MigrationError(
            code="MIGRATION_SQL_INCOMPLETE",
            message="Migration SQL ends with an incomplete statement.",
            details={"version": definition.version, "name": definition.name},
        )
    return tuple(statements)


def _resource_definition(
    version: int, name: str, root: Traversable
) -> MigrationDefinition:
    try:
        content = root.joinpath(name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as error:
        raise MigrationError(
            code="MIGRATION_RESOURCE_UNAVAILABLE",
            message="A packaged migration resource could not be read.",
            details={"version": version, "name": name},
        ) from error
    return MigrationDefinition(version, name, content)


def _packaged_migrations() -> tuple[MigrationDefinition, ...]:
    try:
        root = files(_MIGRATION_PACKAGE)
    except (ModuleNotFoundError, OSError) as error:
        raise MigrationError(
            code="MIGRATION_RESOURCE_UNAVAILABLE",
            message="The packaged migration resources are unavailable.",
        ) from error
    return (_resource_definition(1, "001_initial_schema.sql", root),)


def get_applied_migrations(
    connection: sqlite3.Connection,
) -> tuple[AppliedMigration, ...]:
    """Return applied migration metadata in deterministic version order."""

    exists = connection.execute(
        "SELECT 1 FROM sqlite_schema "
        "WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return ()
    return tuple(
        AppliedMigration(*row)
        for row in connection.execute(
            "SELECT version, name, sha256, applied_at "
            "FROM schema_migrations ORDER BY version"
        )
    )


def _apply_migration_definitions(
    connection: sqlite3.Connection,
    definitions: tuple[MigrationDefinition, ...],
) -> MigrationReport:
    """Apply trusted definitions, committing each migration independently."""

    if connection.in_transaction:
        raise MigrationError(
            code="MIGRATION_ACTIVE_TRANSACTION",
            message="Migrations require a connection with no active transaction.",
        )

    ordered = tuple(sorted(definitions, key=lambda item: item.version))
    applied_now: list[AppliedMigration] = []
    skipped: list[AppliedMigration] = []
    for definition in ordered:
        try:
            script = definition.sql.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MigrationError(
                code="MIGRATION_RESOURCE_UNAVAILABLE",
                message="A packaged migration is not valid UTF-8.",
                details={"version": definition.version, "name": definition.name},
            ) from error
        statements = _statements(script, definition)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY CHECK (version > 0),
                    name TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL CHECK (
                        length(sha256) = 64
                        AND sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
                    applied_at TEXT NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ) STRICT
                """
            )
            existing_rows = connection.execute(
                "SELECT version, name, sha256, applied_at "
                "FROM schema_migrations WHERE version = ? OR name = ? "
                "ORDER BY version",
                (definition.version, definition.name),
            ).fetchall()
            if existing_rows:
                migrations = tuple(AppliedMigration(*row) for row in existing_rows)
                exact = tuple(
                    migration
                    for migration in migrations
                    if (
                        migration.version == definition.version
                        and migration.name == definition.name
                        and migration.sha256 == definition.sha256
                    )
                )
                if len(migrations) != 1 or not exact:
                    raise MigrationError(
                        code="MIGRATION_CHECKSUM_MISMATCH",
                        message=(
                            "Applied migration metadata conflicts with the packaged "
                            "migration."
                        ),
                        details={
                            "version": definition.version,
                            "name": definition.name,
                            "packaged_sha256": definition.sha256,
                            "conflicts": tuple(
                                {
                                    "version": migration.version,
                                    "name": migration.name,
                                    "sha256": migration.sha256,
                                }
                                for migration in migrations
                            ),
                        },
                    )
                connection.commit()
                skipped.append(exact[0])
                continue
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, sha256) VALUES (?, ?, ?)",
                (definition.version, definition.name, definition.sha256),
            )
            row = connection.execute(
                "SELECT version, name, sha256, applied_at "
                "FROM schema_migrations WHERE version = ?",
                (definition.version,),
            ).fetchone()
            connection.commit()
            applied_now.append(AppliedMigration(*row))
        except MigrationError:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise MigrationError(
                code="MIGRATION_APPLY_FAILED",
                message="A migration statement failed; its changes were rolled back.",
                details={"version": definition.version, "name": definition.name},
            ) from error
    return MigrationReport(tuple(applied_now), tuple(skipped))


def apply_migrations(connection: sqlite3.Connection) -> MigrationReport:
    """Apply all packaged migrations and return an inspectable report."""

    return _apply_migration_definitions(connection, _packaged_migrations())
