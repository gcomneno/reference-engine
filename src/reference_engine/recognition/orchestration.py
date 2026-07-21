"""Public application service for one durable recognition invocation."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from reference_engine.db import (
    Artifact,
    Document,
    RecognitionRun,
    get_active_document_model_versions,
    get_artifact,
    get_document,
    persist_completed_recognition_run,
)
from reference_engine.errors import (
    RecognitionOrchestrationError,
    RecognitionRepositoryError,
)
from reference_engine.recognition.outcomes import (
    evaluate_candidate,
    rank_and_select,
    serialize_run_snapshot,
)
from reference_engine.recognition.rules import parse_recognition_definition
from reference_engine.recognition.snapshots import project_safe_document_inputs
from reference_engine.recognition.types import (
    ActiveCandidateSnapshot,
    Availability,
    CapabilitySnapshot,
    InputValue,
    ProbeAcquisitionStatus,
    RecognitionCompletion,
    RecognitionProbeAcquisition,
    RecognitionRunSnapshot,
    RecognitionTextProbe,
    RunFailureCode,
    SafeConfigurationEntry,
    TechnicalDocumentInputs,
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+/-]*$")
_MIME = re.compile(r"^[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_STORED_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
_SUPPORTED_CAPABILITIES = frozenset(
    {"document_metadata.v1", "recognition_text_probe.v1"}
)


def _error(code: str) -> RecognitionOrchestrationError:
    messages = {
        "RECOGNITION_DOCUMENT_UNKNOWN": "The registered document is unknown.",
        "RECOGNITION_INVOCATION_INVALID": "The recognition invocation is invalid.",
        "RECOGNITION_SNAPSHOT_UNAVAILABLE": (
            "The recognition snapshot could not be resolved."
        ),
        "RECOGNITION_CLOCK_INVALID": "The recognition clock returned an invalid value.",
    }
    return RecognitionOrchestrationError(code, messages[code], details=None)


def _now(
    clock: Callable[[], datetime], earliest: datetime | None = None
) -> tuple[datetime, str]:
    try:
        value = clock()
        valid = isinstance(value, datetime) and value.tzinfo is not None
        if valid:
            offset = value.utcoffset()
            valid = offset is not None
        if not valid:
            raise ValueError
        normalized = value.astimezone(UTC)
        if earliest is not None and normalized < earliest:
            raise ValueError
    except Exception:
        raise _error("RECOGNITION_CLOCK_INVALID") from None
    return normalized, normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_context_fields(
    document_id: int,
    engine_version: str,
    capabilities: tuple[CapabilitySnapshot, ...],
    acquisition: RecognitionProbeAcquisition,
) -> tuple[CapabilitySnapshot, ...]:
    if (
        type(document_id) is not int
        or document_id <= 0
        or not isinstance(engine_version, str)
        or not _VERSION.fullmatch(engine_version)
        or len(engine_version) > 255
        or type(capabilities) is not tuple
        or not isinstance(acquisition, RecognitionProbeAcquisition)
        or not isinstance(acquisition.status, ProbeAcquisitionStatus)
    ):
        raise _error("RECOGNITION_INVOCATION_INVALID")
    seen: set[str] = set()
    for capability in capabilities:
        if (
            not isinstance(capability, CapabilitySnapshot)
            or not isinstance(capability.identifier, str)
            or not isinstance(capability.version, str)
            or type(capability.configuration) is not tuple
            or capability.identifier not in _SUPPORTED_CAPABILITIES
            or capability.identifier in seen
            or not _IDENTIFIER.fullmatch(capability.identifier)
            or not _VERSION.fullmatch(capability.version)
            or not isinstance(capability.availability, Availability)
        ):
            raise _error("RECOGNITION_INVOCATION_INVALID")
        seen.add(capability.identifier)
        names: set[str] = set()
        for entry in capability.configuration:
            if (
                not isinstance(entry, SafeConfigurationEntry)
                or not isinstance(entry.name, str)
                or not _IDENTIFIER.fullmatch(entry.name)
                or entry.name in names
                or not (
                    entry.value is None or isinstance(entry.value, (str, int, bool))
                )
            ):
                raise _error("RECOGNITION_INVOCATION_INVALID")
            names.add(entry.name)
        expected = (
            {"maximum_code_points"}
            if capability.identifier == "recognition_text_probe.v1"
            else set()
        )
        configuration = {item.name: item.value for item in capability.configuration}
        limit = configuration.get("maximum_code_points")
        if names != expected or (
            expected and (type(limit) is not int or not 1 <= limit <= 65536)
        ):
            raise _error("RECOGNITION_INVOCATION_INVALID")
    if seen != _SUPPORTED_CAPABILITIES:
        raise _error("RECOGNITION_INVOCATION_INVALID")

    probe_capability = next(
        item for item in capabilities if item.identifier == "recognition_text_probe.v1"
    )
    probe = acquisition.probe
    if acquisition.status is ProbeAcquisitionStatus.AVAILABLE_WITH_PROBE:
        configuration = {
            item.name: item.value for item in probe_capability.configuration
        }
        limit = cast(int, configuration["maximum_code_points"])
        if (
            not isinstance(probe, RecognitionTextProbe)
            or probe_capability.availability is not Availability.AVAILABLE
            or not isinstance(probe.text, str)
            or type(probe.limit) is not int
            or not 1 <= probe.limit <= limit
            or not isinstance(probe.truncated, bool)
            or not isinstance(probe.producer_identifier, str)
            or not _IDENTIFIER.fullmatch(probe.producer_identifier)
            or not isinstance(probe.producer_version, str)
            or not _VERSION.fullmatch(probe.producer_version)
            or probe.producer_identifier != probe_capability.identifier
            or probe.producer_version != probe_capability.version
            or probe.limit != limit
            or len(probe.text) > probe.limit
            or (probe.truncated and len(probe.text) != probe.limit)
        ):
            raise _error("RECOGNITION_INVOCATION_INVALID")
    elif probe is not None or (
        acquisition.status is ProbeAcquisitionStatus.ATTEMPT_FAILED
        and probe_capability.availability is not Availability.AVAILABLE
    ):
        raise _error("RECOGNITION_INVOCATION_INVALID")
    return capabilities


def _validate_context(
    document_id: int,
    engine_version: str,
    capabilities: tuple[CapabilitySnapshot, ...],
    acquisition: RecognitionProbeAcquisition,
) -> tuple[CapabilitySnapshot, ...]:
    try:
        return _validate_context_fields(
            document_id, engine_version, capabilities, acquisition
        )
    except RecognitionOrchestrationError:
        raise
    except Exception:
        # Caller-created dataclass instances can bypass generated constructors.
        raise _error("RECOGNITION_INVOCATION_INVALID") from None


def _inputs(
    document: Document,
    artifact: Artifact,
    probe: RecognitionProbeAcquisition,
) -> TechnicalDocumentInputs:
    # Attribute access is intentionally centralized after durable-shape validation.
    available = Availability.AVAILABLE
    return TechnicalDocumentInputs(
        InputValue(available, artifact.mime_type),
        InputValue(available, document.original_filename),
        InputValue(available, artifact.byte_size),
        InputValue(available, document.source_url),
        InputValue(available, document.retrieved_at),
        InputValue(available, document.published_date),
        InputValue(available, document.page_count),
        InputValue(available, document.registered_at),
        InputValue(available, document.content_sha256),
        probe,
    )


def _valid_durable(document: Document, artifact: Artifact) -> bool:
    try:
        registered = datetime.strptime(document.registered_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        retrieved_valid = document.retrieved_at is None or (
            isinstance(document.retrieved_at, str)
            and _STORED_UTC.fullmatch(document.retrieved_at) is not None
            and datetime.strptime(document.retrieved_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        )
        published_valid = document.published_date is None or (
            isinstance(document.published_date, str)
            and datetime.strptime(document.published_date, "%Y-%m-%d").strftime(
                "%Y-%m-%d"
            )
            == document.published_date
        )
        return bool(
            document.source_artifact_id == artifact.id
            and document.content_sha256 == artifact.sha256
            and artifact.kind == "source_document"
            and artifact.retention_class == "canonical"
            and isinstance(document.original_filename, str)
            and bool(document.original_filename)
            and "\x00" not in document.original_filename
            and isinstance(artifact.mime_type, str)
            and _MIME.fullmatch(artifact.mime_type.split(";", 1)[0].strip().lower())
            and type(artifact.byte_size) is int
            and artifact.byte_size >= 0
            and isinstance(document.content_sha256, str)
            and _HASH.fullmatch(document.content_sha256)
            and isinstance(document.registered_at, str)
            and _STORED_UTC.fullmatch(document.registered_at)
            and registered.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            == document.registered_at
            and retrieved_valid
            and published_valid
            and (document.source_url is None or isinstance(document.source_url, str))
            and (
                document.page_count is None
                or type(document.page_count) is int
                and document.page_count > 0
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _completion_time(clock: Callable[[], datetime], started: datetime) -> str:
    return _now(clock, started)[1]


def recognize_document(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    engine_version: str,
    capabilities: tuple[CapabilitySnapshot, ...],
    probe_acquisition: RecognitionProbeAcquisition,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RecognitionRun:
    """Recognize one registered document and append its complete durable run."""

    capabilities = _validate_context(
        document_id, engine_version, capabilities, probe_acquisition
    )
    started_datetime, started_at = _now(clock)
    caller_owns_transaction = connection.in_transaction
    transaction_started = False
    try:
        if not caller_owns_transaction:
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
        document = get_document(connection, document_id)
        if document is None:
            raise _error("RECOGNITION_DOCUMENT_UNKNOWN")

        snapshot: RecognitionRunSnapshot | None = None
        inputs: TechnicalDocumentInputs | None = None
        try:
            artifact = get_artifact(connection, document.source_artifact_id)
            if artifact is None or not _valid_durable(document, artifact):
                raise ValueError
            inputs = _inputs(document, artifact, probe_acquisition)
            versions = get_active_document_model_versions(connection)
            candidates = tuple(
                ActiveCandidateSnapshot(
                    item.id,
                    item.model_key,
                    item.semantic_version,
                    item.schema_version,
                    item.status,
                    item.definition_sha256,
                    item.definition_json,
                )
                for item in versions
            )
            snapshot = RecognitionRunSnapshot(
                "recognition-run-snapshot.v1",
                document.id,
                artifact.id,
                document.content_sha256,
                engine_version,
                capabilities,
                candidates,
                project_safe_document_inputs(inputs),
            )
        except RecognitionRepositoryError:
            raise
        except Exception:
            completion = RecognitionCompletion(
                document.id,
                engine_version,
                started_at,
                _completion_time(clock, started_datetime),
                None,
                failure_code=RunFailureCode.SNAPSHOT_FAILED,
            )
            result = persist_completed_recognition_run(connection, completion)
        else:
            try:
                _, snapshot_sha256 = serialize_run_snapshot(snapshot)
                evaluations = tuple(
                    evaluate_candidate(
                        candidate,
                        parse_recognition_definition(candidate.definition_json),
                        inputs,
                        capabilities,
                        snapshot_sha256,
                    )
                    for candidate in snapshot.candidates
                )
                ranking = rank_and_select(evaluations)
            except RecognitionRepositoryError:
                raise
            except Exception:
                completion = RecognitionCompletion(
                    document.id,
                    engine_version,
                    started_at,
                    _completion_time(clock, started_datetime),
                    snapshot,
                    inputs,
                    failure_code=RunFailureCode.EVALUATION_FAILED,
                )
            else:
                completion = RecognitionCompletion(
                    document.id,
                    engine_version,
                    started_at,
                    _completion_time(clock, started_datetime),
                    snapshot,
                    inputs,
                    ranking,
                )
            result = persist_completed_recognition_run(connection, completion)
        if transaction_started:
            try:
                connection.commit()
            except sqlite3.Error:
                raise RecognitionRepositoryError(
                    "RECOGNITION_REPOSITORY_ERROR",
                    "Recognition repository operation failed.",
                    details=None,
                ) from None
        return result
    except BaseException as original:
        if transaction_started:
            try:
                connection.rollback()
            except BaseException:
                pass
        raise original from None
