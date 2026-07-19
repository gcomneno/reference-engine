# ruff: noqa: E501
"""Integrity and behavior tests for the generic SQLite v1 projection."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from reference_engine.db import (
    apply_migrations,
    connect_database,
    get_applied_migrations,
)
from reference_engine.db.migrations import (
    MigrationDefinition,
    _apply_migration_definitions,
)
from reference_engine.errors import MigrationError

HASH = "a" * 64
NOW = "2026-01-01T00:00:00Z"
TABLES = {
    "schema_migrations",
    "artifacts",
    "document_models",
    "document_model_versions",
    "model_query_definitions",
    "documents",
    "recognition_runs",
    "recognition_results",
    "document_bindings",
    "extraction_runs",
    "datasets",
    "dataset_versions",
    "records",
    "record_field_values",
    "record_provenance",
    "validation_runs",
    "validation_findings",
    "dataset_publications",
}
VIEWS = {
    "latest_validation_decisions",
    "active_dataset_versions",
    "queryable_records",
    "queryable_record_fields",
}
TRIGGERS = {
    "recognition_runs_snapshot_insert",
    "recognition_runs_snapshot_update",
    "dataset_publications_no_delete",
    "dataset_publications_no_update",
    "dataset_publications_sequence_increasing",
}
INDEXES = {
    "artifacts_sha256_idx",
    "model_versions_definition_sha256_idx",
    "recognition_runs_document_idx",
    "recognition_results_model_idx",
    "bindings_document_idx",
    "bindings_model_version_idx",
    "extraction_runs_binding_idx",
    "extraction_runs_fingerprint_idx",
    "dataset_versions_dataset_sequence_idx",
    "records_natural_key_idx",
    "record_fields_date_idx",
    "record_fields_text_idx",
    "record_fields_integer_idx",
    "provenance_record_idx",
    "validation_runs_version_sequence_idx",
    "validation_findings_run_idx",
    "publications_dataset_sequence_idx",
}


@pytest.fixture
def database() -> sqlite3.Connection:
    connection = connect_database(":memory:")
    apply_migrations(connection)
    return connection


def _artifact(connection: sqlite3.Connection, artifact_id: int, kind: str) -> None:
    connection.execute(
        """INSERT INTO artifacts
           (id, kind, storage_scope, retention_class, relative_path, sha256,
            mime_type, byte_size, created_at, registered_at)
           VALUES (?, ?, 'vault', 'canonical', ?, ?, 'application/json', 1, ?, ?)""",
        (artifact_id, kind, f"artifact-{artifact_id}", f"{artifact_id:064x}", NOW, NOW),
    )


def _base(connection: sqlite3.Connection) -> None:
    for artifact_id, kind in ((1, "model"), (2, "source")):
        _artifact(connection, artifact_id, kind)
    connection.execute(
        "INSERT INTO document_models VALUES (1, 'model', 'Model', 'document', 'record', ?)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO document_model_versions VALUES (1, 1, '1.0.0', 1, 'active', '>=0.1', 1, '{}', ?, ?)",
        (HASH, NOW),
    )
    connection.execute(
        "INSERT INTO documents VALUES (1, 2, ?, 'source.pdf', NULL, NULL, NULL, 1, ?)",
        ("b" * 64, NOW),
    )
    connection.execute(
        "INSERT INTO document_bindings VALUES (1, 1, 1, NULL, 'explicit_cli', '{}', ?, NULL, ?)",
        (HASH, NOW),
    )
    connection.execute("INSERT INTO datasets VALUES (1, 1, 'record', ?)", (NOW,))


def _version(connection: sqlite3.Connection, version: int, sequence: int) -> None:
    artifact_id = 100 + version
    _artifact(connection, artifact_id, "dataset")
    connection.execute(
        "INSERT INTO dataset_versions VALUES (?, 1, ?, 'manual', NULL, NULL, ?, 1, ?)",
        (version, sequence, artifact_id, NOW),
    )
    connection.execute(
        "INSERT INTO records VALUES (?, ?, 'record', 0, '{}', ?, '{}', ?, NULL, NULL, NULL, ?)",
        (version, version, f"{version:064x}", f"{version + 20:064x}", NOW),
    )
    connection.execute(
        "INSERT INTO record_field_values(record_id, field_path, value_index, value_type, integer_value) VALUES (?, '/value', 0, 'integer', ?)",
        (version, version),
    )


def _validation(
    connection: sqlite3.Connection, run_id: int, version: int, sequence: int, decision: str
) -> None:
    connection.execute(
        "INSERT INTO validation_runs VALUES (?, ?, ?, 'human', NULL, ?, ?, ?, NULL, NULL)",
        (run_id, version, sequence, NOW, NOW, decision),
    )


def _publication(
    connection: sqlite3.Connection,
    publication_id: int,
    version: int,
    sequence: int,
    kind: str = "publish",
    supersedes: int | None = None,
) -> None:
    artifact_id = 200 + publication_id
    _artifact(connection, artifact_id, "publication")
    connection.execute(
        "INSERT INTO dataset_publications VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
        (publication_id, version, sequence, kind, artifact_id, supersedes, NOW),
    )


def _active(connection: sqlite3.Connection) -> list[int]:
    return [row[0] for row in connection.execute("SELECT id FROM active_dataset_versions")]


@pytest.mark.parametrize(
    "name",
    [
        "001_initial_schema.sql",
        "002_recognition_run_snapshots.sql",
    ],
)
def test_migration_mirror_is_byte_identical(name: str) -> None:
    root = Path(__file__).parents[3]
    authoritative = (root / "migrations" / name).read_bytes()
    packaged = (
        root
        / "src/reference_engine/resources/migrations"
        / name
    ).read_bytes()

    assert packaged == authoritative


def test_packaged_migration_report_metadata_and_idempotency(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "db.sqlite3")
    first = apply_migrations(connection)
    second = apply_migrations(connection)

    root = Path(__file__).parents[3]
    migration_names = [
        "001_initial_schema.sql",
        "002_recognition_run_snapshots.sql",
    ]
    expected_hashes = [
        hashlib.sha256(
            (root / "migrations" / name).read_bytes()
        ).hexdigest()
        for name in migration_names
    ]

    assert [item.version for item in first.applied] == [1, 2]
    assert first.skipped == ()
    assert second.applied == ()
    assert [item.version for item in second.skipped] == [1, 2]
    assert get_applied_migrations(connection) == first.applied
    assert [item.name for item in first.applied] == migration_names
    assert [item.sha256 for item in first.applied] == expected_hashes


def _authoritative_migration(
    version: int,
    name: str,
) -> MigrationDefinition:
    root = Path(__file__).parents[3]
    return MigrationDefinition(
        version,
        name,
        (root / "migrations" / name).read_bytes(),
    )


def _seed_recognition_document(
    connection: sqlite3.Connection,
) -> None:
    _artifact(connection, 1, "source_document")
    connection.execute(
        """INSERT INTO documents
           (id, source_artifact_id, content_sha256,
            original_filename, source_url, retrieved_at,
            published_date, page_count, registered_at)
           VALUES (
               1, 1, ?, 'source.pdf',
               NULL, NULL, NULL, NULL, ?
           )""",
        (HASH, NOW),
    )


def _insert_recognition_run(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    outcome: str,
    snapshot_json: str | None,
    snapshot_sha256: str | None,
) -> None:
    connection.execute(
        """INSERT INTO recognition_runs
           (id, document_id, engine_version,
            started_at, completed_at, outcome,
            error_code, error_message,
            input_snapshot_json,
            input_snapshot_sha256)
           VALUES (
               ?, 1, 'test-engine',
               ?, ?, ?,
               NULL, NULL, ?, ?
           )""",
        (
            run_id,
            NOW,
            NOW,
            outcome,
            snapshot_json,
            snapshot_sha256,
        ),
    )


def test_existing_database_upgrades_without_data_loss() -> None:
    connection = connect_database(":memory:")
    initial = _authoritative_migration(
        1,
        "001_initial_schema.sql",
    )
    _apply_migration_definitions(connection, (initial,))
    _seed_recognition_document(connection)

    connection.execute(
        """INSERT INTO recognition_runs
           (id, document_id, engine_version,
            started_at, completed_at, outcome,
            error_code, error_message)
           VALUES (
               1, 1, 'legacy-engine',
               ?, ?, 'matched',
               NULL, NULL
           )""",
        (NOW, NOW),
    )
    connection.commit()

    report = apply_migrations(connection)

    assert [item.version for item in report.applied] == [2]
    assert [item.version for item in report.skipped] == [1]
    rows = connection.execute(
        """SELECT id, outcome,
                  input_snapshot_json,
                  input_snapshot_sha256
           FROM recognition_runs"""
    ).fetchall()

    assert [tuple(row) for row in rows] == [
        (1, "matched", None, None),
    ]

    rerun = apply_migrations(connection)
    assert rerun.applied == ()
    assert [item.version for item in rerun.skipped] == [1, 2]


def test_recognition_snapshot_columns_are_nullable_text(
    database: sqlite3.Connection,
) -> None:
    columns = {
        row["name"]: row
        for row in database.execute(
            "PRAGMA table_info(recognition_runs)"
        )
    }

    for name in (
        "input_snapshot_json",
        "input_snapshot_sha256",
    ):
        assert columns[name]["type"] == "TEXT"
        assert columns[name]["notnull"] == 0


@pytest.mark.parametrize(
    "outcome",
    [
        "matched",
        "not_matched",
        "ambiguous",
        "unsupported",
    ],
)
def test_non_failed_recognition_run_requires_snapshot_pair(
    database: sqlite3.Connection,
    outcome: str,
) -> None:
    _seed_recognition_document(database)

    with pytest.raises(
        sqlite3.IntegrityError,
        match="snapshot invariant violated",
    ):
        _insert_recognition_run(
            database,
            run_id=1,
            outcome=outcome,
            snapshot_json=None,
            snapshot_sha256=None,
        )


@pytest.mark.parametrize(
    ("snapshot_json", "snapshot_sha256"),
    [
        ("{}", None),
        (None, HASH),
    ],
)
def test_recognition_snapshot_pair_rejects_one_sided_values(
    database: sqlite3.Connection,
    snapshot_json: str | None,
    snapshot_sha256: str | None,
) -> None:
    _seed_recognition_document(database)

    with pytest.raises(
        sqlite3.IntegrityError,
        match="snapshot invariant violated",
    ):
        _insert_recognition_run(
            database,
            run_id=1,
            outcome="failed",
            snapshot_json=snapshot_json,
            snapshot_sha256=snapshot_sha256,
        )


def test_recognition_snapshot_rejects_invalid_json(
    database: sqlite3.Connection,
) -> None:
    _seed_recognition_document(database)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_recognition_run(
            database,
            run_id=1,
            outcome="matched",
            snapshot_json="not-json",
            snapshot_sha256=HASH,
        )


@pytest.mark.parametrize(
    "snapshot_sha256",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
    ],
)
def test_recognition_snapshot_rejects_invalid_sha256(
    database: sqlite3.Connection,
    snapshot_sha256: str,
) -> None:
    _seed_recognition_document(database)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_recognition_run(
            database,
            run_id=1,
            outcome="matched",
            snapshot_json="{}",
            snapshot_sha256=snapshot_sha256,
        )


@pytest.mark.parametrize(
    "outcome",
    [
        "matched",
        "not_matched",
        "ambiguous",
        "unsupported",
        "failed",
    ],
)
def test_recognition_snapshot_accepts_valid_pair(
    database: sqlite3.Connection,
    outcome: str,
) -> None:
    _seed_recognition_document(database)

    _insert_recognition_run(
        database,
        run_id=1,
        outcome=outcome,
        snapshot_json="{}",
        snapshot_sha256=HASH,
    )

    row = database.execute(
        """SELECT input_snapshot_json,
                  input_snapshot_sha256
           FROM recognition_runs
           WHERE id = 1"""
    ).fetchone()

    assert tuple(row) == ("{}", HASH)


def test_failed_recognition_run_accepts_missing_snapshot(
    database: sqlite3.Connection,
) -> None:
    _seed_recognition_document(database)

    _insert_recognition_run(
        database,
        run_id=1,
        outcome="failed",
        snapshot_json=None,
        snapshot_sha256=None,
    )

    row = database.execute(
        """SELECT input_snapshot_json,
                  input_snapshot_sha256
           FROM recognition_runs
           WHERE id = 1"""
    ).fetchone()

    assert tuple(row) == (None, None)


def test_recognition_snapshot_update_enforces_final_values(
    database: sqlite3.Connection,
) -> None:
    _seed_recognition_document(database)
    _insert_recognition_run(
        database,
        run_id=1,
        outcome="failed",
        snapshot_json=None,
        snapshot_sha256=None,
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="snapshot invariant violated",
    ):
        database.execute(
            """UPDATE recognition_runs
               SET outcome = 'matched'
               WHERE id = 1"""
        )

    database.execute(
        """UPDATE recognition_runs
           SET outcome = 'matched',
               input_snapshot_json = '{}',
               input_snapshot_sha256 = ?
           WHERE id = 1""",
        (HASH,),
    )

    row = database.execute(
        """SELECT outcome,
                  input_snapshot_json,
                  input_snapshot_sha256
           FROM recognition_runs
           WHERE id = 1"""
    ).fetchone()

    assert tuple(row) == ("matched", "{}", HASH)


def test_migrations_apply_report_and_skip_in_version_order() -> None:
    connection = connect_database(":memory:")
    definitions = tuple(
        MigrationDefinition(version, f"{version}.sql", f"CREATE TABLE t{version}(id INTEGER);".encode())
        for version in (3, 1, 2)
    )

    first = _apply_migration_definitions(connection, definitions)
    second = _apply_migration_definitions(connection, definitions)

    assert [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table' AND name LIKE 't%' ORDER BY rowid"
    )] == ["t1", "t2", "t3"]
    assert [migration.version for migration in first.applied] == [1, 2, 3]
    assert [migration.version for migration in get_applied_migrations(connection)] == [1, 2, 3]
    assert second.applied == ()
    assert [migration.version for migration in second.skipped] == [1, 2, 3]


def test_checksum_mismatch_is_structured(database: sqlite3.Connection) -> None:
    database.execute("UPDATE schema_migrations SET sha256 = ? WHERE version = 1", ("0" * 64,))
    database.commit()
    with pytest.raises(MigrationError, match="MIGRATION_CHECKSUM_MISMATCH") as caught:
        apply_migrations(database)
    assert caught.value.code == "MIGRATION_CHECKSUM_MISMATCH"
    assert caught.value.details is not None
    assert caught.value.details["version"] == 1


def test_active_transaction_is_structured(database: sqlite3.Connection) -> None:
    database.execute("SELECT 1")
    database.execute("BEGIN")
    with pytest.raises(MigrationError, match="MIGRATION_ACTIVE_TRANSACTION"):
        apply_migrations(database)
    database.rollback()


def test_real_sql_trigger_bodies_are_parsed(database: sqlite3.Connection) -> None:
    assert set(
        row[0]
        for row in database.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")
    ) == TRIGGERS


def test_failing_sql_migration_is_atomic_and_connection_recovers() -> None:
    connection = connect_database(":memory:")
    first = MigrationDefinition(
        1,
        "first.sql",
        b"CREATE TABLE retained(id INTEGER);\nINSERT INTO retained VALUES (10);\n",
    )
    failing = MigrationDefinition(
        2,
        "failing.sql",
        b"CREATE TABLE rolled_back(id INTEGER);\n"
        b"INSERT INTO rolled_back VALUES (1);\n"
        b"INSERT INTO retained VALUES (20);\n"
        b"INSERT INTO absent_table VALUES (1);\n",
    )
    _apply_migration_definitions(connection, (first,))
    with pytest.raises(MigrationError, match="MIGRATION_APPLY_FAILED") as caught:
        _apply_migration_definitions(connection, (first, failing))
    assert isinstance(caught.value.__cause__, sqlite3.Error)
    assert connection.execute("SELECT name FROM sqlite_schema WHERE name='rolled_back'").fetchone() is None
    assert [row[0] for row in connection.execute("SELECT id FROM retained")] == [10]
    assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations")] == [1]
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 2"
    ).fetchone()[0] == 0
    assert connection.execute("SELECT 42").fetchone()[0] == 42


def _connection_with_test_migration_metadata() -> sqlite3.Connection:
    connection = connect_database(":memory:")
    _apply_migration_definitions(
        connection, (MigrationDefinition(99, "seed.sql", b"SELECT 99;"),)
    )
    return connection


@pytest.mark.parametrize("version", [0, -1])
def test_schema_migrations_rejects_nonpositive_version(version: int) -> None:
    connection = _connection_with_test_migration_metadata()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO schema_migrations(version, name, sha256) VALUES (?, ?, ?)",
            (version, f"{version}.sql", "a" * 64),
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [("version", 1), ("name", "one.sql")],
)
def test_schema_migrations_rejects_duplicate_version_or_name(column: str, value: object) -> None:
    connection = connect_database(":memory:")
    _apply_migration_definitions(connection, (MigrationDefinition(1, "one.sql", b"SELECT 1;"),))
    version, name = (1, "two.sql") if column == "version" else (2, value)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO schema_migrations(version, name, sha256) VALUES (?, ?, ?)",
            (version, name, "a" * 64),
        )


@pytest.mark.parametrize("sha256", ["a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_schema_migrations_rejects_invalid_sha256(sha256: str) -> None:
    connection = _connection_with_test_migration_metadata()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO schema_migrations(version, name, sha256) VALUES (1, 'one.sql', ?)",
            (sha256,),
        )


def test_schema_migrations_accepts_lowercase_64_character_sha256() -> None:
    connection = _connection_with_test_migration_metadata()
    connection.execute(
        "INSERT INTO schema_migrations(version, name, sha256) VALUES (1, 'one.sql', ?)",
        ("abcdef0123456789" * 4,),
    )
    assert connection.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("applied", "requested"),
    [
        (MigrationDefinition(1, "one.sql", b"SELECT 1;"), MigrationDefinition(1, "one.sql", b"SELECT 2;")),
        (MigrationDefinition(1, "one.sql", b"SELECT 1;"), MigrationDefinition(1, "renamed.sql", b"SELECT 1;")),
        (MigrationDefinition(1, "one.sql", b"SELECT 1;"), MigrationDefinition(2, "one.sql", b"SELECT 1;")),
    ],
    ids=["same-version-different-hash", "same-version-different-name", "same-name-different-version"],
)
def test_migration_metadata_conflicts_are_structured(
    applied: MigrationDefinition, requested: MigrationDefinition
) -> None:
    connection = connect_database(":memory:")
    _apply_migration_definitions(connection, (applied,))
    with pytest.raises(MigrationError, match="MIGRATION_CHECKSUM_MISMATCH") as caught:
        _apply_migration_definitions(connection, (requested,))
    assert caught.value.code == "MIGRATION_CHECKSUM_MISMATCH"
    assert caught.value.details is not None
    assert "conflicts" in caught.value.details
    assert "sql" not in caught.value.details


def test_exact_migration_metadata_match_is_skipped() -> None:
    connection = connect_database(":memory:")
    definition = MigrationDefinition(7, "seven.sql", b"SELECT 7;")
    applied = _apply_migration_definitions(connection, (definition,)).applied
    rerun = _apply_migration_definitions(connection, (definition,))
    assert rerun.applied == ()
    assert rerun.skipped == applied


def test_exact_object_inventories_and_generic_schema(database: sqlite3.Connection) -> None:
    objects = {
        kind: {row[0] for row in database.execute(
            "SELECT name FROM sqlite_schema WHERE type=? AND name NOT LIKE 'sqlite_%'", (kind,)
        )}
        for kind in ("table", "view", "trigger", "index")
    }
    assert objects == {"table": TABLES, "view": VIEWS, "trigger": TRIGGERS, "index": INDEXES}
    schema = "\n".join(row[0] or "" for row in database.execute("SELECT sql FROM sqlite_schema"))
    assert "ascit" not in schema.lower()


def test_integrity_pragmas_and_connection(database: sqlite3.Connection) -> None:
    assert database.row_factory is sqlite3.Row
    assert database.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_representative_constraints(database: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO documents VALUES (1, 999, ?, 'x', NULL, NULL, NULL, NULL, ?)", (HASH, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO artifacts VALUES (1, 'x', 'bad', 'canonical', 'x', ?, 'x', 0, ?, ?)", (HASH, NOW, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO artifacts VALUES (1, 'x', 'vault', 'canonical', 'x', 'ABC', 'x', 0, ?, ?)", (NOW, NOW))
    _artifact(database, 1, "model")
    database.execute("INSERT INTO document_models VALUES (1, 'key', 'x', 'x', 'x', ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO document_models VALUES (2, 'key', 'x', 'x', 'x', ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO document_model_versions VALUES (1, 1, '1', 1, 'active', 'x', 1, 'not-json', ?, ?)", (HASH, NOW))


def test_natural_key_and_typed_projection_constraints(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO records VALUES (2, 1, 'record', 1, '{}', ?, '{}', ?, NULL, NULL, NULL, ?)", (f"{1:064x}", "f" * 64, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO record_field_values(record_id, field_path, value_index, value_type, text_value, date_value) VALUES (1, '/bad', 1, 'date', 'x', '2026-01-01')")


def test_publication_relationship_sequence_and_append_only(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    _publication(database, 1, 1, 2)
    with pytest.raises(sqlite3.IntegrityError, match="sequence must increase"):
        _publication(database, 2, 1, 2)
    with pytest.raises(sqlite3.IntegrityError, match="sequence must increase"):
        _publication(database, 3, 1, 1)
    with pytest.raises(sqlite3.IntegrityError, match="append-only: update forbidden"):
        database.execute("UPDATE dataset_publications SET published_at=? WHERE id=1", (NOW,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only: delete forbidden"):
        database.execute("DELETE FROM dataset_publications WHERE id=1")

    _artifact(database, 300, "dataset")
    _artifact(database, 301, "publication")
    database.execute("INSERT INTO datasets VALUES (2, 1, 'other', ?)", (NOW,))
    database.execute("INSERT INTO dataset_versions VALUES (2, 2, 1, 'manual', NULL, NULL, 300, 0, ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO dataset_publications VALUES (4, 1, 2, 3, 'publish', 301, 1, ?)", (NOW,))
    _artifact(database, 302, "publication")
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO dataset_publications VALUES (4, 2, 2, 1, 'publish', 302, 1, ?)", (NOW,))


def test_publication_sequence_is_per_dataset(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    _publication(database, 1, 1, 1)
    _artifact(database, 300, "dataset")
    _artifact(database, 301, "publication")
    database.execute("INSERT INTO datasets VALUES (2, 1, 'other', ?)", (NOW,))
    database.execute("INSERT INTO dataset_versions VALUES (2, 2, 1, 'manual', NULL, NULL, 300, 0, ?)", (NOW,))
    database.execute("INSERT INTO dataset_publications VALUES (2, 2, 2, 1, 'publish', 301, NULL, ?)", (NOW,))


def _eligible_publication(database: sqlite3.Connection, version: int = 1) -> None:
    _base(database)
    _version(database, version, version)
    _validation(database, 1, version, 1, "validated")
    _publication(database, 1, version, 1)


def test_validation_without_publication_is_not_queryable(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    _validation(database, 1, 1, 1, "validated")
    assert _active(database) == []


def test_publication_without_validation_is_not_queryable(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    _publication(database, 1, 1, 1)
    assert _active(database) == []


def test_publication_with_pending_validation_is_not_queryable(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    _validation(database, 1, 1, 1, "pending")
    _publication(database, 1, 1, 1)
    assert _active(database) == []


def test_publication_with_rejected_validation_is_not_queryable(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    _validation(database, 1, 1, 1, "rejected")
    _publication(database, 1, 1, 1)
    assert _active(database) == []


def test_validated_publication_becomes_active(database: sqlite3.Connection) -> None:
    _eligible_publication(database)
    assert _active(database) == [1]


def test_corrected_publication_becomes_active(database: sqlite3.Connection) -> None:
    _base(database)
    _version(database, 1, 1)
    _validation(database, 1, 1, 1, "corrected")
    _publication(database, 1, 1, 1)
    assert _active(database) == [1]


def _add_newer_unpublished_version(
    database: sqlite3.Connection, decision: str
) -> None:
    _eligible_publication(database)
    _version(database, 2, 2)
    _validation(database, 2, 2, 1, decision)


def test_newer_unpublished_pending_version_does_not_replace_active(
    database: sqlite3.Connection,
) -> None:
    _add_newer_unpublished_version(database, "pending")
    assert _active(database) == [1]


def test_newer_unpublished_rejected_version_does_not_replace_active(
    database: sqlite3.Connection,
) -> None:
    _add_newer_unpublished_version(database, "rejected")
    assert _active(database) == [1]


def test_later_eligible_publication_replaces_previous_active(database: sqlite3.Connection) -> None:
    _eligible_publication(database)
    _version(database, 2, 2)
    _validation(database, 2, 2, 1, "validated")
    _publication(database, 2, 2, 2, supersedes=1)
    assert _active(database) == [2]


def test_rollback_publication_selects_earlier_eligible_version(database: sqlite3.Connection) -> None:
    _eligible_publication(database)
    _version(database, 2, 2)
    _validation(database, 2, 2, 1, "validated")
    _publication(database, 2, 2, 2, supersedes=1)
    _publication(database, 3, 1, 3, "rollback", 2)
    assert _active(database) == [1]


def test_queryable_records_exposes_only_active_version(database: sqlite3.Connection) -> None:
    _eligible_publication(database)
    _version(database, 2, 2)
    assert [row[0] for row in database.execute("SELECT id FROM queryable_records")] == [1]


def test_queryable_record_fields_exposes_only_active_version(database: sqlite3.Connection) -> None:
    _eligible_publication(database)
    _version(database, 2, 2)
    assert [row[0] for row in database.execute("SELECT record_id FROM queryable_record_fields")] == [1]


def _add_later_ineligible_publication(
    database: sqlite3.Connection, decision: str
) -> None:
    _eligible_publication(database)
    _version(database, 2, 2)
    _validation(database, 2, 2, 1, decision)
    _publication(database, 2, 2, 2, supersedes=1)


def test_later_published_pending_version_does_not_hide_active(
    database: sqlite3.Connection,
) -> None:
    _add_later_ineligible_publication(database, "pending")
    assert _active(database) == [1]


def test_later_published_rejected_version_does_not_hide_active(
    database: sqlite3.Connection,
) -> None:
    _add_later_ineligible_publication(database, "rejected")
    assert _active(database) == [1]
