"""Explicit SQLite persistence for immutable document model versions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from reference_engine.errors import DocumentModelRepositoryError
from reference_engine.model import LoadedDocumentModel, canonicalize_document_model
from reference_engine.model.normalization import compute_definition_sha256


@dataclass(frozen=True)
class DocumentModel:
    id: int
    model_key: str
    title: str
    document_type: str
    record_type: str
    created_at: str


@dataclass(frozen=True)
class ModelQueryDefinition:
    """Typed snapshot whose JSON definition mapping is mutable by callers."""

    id: int
    model_version_id: int
    query_name: str
    description: str
    definition: Mapping[str, object]
    definition_json: str
    definition_sha256: str


@dataclass(frozen=True)
class DocumentModelVersion:
    """Typed snapshot whose JSON definition mapping is mutable by callers."""

    id: int
    document_model_id: int
    model_key: str
    semantic_version: str
    schema_version: int
    status: str
    engine_compatibility: str
    artifact_id: int
    definition: Mapping[str, object]
    definition_json: str
    definition_sha256: str
    loaded_at: str
    queries: tuple[ModelQueryDefinition, ...]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TypeError("validated normalized model contains a non-string mapping")
    return cast(Mapping[str, object], value)


def _model_values(loaded: LoadedDocumentModel) -> tuple[str, str, str, str]:
    model = _mapping(loaded.normalized_data["model"])
    return cast(
        tuple[str, str, str, str],
        (model["id"], model["title"], model["document_type"], model["record_type"]),
    )


def _document_model(row: sqlite3.Row) -> DocumentModel:
    return DocumentModel(
        row["id"],
        row["model_key"],
        row["title"],
        row["document_type"],
        row["record_type"],
        row["created_at"],
    )


def get_document_model(
    connection: sqlite3.Connection, model_key: str
) -> DocumentModel | None:
    row = connection.execute(
        """SELECT id, model_key, title, document_type, record_type, created_at
           FROM document_models WHERE model_key = ?""",
        (model_key,),
    ).fetchone()
    return None if row is None else _document_model(row)


def _resolve_model_identity(
    connection: sqlite3.Connection,
    model_key: str,
    title: str,
    document_type: str,
    record_type: str,
) -> DocumentModel | None:
    existing = get_document_model(connection, model_key)
    if existing is None:
        return None
    if (existing.title, existing.document_type, existing.record_type) == (
        title,
        document_type,
        record_type,
    ):
        return existing
    raise DocumentModelRepositoryError(
        "MODEL_IDENTITY_CONFLICT",
        "The model key is already registered with different stable metadata.",
        details={
            "model_key": model_key,
            "existing": {
                "title": existing.title,
                "document_type": existing.document_type,
                "record_type": existing.record_type,
            },
            "requested": {
                "title": title,
                "document_type": document_type,
                "record_type": record_type,
            },
        },
    )


def register_document_model(
    connection: sqlite3.Connection, loaded: LoadedDocumentModel
) -> DocumentModel:
    """Register only the stable model identity from a validated loaded model."""

    model_key, title, document_type, record_type = _model_values(loaded)
    existing = _resolve_model_identity(
        connection, model_key, title, document_type, record_type
    )
    if existing is not None:
        return existing
    caller_owns_transaction = connection.in_transaction
    try:
        cursor = connection.execute(
            """INSERT INTO document_models
               (model_key, title, document_type, record_type, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (model_key, title, document_type, record_type, _now()),
        )
    except sqlite3.IntegrityError:
        if not caller_owns_transaction:
            connection.rollback()
        recovered = _resolve_model_identity(
            connection, model_key, title, document_type, record_type
        )
        if recovered is None:
            raise
        return recovered
    model = get_document_model(connection, model_key)
    assert model is not None and model.id == cursor.lastrowid
    return model


def _queries(
    connection: sqlite3.Connection, version_id: int
) -> tuple[ModelQueryDefinition, ...]:
    rows = connection.execute(
        """SELECT id, model_version_id, query_name, description, definition_json,
                  definition_sha256
           FROM model_query_definitions WHERE model_version_id = ?
           ORDER BY query_name""",
        (version_id,),
    )
    return tuple(
        ModelQueryDefinition(
            row["id"],
            row["model_version_id"],
            row["query_name"],
            row["description"],
            _mapping(json.loads(row["definition_json"])),
            row["definition_json"],
            row["definition_sha256"],
        )
        for row in rows
    )


def _version(connection: sqlite3.Connection, row: sqlite3.Row) -> DocumentModelVersion:
    return DocumentModelVersion(
        row["id"],
        row["document_model_id"],
        row["model_key"],
        row["semantic_version"],
        row["schema_version"],
        row["status"],
        row["engine_compatibility"],
        row["artifact_id"],
        _mapping(json.loads(row["definition_json"])),
        row["definition_json"],
        row["definition_sha256"],
        row["loaded_at"],
        _queries(connection, row["id"]),
    )


