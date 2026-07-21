"""Atomic SQLite persistence for completed recognition invocations."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import cmp_to_key
from typing import Any, cast

from reference_engine.errors import RecognitionRepositoryError
from reference_engine.recognition.canonical import (
    canonical_json,
    validate_sha256,
)
from reference_engine.recognition.decimals import (
    ExactScore,
    add_decimals,
    canonical_decimal,
    compare_scores,
    display_score,
)
from reference_engine.recognition.outcomes import (
    complete_candidate,
    evaluate_candidate,
    rank_and_select,
    serialize_run_snapshot,
)
from reference_engine.recognition.rules import parse_recognition_definition
from reference_engine.recognition.snapshots import project_safe_document_inputs
from reference_engine.recognition.types import (
    Availability,
    CandidateEvaluation,
    MetadataExpectation,
    ProbeAcquisitionStatus,
    RankingResult,
    RecognitionCompletion,
    RecognitionDefinition,
    RecognitionRunSnapshot,
    RuleDefinition,
    RuleEvaluationStatus,
    RunFailureCode,
    RunOutcome,
    SafeString,
    SafeTextProbeSnapshot,
    TechnicalDocumentInputs,
)

_SAVEPOINTS = itertools.count()
_EVIDENCE_FIELDS = frozenset(
    {
        "candidate_state",
        "display_score",
        "eligible",
        "exact_score",
        "model",
        "recognition_evidence_schema",
        "required_rules_passed",
        "rules",
        "run_snapshot_sha256",
        "threshold",
    }
)
_DISPLAY_SCORE = re.compile(r"^(0|[1-9][0-9]*)\.[0-9]{6}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+/-]*$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$")
_RUN_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_MIME = re.compile(r"^[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+$")
_CANDIDATE_STATES = frozenset({"evaluated", "definitively_ineligible", "indeterminate"})
_SNAPSHOT_FIELDS = frozenset(
    {
        "capabilities",
        "candidates",
        "document_id",
        "document_sha256",
        "engine_version",
        "safe_document_inputs",
        "snapshot_schema_version",
        "source_artifact_id",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {
        "definition_sha256",
        "model_key",
        "model_version_id",
        "schema_version",
        "semantic_version",
        "status",
    }
)
_MODEL_FIELDS = frozenset(
    {"definition_sha256", "key", "semantic_version", "version_id"}
)
_RULE_FIELDS = frozenset(
    {
        "actual",
        "code",
        "expected",
        "id",
        "passed",
        "required",
        "score_contribution",
        "status",
        "type",
        "weight",
    }
)
_STATUSES = frozenset(
    {
        "evaluated_pass",
        "evaluated_fail",
        "unavailable_capability",
        "unavailable_input",
        "invalid_rule_definition",
        "technical_evaluation_error",
    }
)
_RULE_TYPES = frozenset(
    {
        "mime_type",
        "filename_regex",
        "text_contains",
        "text_regex",
        "page_count_between",
        "sha256",
        "metadata_equals",
        "invalid",
    }
)
_INVALID_CODES = frozenset(
    {
        "DEFINITION_JSON_INVALID",
        "RECOGNITION_DEFINITION_INVALID",
        "RULES_INVALID",
        "RULE_PROPERTIES_INVALID",
        "RULE_ID_INVALID",
        "RULE_REQUIRED_INVALID",
        "RULE_TYPE_OR_WEIGHT_INVALID",
        "REGEX_INVALID",
        "REGEX_FLAG_INVALID",
        "RULE_VALUE_INVALID",
        "RULE_INVALID",
    }
)
_TECHNICAL_CODES = frozenset(
    {
        "REGEX_RESOURCE_EXHAUSTED",
        "PROBE_ACQUISITION_FAILED",
        "HASH_INPUT_INVALID",
        "EVALUATOR_ERROR",
    }
)


@dataclass(frozen=True)
class _RecognitionResultWrite:
    """One candidate projection supplied as part of a complete run unit."""

    model_version_id: int
    score: float
    eligible: bool
    required_rules_passed: bool
    rank_position: int | None
    details_json: str
    details_sha256: str


@dataclass(frozen=True)
class _CompletedRecognitionRunWrite:
    """The complete, indivisible persistence input for one invocation."""

    document_id: int
    engine_version: str
    started_at: str
    completed_at: str
    outcome: RunOutcome
    error_code: str | None
    error_message: str | None
    input_snapshot_json: str | None
    input_snapshot_sha256: str | None
    results: tuple[_RecognitionResultWrite, ...]
    winner_model_version_id: int | None = None


@dataclass(frozen=True)
class RecognitionResult:
    id: int
    recognition_run_id: int
    model_version_id: int
    score: float
    eligible: bool
    required_rules_passed: bool
    rank_position: int | None
    details_json: str
    details_sha256: str
    model_definition_sha256: str
    candidate_state: str | None
    evidence_eligible: bool | None
    evidence_required_rules_passed: bool | None
    exact_score_numerator: str | None
    exact_score_denominator: str | None
    display_score: str | None
    threshold: str | None


@dataclass(frozen=True)
class RecognitionRun:
    id: int
    document_id: int
    engine_version: str
    started_at: str
    completed_at: str
    outcome: RunOutcome
    error_code: str | None
    error_message: str | None
    input_snapshot_json: str | None
    input_snapshot_sha256: str | None
    document_sha256: str | None
    results: tuple[RecognitionResult, ...]
    winner: RecognitionResult | None


def _invalid(message: str) -> RecognitionRepositoryError:
    return RecognitionRepositoryError(
        code="RECOGNITION_PERSISTENCE_INVALID",
        message="Recognition persistence data is invalid.",
        details=None,
    )


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _canonical_object(value: str, message: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value, object_pairs_hook=_object_pairs)
        if not isinstance(parsed, dict) or canonical_json(parsed) != value:
            raise ValueError("not a canonical JSON object")
    except (TypeError, ValueError):
        raise _invalid(message) from None
    return cast(dict[str, Any], parsed)


def _verify_json_hash(
    value: str, digest: str, *, object_message: str, hash_message: str
) -> dict[str, Any]:
    try:
        validate_sha256(digest)
    except ValueError:
        raise _invalid(hash_message) from None
    parsed = _canonical_object(value, object_message)
    actual = hashlib.sha256(value.encode("utf-8")).hexdigest()
    if actual != digest:
        raise _invalid("Canonical JSON hash does not match its exact UTF-8 bytes.")
    _validate_embedded_hashes(parsed)
    return parsed


def _validate_embedded_hashes(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith("sha256"):
                try:
                    validate_sha256(child)
                except ValueError:
                    raise _invalid("An embedded SHA-256 value is invalid.") from None
            _validate_embedded_hashes(child)
    elif isinstance(value, list):
        for child in value:
            _validate_embedded_hashes(child)


def _positive_integer(value: object, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise _invalid(message)
    return value


def _canonical_decimal_string(value: object, message: str) -> Decimal:
    if not isinstance(value, str):
        raise _invalid(message)
    try:
        parsed = Decimal(value)
        if canonical_decimal(parsed) != value:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise _invalid(message) from None
    return parsed


def _parse_canonical_date(value: str) -> date | None:
    if _DATE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed if parsed.strftime("%Y-%m-%d") == value else None


def _parse_canonical_utc(value: str, *, microseconds: bool) -> datetime | None:
    pattern = _RUN_UTC if microseconds else _UTC
    if pattern.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return None
    rendered = parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if not microseconds:
        rendered = f"{rendered[:-4]}Z"
    return parsed if rendered == value else None


def _unavailable(value: object) -> bool:
    return value == {"availability": "unavailable"}


def _digest_and_length(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"sha256", "length"}:
        return False
    try:
        validate_sha256(value.get("sha256"))
    except ValueError:
        return False
    return type(value.get("length")) is int and cast(int, value["length"]) >= 0


def _valid_mime(value: object) -> bool:
    return isinstance(value, str) and _MIME.fullmatch(value) is not None


def _validate_safe_scalar(kind: str, value: object, message: str) -> None:
    if value is None and kind in {
        "source_url",
        "retrieved_at",
        "published_date",
        "page_count",
    }:
        return
    if kind == "mime_type" and not _valid_mime(value):
        raise _invalid(message)
    if kind in {"byte_size", "page_count"} and (
        type(value) is not int or value < (1 if kind == "page_count" else 0)
    ):
        raise _invalid(message)
    if kind == "sha256":
        try:
            validate_sha256(value)
        except ValueError:
            raise _invalid(message) from None
    if kind == "published_date" and (
        not isinstance(value, str) or _parse_canonical_date(value) is None
    ):
        raise _invalid(message)
    if kind in {"retrieved_at", "registered_at"} and (
        not isinstance(value, str)
        or _parse_canonical_utc(value, microseconds=False) is None
    ):
        raise _invalid(message)
    if kind == "page_count_range":
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[1-9][0-9]*\.\.[1-9][0-9]*", value) is None
        ):
            raise _invalid(message)
        minimum, maximum = (int(item) for item in value.split(".."))
        if minimum > maximum:
            raise _invalid(message)


def _validate_snapshot(
    snapshot: dict[str, Any], write: _CompletedRecognitionRunWrite
) -> list[Any]:
    if set(snapshot) != _SNAPSHOT_FIELDS:
        raise _invalid("Input snapshot has invalid top-level fields.")
    if snapshot.get("snapshot_schema_version") != "recognition-run-snapshot.v1":
        raise _invalid("Snapshot schema identifier is not recognition v1.")
    _positive_integer(
        snapshot.get("document_id"), "Snapshot document identity is invalid."
    )
    _positive_integer(
        snapshot.get("source_artifact_id"),
        "Snapshot source artifact identity is invalid.",
    )
    if snapshot.get("document_id") != write.document_id:
        raise _invalid("Snapshot document identity differs from the run.")
    try:
        validate_sha256(snapshot.get("document_sha256"))
    except ValueError:
        raise _invalid("Snapshot document SHA-256 is invalid.") from None
    if snapshot.get("engine_version") != write.engine_version:
        raise _invalid("Snapshot engine version differs from the run.")

    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, list):
        raise _invalid("Snapshot capabilities are invalid.")
    seen_capabilities: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, dict) or set(capability) != {
            "availability",
            "configuration",
            "identifier",
            "version",
        }:
            raise _invalid("Snapshot capability is invalid.")
        identifier = capability.get("identifier")
        version = capability.get("version")
        availability = capability.get("availability")
        configuration = capability.get("configuration")
        if (
            identifier not in {"document_metadata.v1", "recognition_text_probe.v1"}
            or identifier in seen_capabilities
            or not isinstance(version, str)
            or _VERSION.fullmatch(version) is None
            or availability not in {"available", "unavailable"}
            or not isinstance(configuration, dict)
        ):
            raise _invalid("Snapshot capability is invalid.")
        seen_capabilities.add(cast(str, identifier))
        expected_configuration = (
            {"maximum_code_points"}
            if identifier == "recognition_text_probe.v1"
            else set()
        )
        if set(configuration) != expected_configuration:
            raise _invalid("Snapshot capability configuration is invalid.")
        if expected_configuration:
            limit = configuration.get("maximum_code_points")
            if type(limit) is not int or not 1 <= limit <= 65536:
                raise _invalid("Snapshot capability configuration is invalid.")

    safe_inputs = snapshot.get("safe_document_inputs")
    if not isinstance(safe_inputs, dict):
        raise _invalid("Snapshot safe document inputs are invalid.")
    scalar_fields = {
        "mime_type": (str, type(None)),
        "byte_size": (int, type(None)),
        "retrieved_at": (str, type(None)),
        "published_date": (str, type(None)),
        "page_count": (int, type(None)),
        "registered_at": (str, type(None)),
        "sha256": (str, type(None)),
    }
    allowed_inputs = {
        *scalar_fields,
        "original_filename",
        "source_url",
        "recognition_text_probe",
    }
    if not set(safe_inputs) <= allowed_inputs:
        raise _invalid("Snapshot safe document inputs are invalid.")
    for key, types in scalar_fields.items():
        if key not in safe_inputs:
            continue
        value = safe_inputs[key]
        if _unavailable(value):
            continue
        if not isinstance(value, types) or isinstance(value, bool):
            raise _invalid("Snapshot safe document input is invalid.")
        if key == "sha256" and value is not None:
            try:
                validate_sha256(value)
            except ValueError:
                raise _invalid("Snapshot safe document input is invalid.") from None
        if key == "mime_type" and value is not None and not _valid_mime(value):
            raise _invalid("Snapshot safe document input is invalid.")
        if key == "byte_size" and value is not None and cast(int, value) < 0:
            raise _invalid("Snapshot safe document input is invalid.")
        if key == "page_count" and value is not None and cast(int, value) <= 0:
            raise _invalid("Snapshot safe document input is invalid.")
        if (
            key == "published_date"
            and value is not None
            and _parse_canonical_date(cast(str, value)) is None
        ):
            raise _invalid("Snapshot safe document input is invalid.")
        if (
            key in {"retrieved_at", "registered_at"}
            and value is not None
            and _parse_canonical_utc(cast(str, value), microseconds=False) is None
        ):
            raise _invalid("Snapshot safe document input is invalid.")
    for key in ("original_filename", "source_url"):
        if key in safe_inputs and not (
            (key == "source_url" and safe_inputs[key] is None)
            or _unavailable(safe_inputs[key])
            or _digest_and_length(safe_inputs[key])
        ):
            raise _invalid("Snapshot safe document input is invalid.")
    if "recognition_text_probe" in safe_inputs:
        probe = safe_inputs["recognition_text_probe"]
        if not _unavailable(probe):
            if not isinstance(probe, dict) or set(probe) != {
                "character_count",
                "limit",
                "sha256",
                "truncated",
            }:
                raise _invalid("Snapshot recognition text probe is invalid.")
            try:
                validate_sha256(probe.get("sha256"))
            except ValueError:
                raise _invalid("Snapshot recognition text probe is invalid.") from None
            if (
                type(probe.get("character_count")) is not int
                or cast(int, probe["character_count"]) < 0
                or type(probe.get("limit")) is not int
                or not 1 <= cast(int, probe["limit"]) <= 65536
                or cast(int, probe["character_count"]) > cast(int, probe["limit"])
                or type(probe.get("truncated")) is not bool
            ):
                raise _invalid("Snapshot recognition text probe is invalid.")

    candidates = snapshot.get("candidates")
    if not isinstance(candidates, list):
        raise _invalid("Snapshot candidates must be a complete array.")
    identities: set[int] = set()
    ordering: list[tuple[str, str, int]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_FIELDS:
            raise _invalid("Snapshot candidate is invalid.")
        identifier = _positive_integer(
            candidate.get("model_version_id"), "Snapshot candidate identity is invalid."
        )
        schema = _positive_integer(
            candidate.get("schema_version"),
            "Snapshot candidate schema version is invalid.",
        )
        model_key = candidate.get("model_key")
        version = candidate.get("semantic_version")
        if (
            schema != 1
            or candidate.get("status") != "active"
            or not isinstance(model_key, str)
            or _IDENTIFIER.fullmatch(model_key) is None
            or not isinstance(version, str)
            or _VERSION.fullmatch(version) is None
        ):
            raise _invalid("Snapshot candidate is invalid.")
        try:
            validate_sha256(candidate.get("definition_sha256"))
        except ValueError:
            raise _invalid("Snapshot candidate is invalid.") from None
        if identifier in identities:
            raise _invalid("Snapshot candidate identities are duplicated.")
        identities.add(identifier)
        ordering.append((model_key, version, identifier))
    if ordering != sorted(ordering):
        raise _invalid("Snapshot candidates are not in deterministic order.")
    return candidates


@dataclass(frozen=True)
class _ValidatedResult:
    write: _RecognitionResultWrite
    evidence: dict[str, Any]
    state: str | None
    eligible: bool | None
    required_rules_passed: bool | None
    exact_score: ExactScore | None
    threshold: Decimal | None


@dataclass(frozen=True)
class _DurableDefinition:
    model_version_id: int
    model_key: str
    semantic_version: str
    schema_version: int
    definition_sha256: str
    definition_json: str
    parsed: RecognitionDefinition


def _load_durable_definitions(
    connection: sqlite3.Connection, candidates: list[Any]
) -> dict[int, _DurableDefinition]:
    result: dict[int, _DurableDefinition] = {}
    for candidate in candidates:
        identifier = cast(int, candidate["model_version_id"])
        row = connection.execute(
            """SELECT v.id, m.model_key, v.semantic_version, v.schema_version,
                      v.definition_json, v.definition_sha256
               FROM document_model_versions AS v
               JOIN document_models AS m ON m.id = v.document_model_id
               WHERE v.id = ?""",
            (identifier,),
        ).fetchone()
        if row is None:
            raise _invalid("Snapshot candidate has no durable model definition.")
        definition_json = cast(str, row["definition_json"])
        definition_sha256 = cast(str, row["definition_sha256"])
        try:
            validate_sha256(definition_sha256)
        except ValueError:
            raise _invalid("Durable model definition hash is invalid.") from None
        if (
            hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
            != definition_sha256
        ):
            raise _invalid(
                "Durable model definition hash differs from its exact bytes."
            )
        if (
            row["id"] != identifier
            or row["model_key"] != candidate["model_key"]
            or row["semantic_version"] != candidate["semantic_version"]
            or row["schema_version"] != candidate["schema_version"]
            or definition_sha256 != candidate["definition_sha256"]
        ):
            raise _invalid("Snapshot candidate differs from its durable definition.")
        result[identifier] = _DurableDefinition(
            identifier,
            cast(str, row["model_key"]),
            cast(str, row["semantic_version"]),
            cast(int, row["schema_version"]),
            definition_sha256,
            definition_json,
            parse_recognition_definition(definition_json),
        )
    return result


def _rule_type(rule: RuleDefinition) -> str:
    return rule.type.value if rule.type is not None else "invalid"


def _rule_input(rule: RuleDefinition) -> tuple[str, str]:
    rule_type = _rule_type(rule)
    if rule_type == "filename_regex":
        return "original_filename", "document_metadata.v1"
    if rule_type in {"text_contains", "text_regex"}:
        return "recognition_text_probe", "recognition_text_probe.v1"
    if rule_type == "page_count_between":
        return "page_count", "document_metadata.v1"
    if rule_type == "metadata_equals":
        assert isinstance(rule.value, MetadataExpectation)
        return rule.value.field, "document_metadata.v1"
    return rule_type, "document_metadata.v1"


def _snapshot_actual(kind: str, value: object) -> dict[str, Any]:
    if kind == "recognition_text_probe":
        probe = cast(dict[str, Any], value)
        return {
            "kind": kind,
            "length": probe["character_count"],
            "sha256": probe["sha256"],
        }
    if kind in {"original_filename", "source_url"} and isinstance(value, dict):
        return {"kind": kind, "length": value["length"], "sha256": value["sha256"]}
    return {"kind": kind, "value": value}


def _validate_rule_binding(
    raw: dict[str, Any],
    definition: RuleDefinition,
    snapshot: dict[str, Any],
) -> None:
    if (
        raw["id"] != definition.id
        or raw["type"] != _rule_type(definition)
        or raw["required"] is not definition.required
        or raw["weight"] != canonical_decimal(definition.weight)
    ):
        raise _invalid("Candidate rule evidence differs from its durable definition.")
    status = cast(str, raw["status"])
    if definition.invalid_code is not None or definition.type is None:
        if status != "invalid_rule_definition" or raw["code"] != (
            definition.invalid_code or "RULE_INVALID"
        ):
            raise _invalid(
                "Invalid definition evidence differs from its durable definition."
            )
        return
    if status == "invalid_rule_definition":
        raise _invalid("Candidate rule evidence contradicts its durable definition.")
    input_name, capability_name = _rule_input(definition)
    capabilities = cast(list[dict[str, Any]], snapshot["capabilities"])
    relevant_capabilities = tuple(
        item for item in capabilities if item["identifier"] == capability_name
    )
    if len(relevant_capabilities) != 1:
        raise _invalid("Durable snapshot omits a rule capability.")
    capability_available = relevant_capabilities[0]["availability"] == "available"
    inputs = cast(dict[str, Any], snapshot["safe_document_inputs"])
    present = input_name in inputs
    value = inputs.get(input_name)
    available_input = present and not _unavailable(value)
    nullable = (
        definition.type is not None
        and definition.type.value == "metadata_equals"
        and isinstance(definition.value, MetadataExpectation)
        and definition.value.field
        in {"source_url", "retrieved_at", "published_date", "page_count"}
    )
    evaluable = available_input and (value is not None or nullable)
    if status == "unavailable_capability":
        if capability_available:
            raise _invalid("Rule capability status differs from the durable snapshot.")
        return
    if status == "unavailable_input":
        if not capability_available or evaluable:
            raise _invalid("Rule input status differs from the durable snapshot.")
        return
    if status in {"evaluated_pass", "evaluated_fail"}:
        if not capability_available or not evaluable:
            raise _invalid("Evaluated rule contradicts the durable snapshot.")
        if raw["actual"] != _snapshot_actual(input_name, value):
            raise _invalid("Rule actual evidence differs from the durable snapshot.")


def _validate_safe_evidence(value: object, message: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or "kind" not in value:
        raise _invalid(message)
    kind = value.get("kind")
    if not isinstance(kind, str) or not _IDENTIFIER.fullmatch(kind):
        raise _invalid(message)
    if set(value) == {"kind", "value"}:
        scalar = value.get("value")
        if scalar is not None and type(scalar) not in {str, int, bool}:
            raise _invalid(message)
    elif set(value) == {"kind", "sha256", "length"} and _digest_and_length(
        {"sha256": value.get("sha256"), "length": value.get("length")}
    ):
        pass
    else:
        raise _invalid(message)
    sensitive = {
        "expected_text",
        "pattern",
        "original_filename",
        "source_url",
        "recognition_text_probe",
    }
    if kind in sensitive and "value" in value:
        if kind != "source_url" or value.get("value") is not None:
            raise _invalid(message)
    if kind not in {
        "mime_type",
        "original_filename",
        "recognition_text_probe",
        "page_count",
        "page_count_range",
        "sha256",
        "expected_text",
        "pattern",
        "source_url",
        "byte_size",
        "retrieved_at",
        "published_date",
        "registered_at",
    }:
        raise _invalid(message)
    if "value" in value:
        _validate_safe_scalar(kind, value["value"], message)
    return value


def _validate_rule_values(
    rule_type: str,
    status: str,
    expected: dict[str, Any] | None,
    actual: dict[str, Any] | None,
) -> None:
    if status == "invalid_rule_definition":
        return
    expected_kinds: dict[str, set[str]] = {
        "mime_type": {"mime_type"},
        "filename_regex": {"pattern"},
        "text_contains": {"expected_text"},
        "text_regex": {"pattern"},
        "page_count_between": {"page_count_range"},
        "sha256": {"sha256"},
        "metadata_equals": {
            "original_filename",
            "mime_type",
            "byte_size",
            "source_url",
            "retrieved_at",
            "published_date",
            "page_count",
            "registered_at",
        },
    }
    actual_kinds: dict[str, set[str]] = {
        "mime_type": {"mime_type"},
        "filename_regex": {"original_filename"},
        "text_contains": {"recognition_text_probe"},
        "text_regex": {"recognition_text_probe"},
        "page_count_between": {"page_count"},
        "sha256": {"sha256"},
        "metadata_equals": expected_kinds["metadata_equals"],
    }
    if rule_type not in expected_kinds or expected is None:
        raise _invalid("Candidate safe evidence value is invalid.")
    if expected["kind"] not in expected_kinds[rule_type]:
        raise _invalid("Candidate safe evidence value is invalid.")
    if actual is not None:
        if actual["kind"] not in actual_kinds[rule_type]:
            raise _invalid("Candidate safe evidence value is invalid.")
        if rule_type == "metadata_equals" and actual["kind"] != expected["kind"]:
            raise _invalid("Candidate safe evidence value is invalid.")
    for item in (expected, actual):
        if item is None or "value" not in item:
            continue
        kind, scalar = item["kind"], item["value"]
        _validate_safe_scalar(
            cast(str, kind), scalar, "Candidate safe evidence value is invalid."
        )


def _validate_rules(
    evidence: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, bool, bool]:
    rules = evidence.get("rules")
    if not isinstance(rules, list) or not rules:
        raise _invalid("Candidate rule evidence is invalid.")
    seen: set[str] = set()
    failed = unavailable = required_failure = False
    for raw in rules:
        if not isinstance(raw, dict) or set(raw) != _RULE_FIELDS:
            raise _invalid("Candidate rule evidence is invalid.")
        identifier = raw.get("id")
        rule_type = raw.get("type")
        status = raw.get("status")
        if (
            not isinstance(identifier, str)
            or _IDENTIFIER.fullmatch(identifier) is None
            or identifier in seen
            or rule_type not in _RULE_TYPES
            or status not in _STATUSES
            or type(raw.get("required")) is not bool
        ):
            raise _invalid("Candidate rule evidence is invalid.")
        seen.add(identifier)
        weight = _canonical_decimal_string(
            raw.get("weight"), "Candidate rule weight is invalid."
        )
        if weight > Decimal(1000000):
            raise _invalid("Candidate rule weight is invalid.")
        expected = _validate_safe_evidence(
            raw.get("expected"), "Candidate safe evidence value is invalid."
        )
        actual = _validate_safe_evidence(
            raw.get("actual"), "Candidate safe evidence value is invalid."
        )
        _validate_rule_values(cast(str, rule_type), cast(str, status), expected, actual)
        passed, code, contribution = (
            raw.get("passed"),
            raw.get("code"),
            raw.get("score_contribution"),
        )
        if status == "evaluated_pass":
            valid_status = passed is True and code is None
        elif status == "evaluated_fail":
            valid_status = passed is False and code is None
            required_failure |= raw["required"] is True
        elif status == "unavailable_capability":
            valid_status = (
                passed is None and code == "CAPABILITY_UNAVAILABLE" and actual is None
            )
            unavailable = True
        elif status == "unavailable_input":
            valid_status = (
                passed is None and code == "INPUT_UNAVAILABLE" and actual is None
            )
            unavailable = True
        elif status == "invalid_rule_definition":
            valid_status = (
                passed is None
                and code in _INVALID_CODES
                and expected is None
                and actual is None
            )
            failed = True
        else:
            valid_status = passed is None and code in _TECHNICAL_CODES
            failed = True
        if not valid_status:
            raise _invalid("Candidate rule status projections are invalid.")
        if contribution is not None and (
            not isinstance(contribution, str)
            or _DISPLAY_SCORE.fullmatch(contribution) is None
        ):
            raise _invalid("Candidate rule score contribution is invalid.")
        if (
            status not in {"evaluated_pass", "evaluated_fail"}
            and contribution is not None
        ):
            raise _invalid("Candidate rule score contribution is invalid.")
    return cast(list[dict[str, Any]], rules), failed, unavailable, required_failure


def _validate_evidence(
    result: _RecognitionResultWrite,
    snapshot_sha256: str,
    snapshot: dict[str, Any],
    durable: _DurableDefinition,
) -> _ValidatedResult:
    if type(result.model_version_id) is not int or result.model_version_id <= 0:
        raise _invalid("Result model identity must be a positive integer.")
    if type(result.score) is not float:
        raise _invalid("Result score projection must be a real Python float.")
    if not math.isfinite(result.score) or not 0.0 <= result.score <= 1.0:
        raise _invalid("Result score projection must be finite and in [0, 1].")
    if result.score == 0.0 and math.copysign(1.0, result.score) < 0:
        raise _invalid("Result score projection cannot be negative zero.")
    if (
        type(result.eligible) is not bool
        or type(result.required_rules_passed) is not bool
    ):
        raise _invalid("Relational evidence projections must be Boolean.")
    if result.rank_position is not None:
        _positive_integer(
            result.rank_position, "Result rank must be a positive integer."
        )

    evidence = _verify_json_hash(
        result.details_json,
        result.details_sha256,
        object_message="Candidate evidence must be a canonical JSON object.",
        hash_message="Candidate evidence hash must be lowercase SHA-256.",
    )
    if set(evidence) != _EVIDENCE_FIELDS:
        raise _invalid("Candidate evidence has invalid top-level fields.")
    if (
        evidence.get("recognition_evidence_schema")
        != "recognition-candidate-evidence.v1"
    ):
        raise _invalid("Candidate evidence schema identifier is not recognition v1.")
    if not isinstance(evidence.get("model"), dict):
        raise _invalid("Candidate evidence has invalid top-level field types.")
    model = cast(dict[str, Any], evidence["model"])
    if set(model) != _MODEL_FIELDS:
        raise _invalid("Candidate evidence model is invalid.")
    _positive_integer(model.get("version_id"), "Evidence model identity is invalid.")
    if (
        not isinstance(model.get("key"), str)
        or _IDENTIFIER.fullmatch(model["key"]) is None
        or not isinstance(model.get("semantic_version"), str)
        or _VERSION.fullmatch(model["semantic_version"]) is None
    ):
        raise _invalid("Candidate evidence model is invalid.")
    try:
        validate_sha256(model.get("definition_sha256"))
    except ValueError:
        raise _invalid("Candidate evidence model is invalid.") from None
    if evidence.get("run_snapshot_sha256") != snapshot_sha256:
        raise _invalid("Candidate evidence references a different run snapshot.")

    rules, failed_status, unavailable, required_failure = _validate_rules(evidence)
    if len(rules) != len(durable.parsed.rules):
        raise _invalid("Candidate rule evidence is incomplete for its definition.")
    for raw, definition_rule in zip(rules, durable.parsed.rules, strict=True):
        _validate_rule_binding(raw, definition_rule, snapshot)
    threshold_raw = evidence.get("threshold")
    threshold = None
    invalid_threshold = threshold_raw is None
    if threshold_raw is not None:
        threshold = _canonical_decimal_string(
            threshold_raw, "Candidate evidence threshold is invalid."
        )
        if threshold > 1:
            raise _invalid("Candidate evidence threshold is invalid.")
    if threshold != durable.parsed.threshold:
        raise _invalid("Candidate threshold differs from its durable definition.")
    if invalid_threshold and not any(
        rule["status"] == "invalid_rule_definition" for rule in rules
    ):
        raise _invalid("A null threshold requires invalid definition evidence.")
    failed_condition = failed_status or invalid_threshold

    expected_state: str | None
    if failed_condition:
        expected_state = None
    elif required_failure:
        expected_state = "definitively_ineligible"
    elif unavailable:
        expected_state = "indeterminate"
    else:
        expected_state = "evaluated"
    state = evidence.get("candidate_state")
    if state is not None and (
        not isinstance(state, str) or state not in _CANDIDATE_STATES
    ):
        raise _invalid("Candidate evidence state is invalid.")
    eligible = evidence.get("eligible")
    required = evidence.get("required_rules_passed")
    if eligible is not None and type(eligible) is not bool:
        raise _invalid("Candidate evidence eligibility is invalid.")
    if required is not None and type(required) is not bool:
        raise _invalid("Candidate evidence required-rules result is invalid.")
    if eligible is not result.eligible and not (
        eligible is None and result.eligible is False
    ):
        raise _invalid("Relational eligible projection differs from evidence.")
    if required is not result.required_rules_passed and not (
        required is None and result.required_rules_passed is False
    ):
        raise _invalid("Relational required-rules projection differs from evidence.")

    if state != expected_state:
        raise _invalid("Candidate evidence state differs from its rule evidence.")
    exact_raw = evidence.get("exact_score")
    shown = evidence.get("display_score")
    if (exact_raw is None) != (shown is None):
        raise _invalid("Exact and display scores must both be null or present.")
    exact: ExactScore | None = None
    if exact_raw is None:
        if result.score != 0.0:
            raise _invalid("A non-scoreable candidate requires the 0.0 sentinel score.")
    else:
        if not isinstance(exact_raw, dict) or set(exact_raw) != {
            "numerator",
            "denominator",
        }:
            raise _invalid("Candidate exact score is invalid.")
        numerator = _canonical_decimal_string(
            exact_raw.get("numerator"), "Candidate exact score is invalid."
        )
        denominator = _canonical_decimal_string(
            exact_raw.get("denominator"), "Candidate exact score is invalid."
        )
        if denominator <= 0 or numerator > denominator:
            raise _invalid("Candidate exact score is invalid.")
        exact = ExactScore(numerator, denominator)
        if not isinstance(shown, str) or _DISPLAY_SCORE.fullmatch(shown) is None:
            raise _invalid("Candidate display score is invalid.")
        if display_score(exact) != shown:
            raise _invalid("Candidate display score differs from its exact score.")
        if Decimal(str(result.score)) != Decimal(shown):
            raise _invalid("Relational score projection differs from evidence.")

    if state is None:
        if any(value is not None for value in (eligible, required, exact_raw, shown)):
            raise _invalid("Failed candidate evidence projections must be null.")
    elif state == "evaluated":
        if eligible is not True or required is not True or exact is None:
            raise _invalid("Evaluated candidate evidence is inconsistent.")
    elif state == "definitively_ineligible":
        if eligible is not False or required is not False or exact is not None:
            raise _invalid("Ineligible candidate evidence is inconsistent.")
    elif eligible is not None or required not in (None, True) or exact is not None:
        raise _invalid("Indeterminate candidate evidence is inconsistent.")
    expected_required: bool | None
    if state is None:
        expected_required = None
    elif required_failure:
        expected_required = False
    elif any(
        rule["required"]
        and rule["status"] in {"unavailable_capability", "unavailable_input"}
        for rule in rules
    ):
        expected_required = None
    else:
        expected_required = True
    expected_eligible = (
        True
        if state == "evaluated"
        else False
        if state == "definitively_ineligible"
        else None
    )
    if required is not expected_required or eligible is not expected_eligible:
        raise _invalid("Candidate evidence projections differ from its rule evidence.")
    denominator = add_decimals(
        tuple(
            _canonical_decimal_string(
                rule["weight"], "Candidate rule weight is invalid."
            )
            for rule in rules
        )
    )
    if exact is not None:
        expected_numerator = add_decimals(
            tuple(
                _canonical_decimal_string(
                    rule["weight"], "Candidate rule weight is invalid."
                )
                if rule["passed"] is True
                else Decimal(0)
                for rule in rules
            )
        )
        if exact.numerator != expected_numerator or exact.denominator != denominator:
            raise _invalid("Candidate exact score differs from its rule evidence.")
    for rule in rules:
        contribution = rule["score_contribution"]
        if exact is None:
            if contribution is not None:
                raise _invalid("Candidate rule score contribution is invalid.")
        else:
            expected_contribution = display_score(
                ExactScore(
                    _canonical_decimal_string(
                        rule["weight"], "Candidate rule weight is invalid."
                    )
                    if rule["passed"] is True
                    else Decimal(0),
                    denominator,
                )
            )
            if contribution != expected_contribution:
                raise _invalid("Candidate rule score contribution is invalid.")
    if (
        state != "evaluated" or eligible is not True
    ) and result.rank_position is not None:
        raise _invalid("Only evaluated eligible candidates may have ranks.")
    return _ValidatedResult(
        result, evidence, state, eligible, required, exact, threshold
    )


def _validate(
    connection: sqlite3.Connection, write: _CompletedRecognitionRunWrite
) -> None:
    _positive_integer(
        write.document_id, "Run document identity must be a positive integer."
    )
    if write.winner_model_version_id is not None:
        _positive_integer(
            write.winner_model_version_id,
            "Winner model identity must be a positive integer.",
        )
    if not isinstance(write.outcome, RunOutcome):
        raise _invalid("Run outcome is not a recognition-v1 outcome.")
    started_at = (
        _parse_canonical_utc(write.started_at, microseconds=True)
        if isinstance(write.started_at, str)
        else None
    )
    completed_at = (
        _parse_canonical_utc(write.completed_at, microseconds=True)
        if isinstance(write.completed_at, str)
        else None
    )
    if started_at is None or completed_at is None or completed_at < started_at:
        raise _invalid("A completed run requires ordered completion timestamps.")
    if (write.input_snapshot_json is None) != (write.input_snapshot_sha256 is None):
        raise _invalid("Snapshot JSON and SHA-256 must both be null or non-null.")
    if write.outcome is not RunOutcome.FAILED and write.input_snapshot_json is None:
        raise _invalid("Only a failed pre-snapshot run may omit its snapshot.")
    if write.outcome is RunOutcome.FAILED:
        if (
            not isinstance(write.error_code, str)
            or not write.error_code
            or not isinstance(write.error_message, str)
            or not write.error_message
        ):
            raise _invalid("A failed run requires fixed error fields.")
    elif write.error_code is not None or write.error_message is not None:
        raise _invalid("Only failed runs may persist error fields.")

    snapshot: dict[str, Any] | None = None
    candidates: list[Any] | None = None
    if write.input_snapshot_json is not None:
        assert write.input_snapshot_sha256 is not None
        snapshot = _verify_json_hash(
            write.input_snapshot_json,
            write.input_snapshot_sha256,
            object_message="Input snapshot must be a canonical JSON object.",
            hash_message="Input snapshot hash must be lowercase SHA-256.",
        )
        candidates = _validate_snapshot(snapshot, write)

    if snapshot is None and write.results:
        raise _invalid("Pre-snapshot failed runs cannot contain results.")
    if candidates is not None:
        candidate_ids = [candidate["model_version_id"] for candidate in candidates]
        failed_without_results = (
            write.outcome is RunOutcome.FAILED and not write.results
        )
        result_ids = tuple(result.model_version_id for result in write.results)
        if not failed_without_results and result_ids != tuple(candidate_ids):
            raise _invalid(
                "Results do not preserve the complete snapshot candidate order."
            )
    durable = (
        _load_durable_definitions(connection, candidates)
        if candidates is not None
        else {}
    )
    validated = tuple(
        _validate_evidence(
            result,
            cast(str, write.input_snapshot_sha256),
            cast(dict[str, Any], snapshot),
            durable[result.model_version_id],
        )
        for result in write.results
    )

    if candidates is not None:
        by_id = {candidate["model_version_id"]: candidate for candidate in candidates}
        for item in validated:
            result, evidence = item.write, item.evidence
            model = cast(dict[str, Any], evidence["model"])
            if model.get("version_id") != result.model_version_id:
                raise _invalid("Evidence model identity differs from its result.")
            candidate = by_id[result.model_version_id]
            if (
                model.get("definition_sha256") != candidate.get("definition_sha256")
                or model.get("key") != candidate.get("model_key")
                or model.get("semantic_version") != candidate.get("semantic_version")
            ):
                raise _invalid("Evidence model identity differs from the snapshot.")
            durable_model = durable[result.model_version_id]
            if (
                model.get("definition_sha256") != durable_model.definition_sha256
                or model.get("key") != durable_model.model_key
                or model.get("semantic_version") != durable_model.semantic_version
            ):
                raise _invalid(
                    "Evidence model identity differs from its durable definition."
                )

    failed = tuple(item for item in validated if item.state is None)
    indeterminate = tuple(item for item in validated if item.state == "indeterminate")
    if failed or indeterminate:
        if any(item.write.rank_position is not None for item in validated):
            raise _invalid("Failed and indeterminate result sets cannot be ranked.")
    else:
        scoreable = tuple(item for item in validated if item.exact_score is not None)

        def compare(left: _ValidatedResult, right: _ValidatedResult) -> int:
            assert left.exact_score is not None and right.exact_score is not None
            return -compare_scores(left.exact_score, right.exact_score)

        expected = sorted(scoreable, key=cmp_to_key(compare))
        expected_ranks = {
            item.write.model_version_id: rank for rank, item in enumerate(expected, 1)
        }
        if any(
            item.write.rank_position != expected_ranks.get(item.write.model_version_id)
            for item in validated
        ):
            raise _invalid(
                "Result ranks are inconsistent with exact scores and snapshot order."
            )

    qualifiers = tuple(
        item
        for item in validated
        if item.exact_score is not None
        and item.threshold is not None
        and item.exact_score.meets(item.threshold)
    )
    top_qualifiers: tuple[_ValidatedResult, ...] = ()
    if qualifiers:
        highest = qualifiers[0]
        assert highest.exact_score is not None
        for candidate in qualifiers[1:]:
            assert candidate.exact_score is not None
            if compare_scores(candidate.exact_score, highest.exact_score) > 0:
                highest = candidate
        highest_score = highest.exact_score
        assert highest_score is not None
        top_qualifiers = tuple(
            item
            for item in qualifiers
            if item.exact_score is not None
            and compare_scores(item.exact_score, highest_score) == 0
        )

    winner = write.winner_model_version_id
    if write.outcome is RunOutcome.FAILED:
        if winner is not None or (validated and not failed):
            raise _invalid("Failed run evidence or winner is inconsistent.")
    elif write.outcome is RunOutcome.UNSUPPORTED:
        if failed or not indeterminate or winner is not None:
            raise _invalid("Unsupported run evidence or winner is inconsistent.")
    elif write.outcome is RunOutcome.AMBIGUOUS:
        if failed or indeterminate or len(top_qualifiers) < 2 or winner is not None:
            raise _invalid("Ambiguous run evidence or winner is inconsistent.")
    elif write.outcome is RunOutcome.MATCHED:
        if failed or indeterminate or len(top_qualifiers) != 1:
            raise _invalid("Matched run evidence is inconsistent.")
        expected_winner = top_qualifiers[0]
        if (
            winner != expected_winner.write.model_version_id
            or expected_winner.eligible is not True
            or expected_winner.write.rank_position != 1
        ):
            raise _invalid("Matched run winner is inconsistent.")
    elif failed or indeterminate or qualifiers or winner is not None:
        raise _invalid("Not-matched run evidence or winner is inconsistent.")


_FAILURE_MESSAGES = {
    RunFailureCode.SNAPSHOT_FAILED: "Recognition snapshot creation failed.",
    RunFailureCode.ORCHESTRATION_FAILED: "Recognition orchestration failed.",
    RunFailureCode.EVALUATION_FAILED: "Recognition evaluation failed.",
}


def _validate_context_projection(
    snapshot: RecognitionRunSnapshot, inputs: TechnicalDocumentInputs
) -> None:
    projected = dict(snapshot.safe_document_inputs.fields)
    canonical_projection = dict(project_safe_document_inputs(inputs).fields)
    for name, safe_value in projected.items():
        expected_value = canonical_projection[name]
        if isinstance(expected_value, SafeString):
            expected: object = {
                "length": expected_value.length,
                "sha256": expected_value.sha256,
            }
        elif isinstance(expected_value, SafeTextProbeSnapshot):
            expected = {
                "character_count": expected_value.character_count,
                "limit": expected_value.limit,
                "sha256": expected_value.sha256,
                "truncated": expected_value.truncated,
            }
        else:
            expected = expected_value
        if isinstance(safe_value, SafeString):
            actual: object = {
                "length": safe_value.length,
                "sha256": safe_value.sha256,
            }
        elif isinstance(safe_value, SafeTextProbeSnapshot):
            actual = {
                "character_count": safe_value.character_count,
                "limit": safe_value.limit,
                "sha256": safe_value.sha256,
                "truncated": safe_value.truncated,
            }
        else:
            actual = safe_value
        if actual != expected:
            raise _invalid("Safe snapshot differs from immutable evaluation inputs.")

    required_inputs: set[str] = set()
    for candidate in snapshot.candidates:
        definition = parse_recognition_definition(candidate.definition_json)
        for rule in definition.rules:
            if rule.type is not None:
                required_inputs.add(_rule_input(rule)[0])
    if not required_inputs <= projected.keys():
        raise _invalid("Safe snapshot omits an evaluated input.")

    probe_capabilities = tuple(
        item
        for item in snapshot.capabilities
        if item.identifier == "recognition_text_probe.v1"
    )
    acquisition = inputs.recognition_text_probe
    if acquisition.status is ProbeAcquisitionStatus.AVAILABLE_WITH_PROBE:
        probe = acquisition.probe
        if probe is None or len(probe_capabilities) != 1:
            raise _invalid("Recognition probe context is invalid.")
        capability = probe_capabilities[0]
        configuration = {entry.name: entry.value for entry in capability.configuration}
        if (
            capability.availability is not Availability.AVAILABLE
            or probe.producer_identifier != capability.identifier
            or probe.producer_version != capability.version
            or configuration.get("maximum_code_points") != probe.limit
            or len(probe.text) > probe.limit
        ):
            raise _invalid("Recognition probe context is invalid.")


def _technical_rule(
    supplied: Any, regenerated: Any, definition: RuleDefinition
) -> None:
    if supplied.status is not RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR:
        raise _invalid("Rule evidence differs from deterministic evaluation.")
    if (
        supplied.id != definition.id
        or supplied.type != _rule_type(definition)
        or supplied.required is not definition.required
        or supplied.weight != definition.weight
        or supplied.passed is not None
        or supplied.expected != regenerated.expected
    ):
        raise _invalid("Technical rule evidence differs from its definition.")
    allowed: dict[str, set[str]] = {
        "REGEX_RESOURCE_EXHAUSTED": {"filename_regex", "text_regex"},
        "PROBE_ACQUISITION_FAILED": {"text_contains", "text_regex"},
        "HASH_INPUT_INVALID": {"sha256"},
        "EVALUATOR_ERROR": {
            "mime_type",
            "filename_regex",
            "text_contains",
            "text_regex",
            "page_count_between",
            "sha256",
            "metadata_equals",
        },
    }
    if supplied.code not in allowed or supplied.type not in allowed[supplied.code]:
        raise _invalid("Technical error code is not applicable to this rule.")
    if supplied.code == "REGEX_RESOURCE_EXHAUSTED":
        if supplied.actual != regenerated.actual:
            raise _invalid("Technical rule actual evidence is invalid.")
    elif supplied.actual is not None:
        raise _invalid("Technical rule actual evidence is invalid.")


def _derive_ranking(
    completion: RecognitionCompletion,
    snapshot_sha256: str,
    durable: dict[int, _DurableDefinition],
) -> Any:
    assert completion.snapshot is not None
    assert completion.inputs is not None
    assert completion.ranking is not None
    supplied_by_id = {
        item.candidate.model_version_id: item for item in completion.ranking.evaluations
    }
    if len(supplied_by_id) != len(completion.ranking.evaluations):
        raise _invalid("Completion contains duplicate candidates.")
    derived: list[CandidateEvaluation] = []
    for candidate in completion.snapshot.candidates:
        durable_definition = durable[candidate.model_version_id]
        definition = durable_definition.parsed
        supplied = supplied_by_id.get(candidate.model_version_id)
        if supplied is None or supplied.candidate != candidate:
            raise _invalid("Completion candidate set differs from the snapshot.")
        regenerated = evaluate_candidate(
            candidate,
            definition,
            completion.inputs,
            completion.snapshot.capabilities,
            snapshot_sha256,
        )
        if any(
            rule.status is RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR
            for rule in supplied.rules
        ):
            if len(supplied.rules) != len(definition.rules):
                raise _invalid("Completion rule set is incomplete.")
            rebuilt_rules = []
            for claimed, actual, rule_definition in zip(
                supplied.rules, regenerated.rules, definition.rules, strict=True
            ):
                if claimed.status is RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR:
                    _technical_rule(claimed, actual, rule_definition)
                    rebuilt_rules.append(claimed)
                elif claimed != actual:
                    raise _invalid(
                        "Rule evidence differs from deterministic evaluation."
                    )
                else:
                    rebuilt_rules.append(actual)
            regenerated = complete_candidate(
                candidate, definition, tuple(rebuilt_rules), snapshot_sha256
            )
        elif supplied.rules != regenerated.rules:
            raise _invalid("Rule evidence differs from deterministic evaluation.")
        derived.append(regenerated)
    ranking = rank_and_select(tuple(derived))
    if completion.ranking != ranking:
        raise _invalid("Completion ranking differs from deterministic ranking.")
    return ranking


def _prepare_write(
    connection: sqlite3.Connection, completion: RecognitionCompletion
) -> _CompletedRecognitionRunWrite:
    if not isinstance(completion, RecognitionCompletion):
        raise _invalid("Persistence requires a typed recognition completion.")
    if completion.snapshot is not None and not isinstance(
        completion.snapshot, RecognitionRunSnapshot
    ):
        raise _invalid("Completion snapshot is invalid.")
    if completion.inputs is not None and not isinstance(
        completion.inputs, TechnicalDocumentInputs
    ):
        raise _invalid("Completion inputs are invalid.")
    if completion.ranking is not None and not isinstance(
        completion.ranking, RankingResult
    ):
        raise _invalid("Completion ranking is invalid.")
    if completion.failure_code is not None and not isinstance(
        completion.failure_code, RunFailureCode
    ):
        raise _invalid("Completion failure code is invalid.")
    if completion.snapshot is None:
        if completion.inputs is not None or completion.ranking is not None:
            raise _invalid("A pre-snapshot failure cannot contain evaluation data.")
        if completion.failure_code is not RunFailureCode.SNAPSHOT_FAILED:
            raise _invalid("A pre-snapshot failure code is invalid.")
        return _CompletedRecognitionRunWrite(
            completion.document_id,
            completion.engine_version,
            completion.started_at,
            completion.completed_at,
            RunOutcome.FAILED,
            completion.failure_code.value,
            _FAILURE_MESSAGES[completion.failure_code],
            None,
            None,
            (),
        )
    if (
        completion.snapshot.document_id != completion.document_id
        or completion.snapshot.engine_version != completion.engine_version
    ):
        raise _invalid("Completion metadata differs from its snapshot.")
    snapshot_json, snapshot_sha256 = serialize_run_snapshot(completion.snapshot)
    snapshot_dict = cast(dict[str, Any], json.loads(snapshot_json))
    candidates = _validate_snapshot(
        snapshot_dict,
        _CompletedRecognitionRunWrite(
            completion.document_id,
            completion.engine_version,
            completion.started_at,
            completion.completed_at,
            RunOutcome.FAILED,
            "x",
            "x",
            snapshot_json,
            snapshot_sha256,
            (),
        ),
    )
    durable = _load_durable_definitions(connection, candidates)
    for candidate in completion.snapshot.candidates:
        row = durable[candidate.model_version_id]
        definition_digest = hashlib.sha256(
            candidate.definition_json.encode("utf-8")
        ).hexdigest()
        if definition_digest != candidate.definition_sha256:
            raise _invalid("Candidate definition bytes do not match their hash.")
        if (
            row.definition_sha256 != candidate.definition_sha256
            or row.definition_json != candidate.definition_json
        ):
            raise _invalid("Candidate definition differs from durable definition.")
    if completion.ranking is None:
        if completion.inputs is not None:
            _validate_context_projection(completion.snapshot, completion.inputs)
        if completion.failure_code not in {
            RunFailureCode.ORCHESTRATION_FAILED,
            RunFailureCode.EVALUATION_FAILED,
        }:
            raise _invalid("Post-snapshot failure code is invalid.")
        return _CompletedRecognitionRunWrite(
            completion.document_id,
            completion.engine_version,
            completion.started_at,
            completion.completed_at,
            RunOutcome.FAILED,
            completion.failure_code.value,
            _FAILURE_MESSAGES[completion.failure_code],
            snapshot_json,
            snapshot_sha256,
            (),
        )
    if completion.inputs is None or completion.failure_code is not None:
        raise _invalid("A completed evaluation requires inputs and no run failure.")
    _validate_context_projection(completion.snapshot, completion.inputs)
    ranking = _derive_ranking(completion, snapshot_sha256, durable)
    results = tuple(
        _RecognitionResultWrite(
            item.candidate.model_version_id,
            0.0 if item.display_score is None else float(item.display_score),
            item.eligible is True,
            item.required_rules_passed is True,
            item.rank_position,
            item.evidence_json,
            item.evidence_sha256,
        )
        for item in ranking.evaluations
    )
    error_code = (
        RunFailureCode.EVALUATION_FAILED.value
        if ranking.outcome is RunOutcome.FAILED
        else None
    )
    return _CompletedRecognitionRunWrite(
        completion.document_id,
        completion.engine_version,
        completion.started_at,
        completion.completed_at,
        ranking.outcome,
        error_code,
        _FAILURE_MESSAGES[RunFailureCode.EVALUATION_FAILED]
        if error_code is not None
        else None,
        snapshot_json,
        snapshot_sha256,
        results,
        ranking.winner.candidate.model_version_id
        if ranking.winner is not None
        else None,
    )


def _result(row: sqlite3.Row) -> RecognitionResult:
    details = row["details_json"]
    evidence = cast(dict[str, Any], json.loads(details))
    model = cast(dict[str, Any], evidence["model"])
    exact = evidence.get("exact_score")
    numerator = denominator = None
    if isinstance(exact, dict):
        numerator = cast(str, exact["numerator"])
        denominator = cast(str, exact["denominator"])
    return RecognitionResult(
        row["id"],
        row["recognition_run_id"],
        row["model_version_id"],
        row["score"],
        bool(row["eligible"]),
        bool(row["required_rules_passed"]),
        row["rank_position"],
        details,
        hashlib.sha256(details.encode("utf-8")).hexdigest(),
        cast(str, model["definition_sha256"]),
        cast(str | None, evidence.get("candidate_state")),
        cast(bool | None, evidence.get("eligible")),
        cast(bool | None, evidence.get("required_rules_passed")),
        numerator,
        denominator,
        cast(str | None, evidence.get("display_score")),
        cast(str | None, evidence.get("threshold")),
    )


def _get_recognition_results_unchecked(
    connection: sqlite3.Connection, recognition_run_id: int
) -> tuple[RecognitionResult, ...]:
    rows = connection.execute(
        """SELECT id, recognition_run_id, model_version_id, score, eligible,
                  required_rules_passed, rank_position, details_json
           FROM recognition_results WHERE recognition_run_id = ? ORDER BY id""",
        (recognition_run_id,),
    )
    try:
        return tuple(_result(row) for row in rows)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise _invalid("Durable recognition result data is invalid.") from None


def get_recognition_results(
    connection: sqlite3.Connection, recognition_run_id: int
) -> tuple[RecognitionResult, ...]:
    try:
        run = _get_recognition_run(connection, recognition_run_id)
        return () if run is None else run.results
    except RecognitionRepositoryError:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        raise RecognitionRepositoryError(
            code="RECOGNITION_REPOSITORY_ERROR",
            message="Recognition repository operation failed.",
            details=None,
        ) from None


def _get_recognition_run(
    connection: sqlite3.Connection, recognition_run_id: int
) -> RecognitionRun | None:
    row = connection.execute(
        """SELECT id, document_id, engine_version, started_at, completed_at, outcome,
                  error_code, error_message, input_snapshot_json, input_snapshot_sha256
           FROM recognition_runs WHERE id = ?""",
        (recognition_run_id,),
    ).fetchone()
    if row is None or row["completed_at"] is None:
        return None
    results = _get_recognition_results_unchecked(connection, recognition_run_id)
    rank_one = tuple(item for item in results if item.rank_position == 1)
    winner = (
        rank_one[0]
        if row["outcome"] == RunOutcome.MATCHED and len(rank_one) == 1
        else None
    )
    snapshot = row["input_snapshot_json"]
    document_sha256 = None
    if snapshot is not None:
        try:
            document_sha256 = cast(str, json.loads(snapshot)["document_sha256"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise _invalid("Durable recognition snapshot data is invalid.") from None
    try:
        outcome = RunOutcome(row["outcome"])
    except ValueError:
        raise _invalid("Durable recognition run outcome is invalid.") from None
    run = RecognitionRun(
        row["id"],
        row["document_id"],
        row["engine_version"],
        row["started_at"],
        row["completed_at"],
        outcome,
        row["error_code"],
        row["error_message"],
        row["input_snapshot_json"],
        row["input_snapshot_sha256"],
        document_sha256,
        results,
        winner,
    )
    write = _CompletedRecognitionRunWrite(
        run.document_id,
        run.engine_version,
        run.started_at,
        run.completed_at,
        run.outcome,
        run.error_code,
        run.error_message,
        run.input_snapshot_json,
        run.input_snapshot_sha256,
        tuple(
            _RecognitionResultWrite(
                item.model_version_id,
                item.score,
                item.eligible,
                item.required_rules_passed,
                item.rank_position,
                item.details_json,
                item.details_sha256,
            )
            for item in results
        ),
        winner.model_version_id if winner is not None else None,
    )
    _validate(connection, write)
    return run


def get_recognition_run(
    connection: sqlite3.Connection, recognition_run_id: int
) -> RecognitionRun | None:
    try:
        return _get_recognition_run(connection, recognition_run_id)
    except RecognitionRepositoryError:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        raise RecognitionRepositoryError(
            code="RECOGNITION_REPOSITORY_ERROR",
            message="Recognition repository operation failed.",
            details=None,
        ) from None


def _persist(
    connection: sqlite3.Connection,
    value: RecognitionCompletion | _CompletedRecognitionRunWrite,
) -> RecognitionRun:
    nested = connection.in_transaction
    savepoint = f"recognition_run_{id(connection):x}_{next(_SAVEPOINTS):x}"
    try:
        connection.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
        if isinstance(value, RecognitionCompletion):
            try:
                write = _prepare_write(connection, value)
            except RecognitionRepositoryError:
                raise
            except (AttributeError, TypeError, ValueError, KeyError):
                raise _invalid("Completion object graph is invalid.") from None
        else:
            write = value
        _validate(connection, write)
        cursor = connection.execute(
            """INSERT INTO recognition_runs
               (document_id, engine_version, started_at, completed_at, outcome,
                error_code, error_message, input_snapshot_json, input_snapshot_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                write.document_id,
                write.engine_version,
                write.started_at,
                write.completed_at,
                write.outcome.value,
                write.error_code,
                write.error_message,
                write.input_snapshot_json,
                write.input_snapshot_sha256,
            ),
        )
        run_id = cursor.lastrowid
        assert run_id is not None
        for item in write.results:
            connection.execute(
                """INSERT INTO recognition_results
                   (recognition_run_id, model_version_id, score, eligible,
                    required_rules_passed, rank_position, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    item.model_version_id,
                    item.score,
                    int(item.eligible),
                    int(item.required_rules_passed),
                    item.rank_position,
                    item.details_json,
                ),
            )
        if nested:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.commit()
    except BaseException as original:
        if nested:
            rolled_back = False
            try:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            except BaseException:
                pass
            else:
                rolled_back = True
            if rolled_back:
                try:
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException:
                    pass
        else:
            try:
                connection.rollback()
            except BaseException:
                pass
        if isinstance(original, RecognitionRepositoryError):
            raise original from None
        if isinstance(original, (sqlite3.Error, ValueError, TypeError, KeyError)):
            raise RecognitionRepositoryError(
                code="RECOGNITION_REPOSITORY_ERROR",
                message="Recognition repository operation failed.",
                details=None,
            ) from None
        raise
    persisted = get_recognition_run(connection, run_id)
    assert persisted is not None
    return persisted


def _persist_completed_recognition_write(
    connection: sqlite3.Connection, write: _CompletedRecognitionRunWrite
) -> RecognitionRun:
    """Private compatibility seam used to test the low-level validator."""

    return _persist(connection, write)


def persist_completed_recognition_run(
    connection: sqlite3.Connection, completion: RecognitionCompletion
) -> RecognitionRun:
    """Derive and atomically append one immutable domain completion."""

    if not isinstance(completion, RecognitionCompletion):
        raise _invalid("Persistence requires a typed recognition completion.")
    try:
        return _persist(connection, completion)
    except RecognitionRepositoryError:
        raise
    except sqlite3.Error:
        raise RecognitionRepositoryError(
            code="RECOGNITION_REPOSITORY_ERROR",
            message="Recognition repository operation failed.",
            details=None,
        ) from None
