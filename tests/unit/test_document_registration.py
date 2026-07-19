"""PDF registration workflow and focused document repository tests."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast

import pytest
import yaml

from reference_engine import registration as registration_module
from reference_engine.db import (
    apply_migrations,
    connect_database,
    get_document,
    get_document_by_sha256,
    register_artifact,
    register_document,
)
from reference_engine.errors import DocumentRegistrationError
from reference_engine.registration import (
    DocumentRegistrationInput,
    DocumentRegistrationStatus,
    register_pdf_document,
)

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
NOW = datetime(2026, 7, 19, 10, 11, 12, 345000, tzinfo=UTC)
RETRIEVED = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_database(tmp_path / "documents.sqlite3")
    apply_migrations(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic input.pdf"
    path.write_bytes(PDF_BYTES)
    return path


def request(source: Path, vault: Path, **values: object) -> DocumentRegistrationInput:
    return DocumentRegistrationInput(
        source_path=source,
        vault_root=vault,
        source_url=cast(str | None, values.get("source_url")),
        retrieved_at=cast(datetime | None, values.get("retrieved_at")),
    )


def register(
    database: sqlite3.Connection, source: Path, vault: Path
) -> registration_module.DocumentRegistrationResult:
    return register_pdf_document(
        database,
        request(
            source,
            vault,
            source_url="https://example.invalid/synthetic.pdf",
            retrieved_at=RETRIEVED,
        ),
        clock=lambda: NOW,
    )


def canonical(vault: Path) -> tuple[str, Path, Path]:
    digest = hashlib.sha256(PDF_BYTES).hexdigest()
    directory = vault / "documents" / "sha256" / digest[:2] / digest
    return digest, directory / "source.pdf", directory / "metadata.yaml"


def temporary_files(vault: Path) -> list[Path]:
    return [item for item in vault.rglob("*.tmp")] if vault.exists() else []


def test_first_registration_preserves_source_and_writes_sidecar_and_rows(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    digest, expected_source, expected_metadata = canonical(vault)

    result = register(database, source, vault)

    assert result.status is DocumentRegistrationStatus.REGISTERED
    assert result.canonical_source_path == expected_source
    assert result.metadata_path == expected_metadata
    assert expected_source.read_bytes() == PDF_BYTES == source.read_bytes()
    assert hashlib.sha256(expected_source.read_bytes()).hexdigest() == digest
    assert expected_source.stat().st_size == len(PDF_BYTES)
    assert yaml.safe_load(expected_metadata.read_text()) == {
        "schema_version": 1,
        "document_sha256": digest,
        "original_filename": source.name,
        "mime_type": "application/pdf",
        "byte_size": len(PDF_BYTES),
        "source_url": "https://example.invalid/synthetic.pdf",
        "retrieved_at": "2026-07-18T08:00:00.000Z",
        "registered_at": "2026-07-19T10:11:12.345Z",
    }
    assert result.source_artifact.kind == "source_document"
    assert result.source_artifact.storage_scope == "vault"
    assert result.source_artifact.retention_class == "canonical"
    assert result.source_artifact.relative_path == expected_source.relative_to(
        vault
    ).as_posix()
    assert result.source_artifact.sha256 == digest
    assert result.source_artifact.mime_type == "application/pdf"
    assert result.source_artifact.byte_size == len(PDF_BYTES)
    assert result.document.source_artifact_id == result.source_artifact.id
    assert result.document.published_date is None
    assert result.document.page_count is None
    assert get_document(database, result.document.id) == result.document
    assert get_document_by_sha256(database, digest) == result.document
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    assert temporary_files(vault) == []


def test_sidecar_omits_optional_acquisition_values(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    result = register_pdf_document(
        database, request(source, tmp_path / "vault"), clock=lambda: NOW
    )
    metadata = yaml.safe_load(result.metadata_path.read_text())
    assert "source_url" not in metadata
    assert "retrieved_at" not in metadata


def test_same_file_is_idempotent_without_rewriting_or_new_rows(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    first = register(database, source, vault)
    source_times = (
        first.canonical_source_path.stat().st_mtime_ns,
        first.metadata_path.stat().st_mtime_ns,
    )

    second = register_pdf_document(
        database,
        request(source, vault, source_url="https://different.invalid/ignored"),
        clock=lambda: datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert second.status is DocumentRegistrationStatus.ALREADY_REGISTERED
    assert second.document == first.document
    assert second.source_artifact == first.source_artifact
    assert source_times == (
        second.canonical_source_path.stat().st_mtime_ns,
        second.metadata_path.stat().st_mtime_ns,
    )
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    metadata = yaml.safe_load(second.metadata_path.read_text())
    assert metadata["original_filename"] == source.name
    assert metadata["registered_at"] == "2026-07-19T10:11:12.345Z"
    assert metadata["source_url"] == "https://example.invalid/synthetic.pdf"
    assert temporary_files(vault) == []


def test_same_content_under_another_filename_preserves_first_registration(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    first = register(database, source, vault)
    duplicate = tmp_path / "renamed.PDF"
    duplicate.write_bytes(PDF_BYTES)

    second = register_pdf_document(
        database,
        request(duplicate, vault),
        clock=lambda: datetime(2031, 1, 1, tzinfo=UTC),
    )

    assert second.status is DocumentRegistrationStatus.ALREADY_REGISTERED
    assert second.document == first.document
    assert second.document.original_filename == source.name
    metadata = yaml.safe_load(second.metadata_path.read_text())
    assert metadata["original_filename"] == source.name


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("fake.pdf", b"not a PDF", "DOCUMENT_SOURCE_UNSUPPORTED"),
        ("missing.pdf", None, "DOCUMENT_SOURCE_UNREADABLE"),
    ],
)
def test_invalid_or_unreadable_input_is_structured(
    database: sqlite3.Connection,
    tmp_path: Path,
    name: str,
    content: bytes | None,
    code: str,
) -> None:
    source = tmp_path / name
    if content is not None:
        source.write_bytes(content)
    with pytest.raises(DocumentRegistrationError) as caught:
        register_pdf_document(database, request(source, tmp_path / "vault"))
    assert caught.value.code == code
    assert caught.value.details == {"source_path": str(source)}


def test_non_regular_input_is_unreadable(
    database: sqlite3.Connection, tmp_path: Path
) -> None:
    with pytest.raises(DocumentRegistrationError) as caught:
        register_pdf_document(database, request(tmp_path, tmp_path / "vault"))
    assert caught.value.code == "DOCUMENT_SOURCE_UNREADABLE"


def test_regular_but_unreadable_input_is_structured(
    database: sqlite3.Connection,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_open(path: Path, *args: object, **kwargs: object) -> NoReturn:
        raise PermissionError(f"synthetic denial for {path}")

    monkeypatch.setattr(Path, "open", deny_open)
    with pytest.raises(DocumentRegistrationError) as caught:
        register_pdf_document(database, request(source, tmp_path / "vault"))
    assert caught.value.code == "DOCUMENT_SOURCE_UNREADABLE"
    assert caught.value.details == {"source_path": str(source)}


def test_preexisting_canonical_source_conflict_is_not_overwritten(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    _, canonical_source, metadata = canonical(vault)
    canonical_source.parent.mkdir(parents=True)
    conflicting = b"%PDF-conflicting"
    canonical_source.write_bytes(conflicting)

    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"
    assert canonical_source.read_bytes() == conflicting
    assert not metadata.exists()
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert temporary_files(vault) == []


def test_preexisting_sidecar_conflict_is_not_overwritten_and_new_source_is_removed(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    _, canonical_source, metadata = canonical(vault)
    metadata.parent.mkdir(parents=True)
    conflicting = b"schema_version: 999\n"
    metadata.write_bytes(conflicting)

    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"
    assert metadata.read_bytes() == conflicting
    assert not canonical_source.exists()
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert temporary_files(vault) == []


def test_valid_canonical_pair_reconstructs_rows_and_preserves_first_metadata(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    first = register(database, source, vault)
    mtimes = (
        first.canonical_source_path.stat().st_mtime_ns,
        first.metadata_path.stat().st_mtime_ns,
    )
    database.execute("DELETE FROM documents")
    database.execute("DELETE FROM artifacts")
    database.commit()
    duplicate = tmp_path / "different-name.pdf"
    duplicate.write_bytes(PDF_BYTES)

    rebuilt = register_pdf_document(
        database,
        request(
            duplicate,
            vault,
            source_url="https://different.invalid/ignored.pdf",
            retrieved_at=datetime(2030, 1, 1, tzinfo=UTC),
        ),
        clock=lambda: datetime(2031, 1, 1, tzinfo=UTC),
    )

    assert rebuilt.status is DocumentRegistrationStatus.ALREADY_REGISTERED
    assert rebuilt.document.original_filename == source.name
    assert rebuilt.document.source_url == "https://example.invalid/synthetic.pdf"
    assert rebuilt.document.retrieved_at == "2026-07-18T08:00:00.000Z"
    assert rebuilt.document.registered_at == "2026-07-19T10:11:12.345Z"
    assert rebuilt.source_artifact.created_at == "2026-07-19T10:11:12.345Z"
    assert mtimes == (
        rebuilt.canonical_source_path.stat().st_mtime_ns,
        rebuilt.metadata_path.stat().st_mtime_ns,
    )
    assert temporary_files(vault) == []


class _CanonicalMutationConnection(sqlite3.Connection):
    mutation_path: Path | None = None
    mutation_bytes = b""

    def execute(  # type: ignore[override]
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        if sql == "BEGIN IMMEDIATE" and self.mutation_path is not None:
            self.mutation_path.write_bytes(self.mutation_bytes)
            self.mutation_path = None
        return super().execute(sql, parameters)


def test_reconstruction_revalidates_canonical_pair_after_write_lock(
    source: Path, tmp_path: Path
) -> None:
    database = sqlite3.connect(
        tmp_path / "mutation.sqlite3", factory=_CanonicalMutationConnection
    )
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    apply_migrations(database)
    vault = tmp_path / "vault"
    first = register(database, source, vault)
    database.execute("DELETE FROM documents")
    database.execute("DELETE FROM artifacts")
    database.commit()
    externally_changed = first.metadata_path.read_bytes() + b"source_url: changed\n"
    database.mutation_path = first.metadata_path
    database.mutation_bytes = externally_changed
    try:
        with pytest.raises(DocumentRegistrationError) as caught:
            register(database, source, vault)

        assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"
        assert first.metadata_path.read_bytes() == externally_changed
        assert first.canonical_source_path.read_bytes() == PDF_BYTES
        assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert temporary_files(vault) == []
    finally:
        database.close()


def test_caller_rollback_then_fresh_database_reconstructs_from_vault(
    source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    first_database = connect_database(tmp_path / "first.sqlite3")
    apply_migrations(first_database)
    first_database.execute("BEGIN")
    first = register(first_database, source, vault)
    first_database.rollback()
    first_database.close()

    fresh_database = connect_database(tmp_path / "fresh.sqlite3")
    apply_migrations(fresh_database)
    try:
        rebuilt = register(fresh_database, source, vault)
        assert rebuilt.status is DocumentRegistrationStatus.ALREADY_REGISTERED
        assert rebuilt.document.original_filename == first.document.original_filename
        assert get_document_by_sha256(fresh_database, first.document.content_sha256)
    finally:
        fresh_database.close()


@pytest.mark.parametrize(
    "sidecar",
    [
        "- schema_version\n- 1\n",
        "schema_version: 999\n",
        "schema_version: 1\ndocument_sha256: 12\n",
    ],
)
def test_malformed_or_unsupported_preexisting_sidecar_is_a_conflict(
    database: sqlite3.Connection,
    source: Path,
    tmp_path: Path,
    sidecar: str,
) -> None:
    vault = tmp_path / "vault"
    _, canonical_source, metadata = canonical(vault)
    canonical_source.parent.mkdir(parents=True)
    canonical_source.write_bytes(PDF_BYTES)
    metadata.write_text(sidecar)
    before = (canonical_source.read_bytes(), metadata.read_bytes())

    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"
    assert (canonical_source.read_bytes(), metadata.read_bytes()) == before
    assert temporary_files(vault) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("unknown", "value"),
        ("original_filename", None),
        ("original_filename", 12),
        ("original_filename", ""),
        ("source_url", None),
        ("source_url", 12),
        ("retrieved_at", None),
        ("retrieved_at", "not-a-timestamp"),
        ("retrieved_at", "2026-01-01T00:00:00"),
        ("retrieved_at", "2026-01-01T00:00:00+02:00"),
        ("retrieved_at", "2026-01-01T00:00:00-05:00"),
        ("registered_at", "not-a-timestamp"),
        ("registered_at", "2026-01-01T00:00:00"),
        ("registered_at", "2026-01-01T00:00:00+02:00"),
        ("registered_at", "2026-01-01T00:00:00-05:00"),
        ("byte_size", "1"),
        ("byte_size", -1),
        ("schema_version", 2),
        ("document_sha256", "0" * 64),
        ("mime_type", "text/plain"),
        ("byte_size", len(PDF_BYTES) + 1),
    ],
)
def test_invalid_preexisting_sidecar_values_are_conflicts(
    database: sqlite3.Connection,
    source: Path,
    tmp_path: Path,
    key: str,
    value: object,
) -> None:
    vault = tmp_path / "vault"
    digest, canonical_source, metadata = canonical(vault)
    canonical_source.parent.mkdir(parents=True)
    canonical_source.write_bytes(PDF_BYTES)
    sidecar: dict[str, object] = {
        "schema_version": 1,
        "document_sha256": digest,
        "original_filename": source.name,
        "mime_type": "application/pdf",
        "byte_size": len(PDF_BYTES),
        "registered_at": "2026-07-19T10:11:12.345Z",
    }
    sidecar[key] = value
    metadata.write_text(yaml.safe_dump(sidecar))

    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"


def test_missing_required_sidecar_key_is_a_conflict(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    digest, canonical_source, metadata = canonical(vault)
    canonical_source.parent.mkdir(parents=True)
    canonical_source.write_bytes(PDF_BYTES)
    metadata.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "document_sha256": digest,
                "original_filename": source.name,
                "mime_type": "application/pdf",
                "byte_size": len(PDF_BYTES),
            }
        )
    )

    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"


@pytest.mark.parametrize("symlink_name", ["source.pdf", "metadata.yaml"])
def test_canonical_symlinks_are_rejected(
    database: sqlite3.Connection,
    source: Path,
    tmp_path: Path,
    symlink_name: str,
) -> None:
    vault = tmp_path / "vault"
    first = register(database, source, vault)
    database.execute("DELETE FROM documents")
    database.execute("DELETE FROM artifacts")
    database.commit()
    target = (
        first.canonical_source_path
        if symlink_name == "source.pdf"
        else first.metadata_path
    )
    saved = target.with_suffix(target.suffix + ".saved")
    target.rename(saved)
    target.symlink_to(saved.name)

    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"
    assert target.is_symlink()
    assert temporary_files(vault) == []


def test_database_failure_rolls_back_rows_and_compensates_new_files(
    database: sqlite3.Connection,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("forced document insert failure")

    monkeypatch.setattr(registration_module, "register_document", fail)
    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_REGISTRATION_CONFLICT"
    assert database.in_transaction is False
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert not (vault / "documents").exists()
    assert temporary_files(vault) == []


class _RollbackFailingConnection(sqlite3.Connection):
    def rollback(self) -> None:
        raise sqlite3.OperationalError("forced rollback failure")


def test_rollback_failure_preserves_registration_error_and_compensates_files(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = sqlite3.connect(
        tmp_path / "rollback.sqlite3", factory=_RollbackFailingConnection
    )
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    apply_migrations(database)
    vault = tmp_path / "vault"

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("forced document insert failure")

    monkeypatch.setattr(registration_module, "register_document", fail)
    try:
        with pytest.raises(DocumentRegistrationError) as caught:
            register(database, source, vault)
        assert caught.value.code == "DOCUMENT_REGISTRATION_CONFLICT"
        assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        assert "forced document insert failure" in str(caught.value.__cause__)
        assert any(
            "Rollback of the registration transaction failed: OperationalError"
            == note
            for note in caught.value.__cause__.__notes__
        )
        assert not (vault / "documents").exists()
        assert temporary_files(vault) == []
    finally:
        sqlite3.Connection.rollback(database)
        database.close()


def test_caller_owned_transaction_uses_savepoint_and_is_not_committed(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    database.execute("BEGIN")
    result = register(database, source, tmp_path / "vault")
    assert database.in_transaction is True
    assert get_document(database, result.document.id) == result.document

    database.rollback()

    assert get_document(database, result.document.id) is None
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    # Durable canonical state is independent from caller rollback of disposable SQLite.
    assert result.canonical_source_path.read_bytes() == PDF_BYTES
    assert result.metadata_path.is_file()


def test_failure_inside_caller_transaction_preserves_outer_work(
    database: sqlite3.Connection,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database.execute("BEGIN")
    database.execute(
        """INSERT INTO artifacts
           (kind, storage_scope, retention_class, relative_path, sha256, mime_type,
            byte_size, created_at, registered_at)
           VALUES ('test', 'workspace', 'transient', 'outer', ?, 'text/plain', 0,
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
        ("f" * 64,),
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("forced")

    monkeypatch.setattr(registration_module, "register_document", fail)
    with pytest.raises(DocumentRegistrationError):
        register(database, source, tmp_path / "vault")

    assert database.in_transaction is True
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    database.rollback()


def test_existing_canonical_state_survives_relational_conflict(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    first = register(database, source, vault)
    original_source = first.canonical_source_path.read_bytes()
    original_sidecar = first.metadata_path.read_bytes()
    database.execute(
        "UPDATE artifacts SET mime_type = 'text/plain' WHERE id = ?",
        (first.source_artifact.id,),
    )
    database.commit()

    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_REGISTRATION_CONFLICT"
    assert first.canonical_source_path.read_bytes() == original_source
    assert first.metadata_path.read_bytes() == original_sidecar


def test_document_repository_foreign_key_is_enforced(
    database: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        register_document(
            database,
            source_artifact_id=999,
            content_sha256="a" * 64,
            original_filename="synthetic.pdf",
            source_url=None,
            retrieved_at=None,
            registered_at="2026-01-01T00:00:00Z",
        )
    assert database.in_transaction is False


def test_document_repository_failure_preserves_caller_transaction(
    database: sqlite3.Connection,
) -> None:
    database.execute("BEGIN")
    with pytest.raises(sqlite3.IntegrityError):
        register_document(
            database,
            source_artifact_id=999,
            content_sha256="b" * 64,
            original_filename="synthetic.pdf",
            source_url=None,
            retrieved_at=None,
            registered_at="2026-01-01T00:00:00Z",
        )
    assert database.in_transaction is True
    database.rollback()


class _RacingConnection(sqlite3.Connection):
    race_insert = False

    def execute(  # type: ignore[override]
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        if self.race_insert and sql.lstrip().startswith("INSERT INTO documents"):
            self.race_insert = False
            super().execute(sql, parameters)
            self.commit()
        return super().execute(sql, parameters)


def test_document_repository_recovers_exact_uniqueness_race(tmp_path: Path) -> None:
    connection = sqlite3.connect(
        tmp_path / "race.sqlite3", factory=_RacingConnection
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection)
    artifact = register_artifact(
        connection,
        kind="source_document",
        storage_scope="vault",
        retention_class="canonical",
        relative_path="documents/race/source.pdf",
        sha256="c" * 64,
        mime_type="application/pdf",
        byte_size=1,
        created_at="2026-01-01T00:00:00Z",
    )
    connection.commit()
    connection.race_insert = True
    document = register_document(
        connection,
        source_artifact_id=artifact.id,
        content_sha256="c" * 64,
        original_filename="race.pdf",
        source_url=None,
        retrieved_at=None,
        registered_at="2026-01-01T00:00:00Z",
    )
    assert document.content_sha256 == "c" * 64
    assert connection.in_transaction is False
    connection.close()


def test_atomic_placement_race_with_conflicting_source_is_detected(
    database: sqlite3.Connection,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    _, canonical_source, _ = canonical(vault)
    original_place = registration_module._place_without_overwrite
    raced = False

    def place(staged: Path, destination: Path) -> bool:
        nonlocal raced
        if destination.name == "source.pdf" and not raced:
            raced = True
            destination.write_bytes(b"%PDF-racing-conflict")
        return original_place(staged, destination)

    monkeypatch.setattr(registration_module, "_place_without_overwrite", place)
    with pytest.raises(DocumentRegistrationError) as caught:
        register(database, source, vault)

    assert caught.value.code == "DOCUMENT_STORAGE_CONFLICT"
    assert canonical_source.read_bytes() == b"%PDF-racing-conflict"
    assert database.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert temporary_files(vault) == []


def test_source_path_is_read_only(
    database: sqlite3.Connection, source: Path, tmp_path: Path
) -> None:
    before = (source.read_bytes(), source.stat().st_mtime_ns, os.stat(source).st_mode)
    register(database, source, tmp_path / "vault")
    after = (source.read_bytes(), source.stat().st_mtime_ns, os.stat(source).st_mode)
    assert after == before
