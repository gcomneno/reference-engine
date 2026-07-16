"""Explicit SQLite persistence for artifacts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from reference_engine.errors import ArtifactRepositoryError


@dataclass(frozen=True)
class Artifact:
    """One immutable artifact registration."""

    id: int
    kind: str
    storage_scope: str
    retention_class: str
    relative_path: str
    sha256: str
    mime_type: str
    byte_size: int
    created_at: str
    registered_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        id=row["id"],
        kind=row["kind"],
        storage_scope=row["storage_scope"],
        retention_class=row["retention_class"],
        relative_path=row["relative_path"],
        sha256=row["sha256"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        created_at=row["created_at"],
        registered_at=row["registered_at"],
    )


def get_artifact(connection: sqlite3.Connection, artifact_id: int) -> Artifact | None:
    """Return an artifact by database ID, or ``None`` when it is unknown."""

    row = connection.execute(
        """SELECT id, kind, storage_scope, retention_class, relative_path, sha256,
                  mime_type, byte_size, created_at, registered_at
           FROM artifacts WHERE id = ?""",
        (artifact_id,),
    ).fetchone()
    return None if row is None else _artifact(row)


def get_artifact_by_content(
    connection: sqlite3.Connection, sha256: str, kind: str
) -> Artifact | None:
    """Return an artifact by the schema's unique content identity."""

    row = connection.execute(
        """SELECT id, kind, storage_scope, retention_class, relative_path, sha256,
                  mime_type, byte_size, created_at, registered_at
           FROM artifacts WHERE sha256 = ? AND kind = ?""",
        (sha256, kind),
    ).fetchone()
    return None if row is None else _artifact(row)


def get_artifact_by_storage(
    connection: sqlite3.Connection,
    storage_scope: str,
    relative_path: str,
) -> Artifact | None:
    """Return an artifact by the schema's unique storage identity."""

    row = connection.execute(
        """SELECT id, kind, storage_scope, retention_class, relative_path, sha256,
                  mime_type, byte_size, created_at, registered_at
           FROM artifacts WHERE storage_scope = ? AND relative_path = ?""",
        (storage_scope, relative_path),
    ).fetchone()
    return None if row is None else _artifact(row)


def _resolve_artifact_identity(
    connection: sqlite3.Connection,
    requested: tuple[str, str, str, str, str, str, int, str],
) -> Artifact | None:
    """Resolve persisted schema identities, returning ``None`` if neither exists."""

    (
        kind,
        storage_scope,
        retention_class,
        relative_path,
        sha256,
        mime_type,
        byte_size,
        created_at,
    ) = requested
    rows = connection.execute(
        """SELECT id, kind, storage_scope, retention_class, relative_path, sha256,
                  mime_type, byte_size, created_at, registered_at
           FROM artifacts
           WHERE (storage_scope = ? AND relative_path = ?)
              OR (sha256 = ? AND kind = ?)
           ORDER BY id""",
        (storage_scope, relative_path, sha256, kind),
    ).fetchall()
    if not rows:
        return None
    artifacts = tuple(_artifact(row) for row in rows)
    exact = tuple(
        item
        for item in artifacts
        if (
            item.kind,
            item.storage_scope,
            item.retention_class,
            item.relative_path,
            item.sha256,
            item.mime_type,
            item.byte_size,
            item.created_at,
        )
        == requested
    )
    if len(artifacts) == 1 and exact:
        return exact[0]
    raise ArtifactRepositoryError(
        code="ARTIFACT_CONFLICT",
        message="Artifact identity conflicts with an existing registration.",
        details={
            "storage_scope": storage_scope,
            "relative_path": relative_path,
            "sha256": sha256,
            "kind": kind,
            "existing_artifact_ids": tuple(item.id for item in artifacts),
        },
    )


def register_artifact(
    connection: sqlite3.Connection,
    *,
    kind: str,
    storage_scope: str,
    retention_class: str,
    relative_path: str,
    sha256: str,
    mime_type: str,
    byte_size: int,
    created_at: str,
) -> Artifact:
    """Register an artifact, returning an exact existing registration idempotently."""

    requested = (
        kind,
        storage_scope,
        retention_class,
        relative_path,
        sha256,
        mime_type,
        byte_size,
        created_at,
    )
    existing = _resolve_artifact_identity(connection, requested)
    if existing is not None:
        return existing
    caller_owns_transaction = connection.in_transaction
    try:
        cursor = connection.execute(
            """INSERT INTO artifacts
               (kind, storage_scope, retention_class, relative_path, sha256, mime_type,
                byte_size, created_at, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*requested, _now()),
        )
    except sqlite3.IntegrityError:
        if not caller_owns_transaction:
            connection.rollback()
        recovered = _resolve_artifact_identity(connection, requested)
        if recovered is None:
            raise
        return recovered
    artifact_id = cursor.lastrowid
    assert artifact_id is not None
    artifact = get_artifact(connection, artifact_id)
    assert artifact is not None
    return artifact