def get_document_model_version(
    connection: sqlite3.Connection, model_key: str, semantic_version: str
) -> DocumentModelVersion | None:
    """Return one exact model version, or ``None`` when model/version is unknown."""

    row = connection.execute(
        """SELECT v.id, v.document_model_id, m.model_key, v.semantic_version,
                  v.schema_version, v.status, v.engine_compatibility, v.artifact_id,
                  v.definition_json, v.definition_sha256, v.loaded_at
           FROM document_model_versions AS v
           JOIN document_models AS m ON m.id = v.document_model_id
           WHERE m.model_key = ? AND v.semantic_version = ?""",
        (model_key, semantic_version),
    ).fetchone()
    return None if row is None else _version(connection, row)


def get_active_document_model_versions(
    connection: sqlite3.Connection,
) -> tuple[DocumentModelVersion, ...]:
    """Return the active recognition candidates in recognition-v1 order."""

    rows = connection.execute(
        """SELECT v.id, v.document_model_id, m.model_key, v.semantic_version,
                  v.schema_version, v.status, v.engine_compatibility, v.artifact_id,
                  v.definition_json, v.definition_sha256, v.loaded_at
           FROM document_model_versions AS v
           JOIN document_models AS m ON m.id = v.document_model_id
           WHERE v.status = 'active'
           ORDER BY m.model_key, v.semantic_version, v.id"""
    )
    return tuple(_version(connection, row) for row in rows)


def _resolve_model_version_identity(
    connection: sqlite3.Connection,
    model_key: str,
    semantic_version: str,
    definition_sha256: str,
) -> DocumentModelVersion | None:
    existing = get_document_model_version(connection, model_key, semantic_version)
    if existing is None:
        return None
    if existing.definition_sha256 == definition_sha256:
        return existing
    raise DocumentModelRepositoryError(
        "MODEL_VERSION_CONFLICT",
        "The semantic version is already registered with a different definition.",
        details={
            "model_key": model_key,
            "semantic_version": semantic_version,
            "existing_definition_hash": existing.definition_sha256,
            "requested_definition_hash": definition_sha256,
        },
    )


def _insert_queries(
    connection: sqlite3.Connection, version_id: int, loaded: LoadedDocumentModel
) -> None:
    queries = cast(list[object], loaded.normalized_data["queries"])
    for value in queries:
        query = _mapping(value)
        definition_json = canonicalize_document_model(query)
        connection.execute(
            """INSERT INTO model_query_definitions
               (model_version_id, query_name, description, definition_json,
                definition_sha256)
               VALUES (?, ?, ?, ?, ?)""",
            (
                version_id,
                query["name"],
                query.get("description", ""),
                definition_json,
                compute_definition_sha256(query),
            ),
        )


def register_document_model_version(
    connection: sqlite3.Connection, loaded: LoadedDocumentModel, *, artifact_id: int
) -> DocumentModelVersion:
    """Atomically register a model identity, immutable version, and its queries.

    A savepoint is used when the caller already owns a transaction; otherwise this
    function owns and commits an immediate transaction.
    """

    model_data = _mapping(loaded.normalized_data["model"])
    model_key = cast(str, model_data["id"])
    semantic_version = cast(str, model_data["version"])
    existing = _resolve_model_version_identity(
        connection, model_key, semantic_version, loaded.definition_sha256
    )
    if existing is not None:
        return existing

    nested = connection.in_transaction
    try:
        connection.execute(
            "SAVEPOINT register_model_version" if nested else "BEGIN IMMEDIATE"
        )
        model = register_document_model(connection, loaded)
        try:
            cursor = connection.execute(
                """INSERT INTO document_model_versions
                   (document_model_id, semantic_version, schema_version, status,
                    engine_compatibility, artifact_id, definition_json,
                    definition_sha256, loaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model.id,
                    semantic_version,
                    loaded.normalized_data["schema_version"],
                    model_data["status"],
                    model_data["engine_compatibility"],
                    artifact_id,
                    loaded.canonical_json,
                    loaded.definition_sha256,
                    _now(),
                ),
            )
        except sqlite3.IntegrityError:
            recovered = _resolve_model_version_identity(
                connection,
                model_key,
                semantic_version,
                loaded.definition_sha256,
            )
            if recovered is None:
                raise
        else:
            version_id = cursor.lastrowid
            assert version_id is not None
            _insert_queries(connection, version_id, loaded)
        if nested:
            connection.execute("RELEASE SAVEPOINT register_model_version")
        else:
            connection.commit()
    except Exception:
        if nested:
            connection.execute("ROLLBACK TO SAVEPOINT register_model_version")
            connection.execute("RELEASE SAVEPOINT register_model_version")
        else:
            connection.rollback()
        raise
    result = get_document_model_version(connection, model_key, semantic_version)
    assert result is not None
    return result
