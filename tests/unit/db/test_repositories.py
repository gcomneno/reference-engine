"""Focused persistence tests for artifacts and immutable model versions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from reference_engine.db import (
    Artifact,
    apply_migrations,
    connect_database,
    get_active_document_model_versions,
    get_artifact,
    get_artifact_by_content,
    get_artifact_by_storage,
    get_document_model,
    get_document_model_version,
    register_artifact,
    register_document_model,
    register_document_model_version,
)
from reference_engine.db import artifacts as artifact_repository
from reference_engine.db import document_models as model_repository
from reference_engine.errors import (
    ArtifactRepositoryError,
    DocumentModelRepositoryError,
)
from reference_engine.model import LoadedDocumentModel, load_document_model
from reference_engine.model.normalization import (
    canonicalize_document_model,
    compute_definition_sha256,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "models"
HASH = "a" * 64
CREATED = "2026-01-01T00:00:00Z"


@pytest.fixture
def database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "repository.sqlite3")
    apply_migrations(connection)
    return connection


@pytest.fixture
def concurrent_database(
    tmp_path: Path,
) -> Iterator[tuple[sqlite3.Connection, sqlite3.Connection]]:
    path = tmp_path / "concurrent-repository.sqlite3"
    initializer = connect_database(path)
    apply_migrations(initializer)
    initializer.close()
    primary = connect_database(path)
    competitor = connect_database(path)
    try:
        yield primary, competitor
    finally:
        primary.close()
        competitor.close()


def artifact(connection: sqlite3.Connection, **changes: object) -> Artifact:
    values: dict[str, object] = {
        "kind": "document_model",
        "storage_scope": "vault",
        "retention_class": "canonical",
        "relative_path": "models/sample.yaml",
        "sha256": HASH,
        "mime_type": "application/yaml",
        "byte_size": 123,
        "created_at": CREATED,
    }
    values.update(changes)
    return register_artifact(
        connection,
        kind=cast(str, values["kind"]),
        storage_scope=cast(str, values["storage_scope"]),
        retention_class=cast(str, values["retention_class"]),
        relative_path=cast(str, values["relative_path"]),
        sha256=cast(str, values["sha256"]),
        mime_type=cast(str, values["mime_type"]),
        byte_size=cast(int, values["byte_size"]),
        created_at=cast(str, values["created_at"]),
    )


def loaded(name: str = "valid-minimal.yaml") -> LoadedDocumentModel:
    return load_document_model(FIXTURES / name)


def version_artifact(connection: sqlite3.Connection, suffix: str = "one") -> int:
    result = artifact(
        connection,
        relative_path=f"models/{suffix}.yaml",
        sha256=compute_definition_sha256({"suffix": suffix}),
    )
    return result.id


def changed_loaded(
    source: LoadedDocumentModel, **model_changes: object
) -> LoadedDocumentModel:
    normalized = deepcopy(source.normalized_data)
    model = cast(dict[str, object], normalized["model"])
    model.update(model_changes)
    canonical = canonicalize_document_model(normalized)
    return LoadedDocumentModel(
        source.source_path,
        normalized,
        normalized,
        canonical,
        compute_definition_sha256(normalized),
    )


def test_artifact_insert_lookup_and_idempotency(database: sqlite3.Connection) -> None:
    first = artifact(database)
    second = artifact(database)

    assert first == second
    assert get_artifact(database, first.id) == first
    assert get_artifact_by_content(database, HASH, "document_model") == first
    assert get_artifact_by_storage(database, "vault", "models/sample.yaml") == first
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    assert get_artifact(database, 999) is None
    assert get_artifact_by_content(database, "f" * 64, "document_model") is None
    assert get_artifact_by_storage(database, "vault", "models/unknown.yaml") is None


@pytest.mark.parametrize(
    ("change", "value"),
    [("mime_type", "text/plain"), ("relative_path", "models/moved.yaml")],
)
def test_artifact_identity_conflict_is_structured(
    database: sqlite3.Connection, change: str, value: str
) -> None:
    artifact(database)
    with pytest.raises(ArtifactRepositoryError) as caught:
        artifact(database, **{change: value})
    assert caught.value.code == "ARTIFACT_CONFLICT"
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1


def test_artifact_schema_constraints_remain_visible(
    database: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        artifact(database, storage_scope="elsewhere")


def test_model_identity_insertion_and_repeat(database: sqlite3.Connection) -> None:
    model = loaded()
    first = register_document_model(database, model)
    second = register_document_model(database, model)
    assert first == second
    assert get_document_model(database, first.model_key) == first
    assert get_document_model(database, "unknown") is None
    assert database.execute("SELECT COUNT(*) FROM document_models").fetchone()[0] == 1
    assert (
        database.execute("SELECT COUNT(*) FROM document_model_versions").fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Changed title"),
        ("document_type", "changed_document"),
        ("record_type", "changed_record"),
    ],
)
def test_model_identity_conflict_preserves_stable_metadata(
    database: sqlite3.Connection, field: str, value: str
) -> None:
    original = loaded()
    existing = register_document_model(database, original)
    requested = changed_loaded(original, **{field: value})

    with pytest.raises(DocumentModelRepositoryError) as caught:
        register_document_model(database, requested)

    assert caught.value.code == "MODEL_IDENTITY_CONFLICT"
    assert caught.value.details == {
        "model_key": "sample.basic-record",
        "existing": {
            "title": existing.title,
            "document_type": existing.document_type,
            "record_type": existing.record_type,
        },
        "requested": {
            "title": value if field == "title" else existing.title,
            "document_type": (
                value if field == "document_type" else existing.document_type
            ),
            "record_type": value if field == "record_type" else existing.record_type,
        },
    }
    assert get_document_model(database, existing.model_key) == existing


def test_first_version_persists_canonical_definition_and_queries(
    database: sqlite3.Connection,
) -> None:
    model = loaded("valid-complete.yaml")
    result = register_document_model_version(
        database, model, artifact_id=version_artifact(database)
    )
    persisted = get_document_model_version(database, result.model_key, "2.1.0-beta.1")

    assert persisted == result
    assert result.definition_json == model.canonical_json
    assert result.definition_sha256 == model.definition_sha256
    assert [query.query_name for query in result.queries] == ["by_date", "current_day"]
    assert all(query.model_version_id == result.id for query in result.queries)
    assert (
        database.execute(
            "SELECT definition_json FROM document_model_versions WHERE id = ?",
            (result.id,),
        ).fetchone()[0]
        == model.canonical_json
    )


def test_same_version_hash_is_idempotent_without_query_duplication(
    database: sqlite3.Connection,
) -> None:
    model = loaded()
    artifact_id = version_artifact(database)
    first = register_document_model_version(database, model, artifact_id=artifact_id)
    second = register_document_model_version(database, model, artifact_id=artifact_id)
    assert second == first
    assert (
        database.execute("SELECT COUNT(*) FROM document_model_versions").fetchone()[0]
        == 1
    )
    assert (
        database.execute("SELECT COUNT(*) FROM model_query_definitions").fetchone()[0]
        == 1
    )


def test_same_version_different_hash_is_structured_conflict(
    database: sqlite3.Connection,
) -> None:
    first = loaded()
    register_document_model_version(
        database, first, artifact_id=version_artifact(database)
    )
    requested = changed_loaded(first, title="Changed")
    with pytest.raises(DocumentModelRepositoryError) as caught:
        register_document_model_version(
            database, requested, artifact_id=version_artifact(database, "two")
        )
    assert caught.value.code == "MODEL_VERSION_CONFLICT"
    assert caught.value.details == {
        "model_key": "sample.basic-record",
        "semantic_version": "1.0.0",
        "existing_definition_hash": first.definition_sha256,
        "requested_definition_hash": requested.definition_sha256,
    }


def test_multiple_versions_remain_separate_and_unchanged(
    database: sqlite3.Connection,
) -> None:
    first_model = loaded()
    first = register_document_model_version(
        database, first_model, artifact_id=version_artifact(database)
    )
    second_model = changed_loaded(first_model, version="1.1.0")
    second = register_document_model_version(
        database, second_model, artifact_id=version_artifact(database, "two")
    )
    assert first.id != second.id
    assert first.definition_json == first_model.canonical_json
    assert second.definition_json == second_model.canonical_json
    assert first.queries[0].model_version_id == first.id
    assert second.queries[0].model_version_id == second.id
    assert get_document_model_version(database, first.model_key, "9.9.9") is None
    assert get_document_model_version(database, "unknown", "1.0.0") is None


def test_new_version_with_conflicting_identity_rolls_back_everything(
    database: sqlite3.Connection,
) -> None:
    original = loaded()
    first = register_document_model_version(
        database, original, artifact_id=version_artifact(database)
    )
    existing_identity = get_document_model(database, first.model_key)
    requested = changed_loaded(original, version="1.1.0", record_type="changed")

    with pytest.raises(DocumentModelRepositoryError) as caught:
        register_document_model_version(
            database, requested, artifact_id=version_artifact(database, "two")
        )

    assert caught.value.code == "MODEL_IDENTITY_CONFLICT"
    assert get_document_model(database, first.model_key) == existing_identity
    assert (
        database.execute("SELECT COUNT(*) FROM document_model_versions").fetchone()[0]
        == 1
    )
    assert (
        database.execute("SELECT COUNT(*) FROM model_query_definitions").fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("conflicting", [False, True])
def test_artifact_post_insert_unique_conflict_is_resolved(
    concurrent_database: tuple[sqlite3.Connection, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
    conflicting: bool,
) -> None:
    primary, competitor = concurrent_database
    original = artifact_repository._resolve_artifact_identity
    calls = 0

    def race(
        connection: sqlite3.Connection,
        requested: tuple[str, str, str, str, str, str, int, str],
    ) -> Artifact | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            persisted = list(requested)
            if conflicting:
                persisted[5] = "text/plain"
            competitor.execute(
                """INSERT INTO artifacts
                   (kind, storage_scope, retention_class, relative_path, sha256,
                    mime_type, byte_size, created_at, registered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*persisted, CREATED),
            )
            competitor.commit()
            return None
        return original(connection, requested)

    monkeypatch.setattr(artifact_repository, "_resolve_artifact_identity", race)
    if conflicting:
        with pytest.raises(ArtifactRepositoryError, match="ARTIFACT_CONFLICT"):
            artifact(primary)
    else:
        assert artifact(primary).mime_type == "application/yaml"
    assert primary.in_transaction is False


