"""Focused SQLite persistence for registered documents."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from reference_engine.errors import DocumentRepositoryError


@dataclass(frozen=True)
class Document:
    """The relational projection of one canonical source document."""

    id: int
    source_artifact_id: int
    content_sha256: str
    original_filename: str
    source_url: str | None
    retrieved_at: str | None
    published_date: str | None
    page_count: int | None
    registered_at: str


def _document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        source_artifact_id=row["source_artifact_id"],
        content_sha256=row["content_sha256"],
        original_filename=row["original_filename"],
        source_url=row["source_url"],
        retrieved_at=row["retrieved_at"],
        published_date=row["published_date"],
        page_count=row["page_count"],
        registered_at=row["registered_at"],
    )


_SELECT = """SELECT id, source_artifact_id, content_sha256, original_filename,
                    source_url, retrieved_at, published_date, page_count, registered_at
             FROM documents"""


def get_document(connection: sqlite3.Connection, document_id: int) -> Document | None:
    """Return a document by ID, or ``None`` when it is unknown."""

    row = connection.execute(f"{_SELECT} WHERE id = ?", (document_id,)).fetchone()
    return None if row is None else _document(row)


def get_document_by_sha256(
    connection: sqlite3.Connection, content_sha256: str
) -> Document | None:
    """Return the unique document with a content hash, or ``None``."""

    row = connection.execute(
        f"{_SELECT} WHERE content_sha256 = ?", (content_sha256,)
    ).fetchone()
    return None if row is None else _document(row)


def _resolve_document_identity(
    connection: sqlite3.Connection,
    *,
    source_artifact_id: int,
    content_sha256: str,
    original_filename: str,
    source_url: str | None,
    retrieved_at: str | None,
    registered_at: str,
) -> Document | None:
    rows = connection.execute(
        f"{_SELECT} WHERE content_sha256 = ? OR source_artifact_id = ? ORDER BY id",
        (content_sha256, source_artifact_id),
    ).fetchall()
    if not rows:
        return None
    documents = tuple(_document(row) for row in rows)
    exact = tuple(
        item
        for item in documents
        if (
            item.source_artifact_id,
            item.content_sha256,
            item.original_filename,
            item.source_url,
            item.retrieved_at,
            item.registered_at,
        )
        == (
            source_artifact_id,
            content_sha256,
            original_filename,
            source_url,
            retrieved_at,
            registered_at,
        )
    )
    if len(documents) == 1 and exact:
        return exact[0]
    raise DocumentRepositoryError(
        code="DOCUMENT_REGISTRATION_CONFLICT",
        message="Document identity conflicts with an existing registration.",
        details={
            "content_sha256": content_sha256,
            "source_artifact_id": source_artifact_id,
            "existing_document_ids": tuple(item.id for item in documents),
        },
    )


def register_document(
    connection: sqlite3.Connection,
    *,
    source_artifact_id: int,
    content_sha256: str,
    original_filename: str,
    source_url: str | None,
    retrieved_at: str | None,
    registered_at: str,
) -> Document:
    """Insert a document or return an exact existing registration."""

    existing = _resolve_document_identity(
        connection,
        source_artifact_id=source_artifact_id,
        content_sha256=content_sha256,
        original_filename=original_filename,
        source_url=source_url,
        retrieved_at=retrieved_at,
        registered_at=registered_at,
    )
    if existing is not None:
        return existing
    caller_owns_transaction = connection.in_transaction
    try:
        cursor = connection.execute(
            """INSERT INTO documents
               (source_artifact_id, content_sha256, original_filename, source_url,
                retrieved_at, published_date, page_count, registered_at)
               VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (
                source_artifact_id,
                content_sha256,
                original_filename,
                source_url,
                retrieved_at,
                registered_at,
            ),
        )
    except sqlite3.IntegrityError as error:
        if not caller_owns_transaction:
            connection.rollback()
        if error.sqlite_errorcode != sqlite3.SQLITE_CONSTRAINT_UNIQUE:
            raise
        recovered = _resolve_document_identity(
            connection,
            source_artifact_id=source_artifact_id,
            content_sha256=content_sha256,
            original_filename=original_filename,
            source_url=source_url,
            retrieved_at=retrieved_at,
            registered_at=registered_at,
        )
        if recovered is None:
            raise
        return recovered
    document_id = cursor.lastrowid
    assert document_id is not None
    result = get_document(connection, document_id)
    assert result is not None
    return result
