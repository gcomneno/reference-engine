"""Registration of unchanged local PDF documents into the canonical vault."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, TypedDict

import yaml

from reference_engine.db.artifacts import Artifact, register_artifact
from reference_engine.db.documents import (
    Document,
    get_document_by_sha256,
    register_document,
)
from reference_engine.errors import (
    ArtifactRepositoryError,
    DocumentRegistrationError,
    DocumentRepositoryError,
)

PDF_MIME_TYPE = "application/pdf"
SIDECAR_SCHEMA_VERSION = 1
_CHUNK_SIZE = 1024 * 1024


class DocumentRegistrationStatus(StrEnum):
    """Outcome of a successful registration request."""

    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"


@dataclass(frozen=True)
class DocumentRegistrationInput:
    """Caller-supplied local source and acquisition metadata."""

    source_path: str | Path
    vault_root: str | Path
    source_url: str | None = None
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class DocumentRegistrationResult:
    """A registered document and its canonical storage locations."""

    document: Document
    source_artifact: Artifact
    status: DocumentRegistrationStatus
    canonical_source_path: Path
    metadata_path: Path


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("document registration timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_size = 0
    while chunk := stream.read(_CHUNK_SIZE):
        digest.update(chunk)
        byte_size += len(chunk)
    return digest.hexdigest(), byte_size


def _inspect_source(source_path: Path) -> tuple[str, int]:
    try:
        source_stat = source_path.stat()
        if not stat.S_ISREG(source_stat.st_mode):
            raise DocumentRegistrationError(
                code="DOCUMENT_SOURCE_UNREADABLE",
                message="The document source is not a regular file.",
                details={"source_path": str(source_path)},
            )
        with source_path.open("rb") as stream:
            signature = stream.read(5)
            if signature != b"%PDF-":
                raise DocumentRegistrationError(
                    code="DOCUMENT_SOURCE_UNSUPPORTED",
                    message="The document source does not have a PDF signature.",
                    details={"source_path": str(source_path)},
                )
            stream.seek(0)
            return _hash_stream(stream)
    except DocumentRegistrationError:
        raise
    except (OSError, ValueError) as error:
        raise DocumentRegistrationError(
            code="DOCUMENT_SOURCE_UNREADABLE",
            message="The document source could not be read.",
            details={"source_path": str(source_path)},
        ) from error


def _relative_source_path(content_sha256: str) -> Path:
    return (
        Path("documents")
        / "sha256"
        / content_sha256[:2]
        / content_sha256
        / "source.pdf"
    )


def _sidecar(
    *,
    content_sha256: str,
    original_filename: str,
    byte_size: int,
    source_url: str | None,
    retrieved_at: str | None,
    registered_at: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "document_sha256": content_sha256,
        "original_filename": original_filename,
        "mime_type": PDF_MIME_TYPE,
        "byte_size": byte_size,
    }
    if source_url is not None:
        result["source_url"] = source_url
    if retrieved_at is not None:
        result["retrieved_at"] = retrieved_at
    result["registered_at"] = registered_at
    return result


def _storage_conflict(
    message: str, content_sha256: str, path: Path
) -> DocumentRegistrationError:
    return DocumentRegistrationError(
        code="DOCUMENT_STORAGE_CONFLICT",
        message=message,
        details={"content_sha256": content_sha256, "path": str(path)},
    )


def _verify_source(path: Path, content_sha256: str, byte_size: int) -> None:
    try:
        path_stat = path.lstat()
        if not stat.S_ISREG(path_stat.st_mode):
            raise _storage_conflict(
                "The canonical source path is not a regular file.",
                content_sha256,
                path,
            )
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise _storage_conflict(
                    "The canonical source does not have a PDF signature.",
                    content_sha256,
                    path,
                )
            stream.seek(0)
            actual_hash, actual_size = _hash_stream(stream)
    except DocumentRegistrationError:
        raise
    except OSError as error:
        raise _storage_conflict(
            "The canonical source could not be verified.", content_sha256, path
        ) from error
    if (actual_hash, actual_size) != (content_sha256, byte_size):
        raise _storage_conflict(
            "The canonical source bytes conflict with the requested document.",
            content_sha256,
            path,
        )


class _Sidecar(TypedDict):
    schema_version: int
    document_sha256: str
    original_filename: str
    mime_type: str
    byte_size: int
    source_url: str | None
    retrieved_at: str | None
    registered_at: str


def _valid_stored_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return value


def _load_sidecar(path: Path, content_sha256: str) -> _Sidecar:
    try:
        path_stat = path.lstat()
        if not stat.S_ISREG(path_stat.st_mode):
            raise _storage_conflict(
                "The canonical metadata path is not a regular file.",
                content_sha256,
                path,
            )
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except DocumentRegistrationError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise _storage_conflict(
            "The canonical metadata could not be verified.", content_sha256, path
        ) from error
    required = {
        "schema_version",
        "document_sha256",
        "original_filename",
        "mime_type",
        "byte_size",
        "registered_at",
    }
    optional = {"source_url", "retrieved_at"}
    if not isinstance(loaded, dict) or set(loaded) - optional != required:
        raise _storage_conflict(
            "The canonical metadata has an unexpected structure.",
            content_sha256,
            path,
        )
    schema_version = loaded["schema_version"]
    byte_size = loaded["byte_size"]
    source_url = loaded.get("source_url")
    retrieved_at_value = loaded.get("retrieved_at")
    registered_at_value = loaded["registered_at"]
    valid = (
        type(schema_version) is int
        and schema_version == SIDECAR_SCHEMA_VERSION
        and isinstance(loaded["document_sha256"], str)
        and isinstance(loaded["original_filename"], str)
        and bool(loaded["original_filename"])
        and isinstance(loaded["mime_type"], str)
        and type(byte_size) is int
        and byte_size >= 0
        and ("source_url" not in loaded or isinstance(source_url, str))
        and (
            "retrieved_at" not in loaded
            or _valid_stored_timestamp(retrieved_at_value)
        )
        and _valid_stored_timestamp(registered_at_value)
    )
    if not valid:
        raise _storage_conflict(
            "The canonical metadata contains invalid values.", content_sha256, path
        )
    return _Sidecar(
        schema_version=schema_version,
        document_sha256=loaded["document_sha256"],
        original_filename=loaded["original_filename"],
        mime_type=loaded["mime_type"],
        byte_size=byte_size,
        source_url=source_url,
        retrieved_at=retrieved_at_value,
        registered_at=registered_at_value,
    )


def _verify_sidecar(
    path: Path, expected: Mapping[str, object], content_sha256: str
) -> None:
    loaded = _load_sidecar(path, content_sha256)
    normalized = {key: value for key, value in loaded.items() if value is not None}
    if normalized != expected:
        raise _storage_conflict(
            "The canonical metadata conflicts with the registered document.",
            content_sha256,
            path,
        )


def _mkdirs(path: Path, stop: Path) -> list[Path]:
    missing: list[Path] = []
    created: list[Path] = []
    cursor = path
    while cursor != stop and not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    try:
        if not stop.exists():
            stop.mkdir(parents=True, exist_ok=True)
            created.append(stop)
        for directory in reversed(missing):
            try:
                directory.mkdir()
                created.append(directory)
            except FileExistsError:
                if not directory.is_dir():
                    raise
    except OSError as error:
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                break
        raise DocumentRegistrationError(
            code="DOCUMENT_STORAGE_CONFLICT",
            message="The canonical document directory could not be created.",
            details={"path": str(path)},
        ) from error
    return created


def _stage_copy(source: Path, directory: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".source.pdf.", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as source_stream:
            while chunk := source_stream.read(_CHUNK_SIZE):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except Exception as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            error.add_note(
                "Cleanup of a newly created source staging file failed: "
                f"{type(cleanup_error).__name__}"
            )
        raise
    return temporary


def _stage_sidecar(content: bytes, directory: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".metadata.yaml.", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
    except Exception as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            error.add_note(
                "Cleanup of a newly created metadata staging file failed: "
                f"{type(cleanup_error).__name__}"
            )
        raise
    return temporary


def _place_without_overwrite(staged: Path, destination: Path) -> bool:
    placed = False
    try:
        os.link(staged, destination)
        placed = True
    except FileExistsError:
        return False
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            if placed:
                destination.unlink(missing_ok=True)
            raise
    return True


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_empty_directories(created: list[Path]) -> list[OSError]:
    errors: list[OSError] = []
    for directory in reversed(created):
        try:
            directory.rmdir()
        except OSError as error:
            errors.append(error)
            break
    return errors


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise DocumentRegistrationError(
            code="DOCUMENT_STORAGE_CONFLICT",
            message="Canonical document storage could not be inspected.",
            details={"path": str(path)},
        ) from error
    return True


def _artifact_for_document(
    connection: sqlite3.Connection, document: Document
) -> Artifact:
    row = connection.execute(
        """SELECT id, kind, storage_scope, retention_class, relative_path, sha256,
                  mime_type, byte_size, created_at, registered_at
           FROM artifacts WHERE id = ?""",
        (document.source_artifact_id,),
    ).fetchone()
    if row is None:
        raise DocumentRegistrationError(
            code="DOCUMENT_REGISTRATION_CONFLICT",
            message="The registered document has no source artifact.",
            details={"document_id": document.id},
        )
    return Artifact(**dict(row))


def register_pdf_document(
    connection: sqlite3.Connection,
    registration: DocumentRegistrationInput,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DocumentRegistrationResult:
    """Register one local PDF while preserving caller-owned transactions."""

    source_path = Path(registration.source_path)
    vault_root = Path(registration.vault_root)
    content_sha256, byte_size = _inspect_source(source_path)
    relative_source = _relative_source_path(content_sha256)
    canonical_source = vault_root / relative_source
    metadata_path = canonical_source.with_name("metadata.yaml")

    existing = get_document_by_sha256(connection, content_sha256)
    if existing is not None:
        artifact = _artifact_for_document(connection, existing)
        expected_path = relative_source.as_posix()
        if (
            artifact.kind != "source_document"
            or artifact.storage_scope != "vault"
            or artifact.retention_class != "canonical"
            or artifact.relative_path != expected_path
            or artifact.sha256 != content_sha256
            or artifact.mime_type != PDF_MIME_TYPE
            or artifact.byte_size != byte_size
        ):
            raise DocumentRegistrationError(
                code="DOCUMENT_REGISTRATION_CONFLICT",
                message="The source artifact conflicts with the registered document.",
                details={"document_id": existing.id, "artifact_id": artifact.id},
            )
        expected_sidecar = _sidecar(
            content_sha256=content_sha256,
            original_filename=existing.original_filename,
            byte_size=byte_size,
            source_url=existing.source_url,
            retrieved_at=existing.retrieved_at,
            registered_at=existing.registered_at,
        )
        _verify_source(canonical_source, content_sha256, byte_size)
        _verify_sidecar(metadata_path, expected_sidecar, content_sha256)
        return DocumentRegistrationResult(
            existing,
            artifact,
            DocumentRegistrationStatus.ALREADY_REGISTERED,
            canonical_source,
            metadata_path,
        )

    source_exists = _path_exists(canonical_source)
    sidecar_exists = _path_exists(metadata_path)
    if source_exists != sidecar_exists:
        conflict_path = metadata_path if source_exists else canonical_source
        raise _storage_conflict(
            "Canonical document storage is incomplete.",
            content_sha256,
            conflict_path,
        )

    reconstructing = source_exists
    if reconstructing:
        _verify_source(canonical_source, content_sha256, byte_size)
        stored_sidecar = _load_sidecar(metadata_path, content_sha256)
        if (
            stored_sidecar["document_sha256"] != content_sha256
            or stored_sidecar["mime_type"] != PDF_MIME_TYPE
            or stored_sidecar["byte_size"] != byte_size
        ):
            raise _storage_conflict(
                "The canonical metadata conflicts with the canonical source.",
                content_sha256,
                metadata_path,
            )
        original_filename = stored_sidecar["original_filename"]
        source_url = stored_sidecar["source_url"]
        retrieved_at = stored_sidecar["retrieved_at"]
        registered_at = stored_sidecar["registered_at"]
        new_sidecar: Mapping[str, object] | None = None
        sidecar_bytes: bytes | None = None
    else:
        registered_at = _timestamp(clock())
        retrieved_at = None
        if registration.retrieved_at is not None:
            retrieved_at = _timestamp(registration.retrieved_at)
        original_filename = source_path.name
        source_url = registration.source_url
        new_sidecar = _sidecar(
            content_sha256=content_sha256,
            original_filename=original_filename,
            byte_size=byte_size,
            source_url=source_url,
            retrieved_at=retrieved_at,
            registered_at=registered_at,
        )
        sidecar_bytes = yaml.safe_dump(
            new_sidecar, sort_keys=False, allow_unicode=True
        ).encode("utf-8")

    created_directories: list[Path] = []
    created_source = False
    created_sidecar = False
    nested = connection.in_transaction
    transaction_started = False
    try:
        transaction_statement = (
            "SAVEPOINT register_document" if nested else "BEGIN IMMEDIATE"
        )
        connection.execute(transaction_statement)
        transaction_started = True
        # Recheck after acquiring the write lock.
        raced = get_document_by_sha256(connection, content_sha256)
        if raced is not None:
            if nested:
                connection.execute("RELEASE SAVEPOINT register_document")
            else:
                connection.commit()
            return register_pdf_document(connection, registration, clock=clock)

        if reconstructing:
            # The canonical pair is caller-owned during reconstruction. Revalidate
            # it only after holding the relational write lock, and require the
            # sidecar to be identical to the values validated before the lock.
            _verify_source(canonical_source, content_sha256, byte_size)
            current_sidecar = _load_sidecar(metadata_path, content_sha256)
            assert stored_sidecar is not None
            if current_sidecar != stored_sidecar:
                raise _storage_conflict(
                    "Canonical metadata changed during registration.",
                    content_sha256,
                    metadata_path,
                )
        else:
            # Recheck the pair after taking the database write lock and before
            # creating either canonical file.
            if _path_exists(canonical_source) or _path_exists(metadata_path):
                raise _storage_conflict(
                    "Canonical document storage changed during registration.",
                    content_sha256,
                    canonical_source.parent,
                )
            created_directories = _mkdirs(canonical_source.parent, vault_root)
            staged_source = _stage_copy(source_path, canonical_source.parent)
            created_source = _place_without_overwrite(staged_source, canonical_source)
            if not created_source:
                raise _storage_conflict(
                    "The canonical source appeared during registration.",
                    content_sha256,
                    canonical_source,
                )
            _verify_source(canonical_source, content_sha256, byte_size)
            assert sidecar_bytes is not None
            staged_sidecar = _stage_sidecar(sidecar_bytes, canonical_source.parent)
            created_sidecar = _place_without_overwrite(staged_sidecar, metadata_path)
            if not created_sidecar:
                raise _storage_conflict(
                    "The canonical metadata appeared during registration.",
                    content_sha256,
                    metadata_path,
                )
            assert new_sidecar is not None
            _verify_sidecar(metadata_path, new_sidecar, content_sha256)
            _fsync_directory(canonical_source.parent)

        artifact = register_artifact(
            connection,
            kind="source_document",
            storage_scope="vault",
            retention_class="canonical",
            relative_path=relative_source.as_posix(),
            sha256=content_sha256,
            mime_type=PDF_MIME_TYPE,
            byte_size=byte_size,
            created_at=registered_at,
        )
        document = register_document(
            connection,
            source_artifact_id=artifact.id,
            content_sha256=content_sha256,
            original_filename=original_filename,
            source_url=source_url,
            retrieved_at=retrieved_at,
            registered_at=registered_at,
        )
        if nested:
            connection.execute("RELEASE SAVEPOINT register_document")
        else:
            connection.commit()
    except Exception as error:
        if nested and transaction_started:
            try:
                connection.execute("ROLLBACK TO SAVEPOINT register_document")
            except Exception as cleanup_error:
                error.add_note(
                    "Rollback to the registration savepoint failed: "
                    f"{type(cleanup_error).__name__}"
                )
            try:
                connection.execute("RELEASE SAVEPOINT register_document")
            except Exception as cleanup_error:
                error.add_note(
                    "Release of the registration savepoint failed: "
                    f"{type(cleanup_error).__name__}"
                )
        elif transaction_started:
            try:
                connection.rollback()
            except Exception as cleanup_error:
                error.add_note(
                    "Rollback of the registration transaction failed: "
                    f"{type(cleanup_error).__name__}"
                )
        cleanup_errors: list[OSError] = []
        for created, path in (
            (created_sidecar, metadata_path),
            (created_source, canonical_source),
        ):
            if created:
                try:
                    path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    cleanup_errors.append(cleanup_error)
        cleanup_errors.extend(_remove_empty_directories(created_directories))
        for retained_cleanup_error in cleanup_errors:
            error.add_note(
                f"Cleanup of newly created canonical storage failed: "
                f"{type(retained_cleanup_error).__name__}"
            )
        if isinstance(error, DocumentRegistrationError):
            raise
        if isinstance(error, (ArtifactRepositoryError, DocumentRepositoryError)):
            raise DocumentRegistrationError(
                code="DOCUMENT_REGISTRATION_CONFLICT",
                message=(
                    "Relational document registration conflicts with existing data."
                ),
                details={"content_sha256": content_sha256},
            ) from error
        if isinstance(error, sqlite3.Error):
            raise DocumentRegistrationError(
                code="DOCUMENT_REGISTRATION_CONFLICT",
                message="Relational document registration failed.",
                details={"content_sha256": content_sha256},
            ) from error
        if isinstance(error, OSError):
            raise DocumentRegistrationError(
                code="DOCUMENT_STORAGE_CONFLICT",
                message="Canonical document storage failed.",
                details={"content_sha256": content_sha256},
            ) from error
        raise

    return DocumentRegistrationResult(
        document,
        artifact,
        (
            DocumentRegistrationStatus.ALREADY_REGISTERED
            if reconstructing
            else DocumentRegistrationStatus.REGISTERED
        ),
        canonical_source,
        metadata_path,
    )


__all__ = [
    "DocumentRegistrationInput",
    "DocumentRegistrationResult",
    "DocumentRegistrationStatus",
    "register_pdf_document",
]