@pytest.mark.parametrize("conflicting", [False, True])
def test_model_identity_post_insert_unique_conflict_is_resolved(
    concurrent_database: tuple[sqlite3.Connection, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
    conflicting: bool,
) -> None:
    primary, competitor = concurrent_database
    original = model_repository._resolve_model_identity
    calls = 0

    def race(
        connection: sqlite3.Connection,
        model_key: str,
        title: str,
        document_type: str,
        record_type: str,
    ) -> model_repository.DocumentModel | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            competitor.execute(
                """INSERT INTO document_models
                   (model_key, title, document_type, record_type, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    model_key,
                    "Conflicting" if conflicting else title,
                    document_type,
                    record_type,
                    CREATED,
                ),
            )
            competitor.commit()
            return None
        return original(connection, model_key, title, document_type, record_type)

    monkeypatch.setattr(model_repository, "_resolve_model_identity", race)
    if conflicting:
        with pytest.raises(
            DocumentModelRepositoryError, match="MODEL_IDENTITY_CONFLICT"
        ):
            register_document_model(primary, loaded())
    else:
        assert register_document_model(primary, loaded()).title == "Basic record model"
    assert primary.in_transaction is False


@pytest.mark.parametrize("conflicting", [False, True])
def test_model_version_post_insert_unique_conflict_is_resolved(
    concurrent_database: tuple[sqlite3.Connection, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
    conflicting: bool,
) -> None:
    primary, competitor = concurrent_database
    requested = loaded("valid-complete.yaml")
    artifact_id = version_artifact(primary)
    primary.commit()
    assert primary.in_transaction is False
    original = model_repository._resolve_model_version_identity
    calls = 0

    def race(
        connection: sqlite3.Connection,
        model_key: str,
        semantic_version: str,
        definition_sha256: str,
    ) -> model_repository.DocumentModelVersion | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            assert connection is primary
            assert primary.in_transaction is False
            identity = register_document_model(competitor, requested)
            cursor = competitor.execute(
                """INSERT INTO document_model_versions
                   (document_model_id, semantic_version, schema_version, status,
                    engine_compatibility, artifact_id, definition_json,
                    definition_sha256, loaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    identity.id,
                    semantic_version,
                    1,
                    "active",
                    ">=0.1,<1.0",
                    artifact_id,
                    requested.canonical_json,
                    "b" * 64 if conflicting else definition_sha256,
                    CREATED,
                ),
            )
            version_id = cursor.lastrowid
            assert version_id is not None
            model_repository._insert_queries(competitor, version_id, requested)
            competitor.commit()
            return None
        return original(connection, model_key, semantic_version, definition_sha256)

    monkeypatch.setattr(model_repository, "_resolve_model_version_identity", race)
    if conflicting:
        with pytest.raises(DocumentModelRepositoryError) as caught:
            register_document_model_version(primary, requested, artifact_id=artifact_id)
        assert caught.value.code == "MODEL_VERSION_CONFLICT"
        details = caught.value.details
        assert details is not None
        assert details["existing_definition_hash"] == "b" * 64
        assert details["requested_definition_hash"] == requested.definition_sha256
    else:
        result = register_document_model_version(
            primary, requested, artifact_id=artifact_id
        )
        assert result.definition_sha256 == requested.definition_sha256
        assert len(result.queries) == len(
            cast(list[object], requested.normalized_data["queries"])
        )

    assert primary.in_transaction is False
    assert (
        primary.execute("SELECT COUNT(*) FROM document_model_versions").fetchone()[0]
        == 1
    )
    assert primary.execute("SELECT COUNT(*) FROM model_query_definitions").fetchone()[
        0
    ] == len(cast(list[object], requested.normalized_data["queries"]))


def test_version_artifact_unique_failure_is_not_mislabeled(
    database: sqlite3.Connection,
) -> None:
    model = loaded()
    artifact_id = version_artifact(database)
    register_document_model_version(database, model, artifact_id=artifact_id)
    requested = changed_loaded(model, id="sample.other", version="2.0.0")

    with pytest.raises(sqlite3.IntegrityError):
        register_document_model_version(database, requested, artifact_id=artifact_id)

    assert get_document_model(database, "sample.other") is None


def test_active_model_versions_filter_and_use_lexical_order(
    database: sqlite3.Connection,
) -> None:
    source = loaded()
    for index, (key, version, status) in enumerate(
        (
            ("model.z", "1.0.0", "active"),
            ("model.a", "3.0.0", "disabled"),
            ("model.a", "10.0.0", "active"),
            ("model.a", "2.0.0", "active"),
            ("model.q", "1.0.0", "deprecated"),
        ),
        1,
    ):
        item = changed_loaded(source, id=key, version=version, status=status)
        register_document_model_version(
            database, item, artifact_id=version_artifact(database, str(index))
        )
    database.commit()
    active = get_active_document_model_versions(database)
    assert [(item.model_key, item.semantic_version) for item in active] == [
        ("model.a", "10.0.0"),
        ("model.a", "2.0.0"),
        ("model.z", "1.0.0"),
    ]
    database.execute("UPDATE document_model_versions SET status = 'disabled'")
    assert get_active_document_model_versions(database) == ()


@pytest.mark.parametrize("preexisting_identity", [False, True])
def test_query_failure_rolls_back_complete_registration(
    database: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    preexisting_identity: bool,
) -> None:
    model = loaded()
    if preexisting_identity:
        register_document_model(database, model)
        database.commit()
    artifact_id = version_artifact(database)
    database.commit()

    def fail_query(*args: object, **kwargs: object) -> None:
        raise sqlite3.IntegrityError("forced query failure")

    monkeypatch.setattr(
        "reference_engine.db.document_models._insert_queries", fail_query
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced query failure"):
        register_document_model_version(database, model, artifact_id=artifact_id)

    assert (
        database.execute("SELECT COUNT(*) FROM document_model_versions").fetchone()[0]
        == 0
    )
    assert (
        database.execute("SELECT COUNT(*) FROM model_query_definitions").fetchone()[0]
        == 0
    )
    expected_models = 1 if preexisting_identity else 0
    assert (
        database.execute("SELECT COUNT(*) FROM document_models").fetchone()[0]
        == expected_models
    )
    assert database.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_registration_respects_caller_transaction(database: sqlite3.Connection) -> None:
    model = loaded()
    artifact_id = version_artifact(database)
    database.commit()
    database.execute("BEGIN")
    register_document_model_version(database, model, artifact_id=artifact_id)
    assert database.in_transaction
    database.rollback()
    assert get_document_model(database, "sample.basic-record") is None
    assert (
        database.execute("SELECT COUNT(*) FROM document_model_versions").fetchone()[0]
        == 0
    )


def test_model_version_requires_existing_artifact_foreign_key(
    database: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        register_document_model_version(database, loaded(), artifact_id=999)
    assert get_document_model(database, "sample.basic-record") is None
