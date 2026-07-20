"""Persisted definition parsing and side-effect-free rule evaluators."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from reference_engine.recognition.canonical import string_digest, validate_sha256
from reference_engine.recognition.decimals import (
    RecognitionDecimalError,
    validate_threshold,
    validate_weight,
)
from reference_engine.recognition.types import (
    Availability,
    CapabilitySnapshot,
    InputValue,
    MetadataExpectation,
    PageCountRange,
    ProbeAcquisitionStatus,
    RecognitionDefinition,
    RuleDefinition,
    RuleEvaluationStatus,
    RuleEvidence,
    RuleType,
    SafeEvidenceValue,
    TechnicalDocumentInputs,
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_MIME = re.compile(r"^[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+$")
_HASH = re.compile(r"^[0-9A-Fa-f]{64}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$")
_FIELDS = frozenset({"id", "type", "value", "required", "weight"})
_SELECTORS = frozenset(
    {
        "original_filename",
        "mime_type",
        "byte_size",
        "source_url",
        "retrieved_at",
        "published_date",
        "page_count",
        "registered_at",
    }
)


def _invalid(index: int, raw: object, code: str) -> RuleDefinition:
    identifier = raw.get("id") if isinstance(raw, Mapping) else None
    return RuleDefinition(
        str(identifier) if isinstance(identifier, str) else f"invalid-{index}",
        None,
        None,
        False,
        Decimal(0),
        code,
    )


def parse_recognition_definition(definition_json: str) -> RecognitionDefinition:
    """Parse persisted JSON with exact decimals, retaining rule declaration order."""

    try:
        root = json.loads(
            definition_json,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, ValueError):
        return RecognitionDefinition(
            None,
            (_invalid(0, None, "DEFINITION_JSON_INVALID"),),
            "DEFINITION_JSON_INVALID",
        )
    if not isinstance(root, Mapping) or not isinstance(
        root.get("recognition"), Mapping
    ):
        return RecognitionDefinition(
            None,
            (_invalid(0, None, "RECOGNITION_DEFINITION_INVALID"),),
            "RECOGNITION_DEFINITION_INVALID",
        )
    recognition = cast(Mapping[object, object], root["recognition"])
    try:
        threshold = validate_threshold(recognition.get("minimum_score"))
    except RecognitionDecimalError:
        threshold = None
    policy_valid = recognition.get("ambiguity_policy", "reject") == "reject"
    raw_rules = recognition.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        return RecognitionDefinition(
            threshold,
            (_invalid(0, raw_rules, "RULES_INVALID"),),
            "RECOGNITION_DEFINITION_INVALID",
        )
    rules: list[RuleDefinition] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, Mapping) or set(raw) != _FIELDS:
            rules.append(_invalid(index, raw, "RULE_PROPERTIES_INVALID"))
            continue
        identifier, type_value = raw.get("id"), raw.get("type")
        required = raw.get("required")
        if (
            not isinstance(identifier, str)
            or not _IDENTIFIER.fullmatch(identifier)
            or identifier in seen
        ):
            rules.append(_invalid(index, raw, "RULE_ID_INVALID"))
            continue
        seen.add(identifier)
        if not isinstance(required, bool):
            rules.append(_invalid(index, raw, "RULE_REQUIRED_INVALID"))
            continue
        try:
            if not isinstance(type_value, str):
                raise ValueError
            rule_type = RuleType(type_value)
            weight = validate_weight(raw.get("weight"))
        except (ValueError, RecognitionDecimalError):
            rules.append(
                RuleDefinition(
                    identifier,
                    None,
                    None,
                    required,
                    Decimal(0),
                    "RULE_TYPE_OR_WEIGHT_INVALID",
                )
            )
            continue
        raw_value = raw.get("value")
        code = _validate_value(rule_type, raw_value)
        value = _immutable_value(rule_type, raw_value) if code is None else None
        rules.append(
            RuleDefinition(identifier, rule_type, value, required, weight, code)
        )
    invalid = (
        None
        if threshold is not None and policy_valid
        else "RECOGNITION_DEFINITION_INVALID"
    )
    if invalid is not None and not any(rule.invalid_code for rule in rules):
        first = rules[0]
        rules[0] = RuleDefinition(
            first.id, first.type, first.value, first.required, first.weight, invalid
        )
    return RecognitionDefinition(threshold, tuple(rules), invalid)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object member")
        result[key] = value
    return result


def _regex_invalid(pattern: object) -> str | None:
    if not isinstance(pattern, str) or not pattern or len(pattern) > 4096:
        return "REGEX_INVALID"
    for match in re.finditer(r"\(\?([A-Za-z-]+)(?=[:)])", pattern):
        flags = match.group(1).replace("-", "")
        if any(flag not in "aimsx" for flag in flags):
            return "REGEX_FLAG_INVALID"
    try:
        re.compile(pattern)
    except (re.error, OverflowError):
        return "REGEX_INVALID"
    return None


def _valid_metadata_expected(field: object, expected: object) -> bool:
    if field not in _SELECTORS:
        return False
    if field == "original_filename":
        return isinstance(expected, str)
    if field == "mime_type":
        return (
            isinstance(expected, str)
            and _MIME.fullmatch(_normalize_mime(expected)) is not None
        )
    if field == "byte_size":
        return (
            isinstance(expected, Decimal) and expected == expected.to_integral_value()
        )
    if field == "page_count":
        return (
            expected is None
            or isinstance(expected, Decimal)
            and expected == expected.to_integral_value()
            and expected > 0
        )
    if field == "source_url":
        return expected is None or isinstance(expected, str)
    if field == "published_date":
        return expected is None or isinstance(expected, str) and _valid_date(expected)
    if field == "retrieved_at":
        return expected is None or isinstance(expected, str) and _valid_utc(expected)
    return isinstance(expected, str) and _valid_utc(expected)


def _valid_date(value: str) -> bool:
    if _DATE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_utc(value: str) -> bool:
    if _UTC.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") == value


def _validate_value(rule_type: RuleType, value: object) -> str | None:
    if rule_type is RuleType.MIME_TYPE:
        return (
            None
            if isinstance(value, str) and _MIME.fullmatch(value)
            else "RULE_VALUE_INVALID"
        )
    if rule_type in {RuleType.FILENAME_REGEX, RuleType.TEXT_REGEX}:
        return _regex_invalid(value)
    if rule_type is RuleType.TEXT_CONTAINS:
        return None if isinstance(value, str) and value else "RULE_VALUE_INVALID"
    if rule_type is RuleType.PAGE_COUNT_BETWEEN:
        if not isinstance(value, Mapping) or set(value) != {"minimum", "maximum"}:
            return "RULE_VALUE_INVALID"
        minimum, maximum = value.get("minimum"), value.get("maximum")
        valid = all(
            isinstance(item, Decimal) and item == item.to_integral_value()
            for item in (minimum, maximum)
        )
        return (
            None
            if valid
            and cast(Decimal, minimum) >= 1
            and cast(Decimal, minimum) <= cast(Decimal, maximum)
            else "RULE_VALUE_INVALID"
        )
    if rule_type is RuleType.SHA256:
        return (
            None
            if isinstance(value, str) and _HASH.fullmatch(value)
            else "RULE_VALUE_INVALID"
        )
    if not isinstance(value, Mapping) or set(value) != {"field", "expected"}:
        return "RULE_VALUE_INVALID"
    return (
        None
        if _valid_metadata_expected(value.get("field"), value.get("expected"))
        else "RULE_VALUE_INVALID"
    )


def _immutable_value(
    rule_type: RuleType, value: object
) -> str | PageCountRange | MetadataExpectation:
    if rule_type is RuleType.PAGE_COUNT_BETWEEN:
        range_item = cast(Mapping[str, Decimal], value)
        return PageCountRange(int(range_item["minimum"]), int(range_item["maximum"]))
    if rule_type is RuleType.METADATA_EQUALS:
        item = cast(Mapping[str, object], value)
        field = cast(str, item["field"])
        expected = item["expected"]
        if isinstance(expected, Decimal):
            expected = int(expected)
        if field == "mime_type" and isinstance(expected, str):
            expected = _normalize_mime(expected)
        return MetadataExpectation(field, cast(str | int | bool | None, expected))
    result = cast(str, value)
    return result.lower() if rule_type is RuleType.SHA256 else result


def _safe(kind: str, value: object, *, sensitive: bool = False) -> SafeEvidenceValue:
    if sensitive and isinstance(value, str):
        digest, length = string_digest(value)
        return SafeEvidenceValue(kind, sha256=digest, length=length)
    return SafeEvidenceValue(kind, cast(str | int | bool | None, value))


def _capability_available(
    capabilities: tuple[CapabilitySnapshot, ...], identifier: str
) -> bool:
    return any(
        item.identifier == identifier and item.availability is Availability.AVAILABLE
        for item in capabilities
    )


def evaluate_rule(
    rule: RuleDefinition,
    inputs: TechnicalDocumentInputs,
    capabilities: tuple[CapabilitySnapshot, ...],
) -> RuleEvidence:
    if rule.invalid_code or rule.type is None:
        return RuleEvidence(
            rule.id,
            rule.type.value if rule.type else "invalid",
            rule.required,
            rule.weight,
            RuleEvaluationStatus.INVALID_RULE_DEFINITION,
            None,
            None,
            None,
            rule.invalid_code or "RULE_INVALID",
        )
    capability = (
        "recognition_text_probe.v1"
        if rule.type in {RuleType.TEXT_CONTAINS, RuleType.TEXT_REGEX}
        else "document_metadata.v1"
    )
    if not _capability_available(capabilities, capability):
        return RuleEvidence(
            rule.id,
            rule.type.value,
            rule.required,
            rule.weight,
            RuleEvaluationStatus.UNAVAILABLE_CAPABILITY,
            None,
            _expected(rule),
            None,
            "CAPABILITY_UNAVAILABLE",
        )
    source, kind, sensitive, technical_code = _source(rule, inputs)
    if technical_code is not None:
        return RuleEvidence(
            rule.id,
            rule.type.value,
            rule.required,
            rule.weight,
            RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR,
            None,
            _expected(rule),
            None,
            technical_code,
        )
    if source is None or source.availability is Availability.UNAVAILABLE:
        return RuleEvidence(
            rule.id,
            rule.type.value,
            rule.required,
            rule.weight,
            RuleEvaluationStatus.UNAVAILABLE_INPUT,
            None,
            _expected(rule),
            None,
            "INPUT_UNAVAILABLE",
        )
    if source.value is None and not _nullable_input(rule):
        return RuleEvidence(
            rule.id,
            rule.type.value,
            rule.required,
            rule.weight,
            RuleEvaluationStatus.UNAVAILABLE_INPUT,
            None,
            _expected(rule),
            None,
            "INPUT_UNAVAILABLE",
        )
    if rule.type is RuleType.SHA256:
        try:
            validate_sha256(source.value, "sha256 input")
        except ValueError:
            return RuleEvidence(
                rule.id,
                rule.type.value,
                rule.required,
                rule.weight,
                RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR,
                None,
                _expected(rule),
                None,
                "HASH_INPUT_INVALID",
            )
    try:
        passed = _matches(rule, source.value)
    except (MemoryError, RecursionError, TimeoutError, KeyboardInterrupt):
        return RuleEvidence(
            rule.id,
            rule.type.value,
            rule.required,
            rule.weight,
            RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR,
            None,
            _expected(rule),
            _safe(kind, source.value, sensitive=sensitive)
            if isinstance(source.value, (str, int))
            else None,
            "REGEX_RESOURCE_EXHAUSTED",
        )
    except Exception:
        return RuleEvidence(
            rule.id,
            rule.type.value,
            rule.required,
            rule.weight,
            RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR,
            None,
            _expected(rule),
            None,
            "EVALUATOR_ERROR",
        )
    status = (
        RuleEvaluationStatus.EVALUATED_PASS
        if passed
        else RuleEvaluationStatus.EVALUATED_FAIL
    )
    return RuleEvidence(
        rule.id,
        rule.type.value,
        rule.required,
        rule.weight,
        status,
        passed,
        _expected(rule),
        _safe(kind, source.value, sensitive=sensitive),
    )


def _source(
    rule: RuleDefinition, inputs: TechnicalDocumentInputs
) -> tuple[InputValue | None, str, bool, str | None]:
    if rule.type is RuleType.MIME_TYPE:
        item = inputs.mime_type
        if item.availability is Availability.AVAILABLE and isinstance(item.value, str):
            item = InputValue(item.availability, _normalize_mime(item.value))
        return item, "mime_type", False, None
    if rule.type is RuleType.FILENAME_REGEX:
        item = inputs.original_filename
        if item.availability is Availability.AVAILABLE and isinstance(item.value, str):
            basename = re.split(r"[/\\\\]", item.value)[-1]
            item = InputValue(item.availability, basename)
        return item, "original_filename", True, None
    if rule.type in {RuleType.TEXT_CONTAINS, RuleType.TEXT_REGEX}:
        acquisition = inputs.recognition_text_probe
        if acquisition.status is ProbeAcquisitionStatus.ATTEMPT_FAILED:
            return None, "recognition_text_probe", True, "PROBE_ACQUISITION_FAILED"
        probe = acquisition.probe
        return (
            None if probe is None else InputValue(Availability.AVAILABLE, probe.text),
            "recognition_text_probe",
            True,
            None,
        )
    if rule.type is RuleType.PAGE_COUNT_BETWEEN:
        return inputs.page_count, "page_count", False, None
    if rule.type is RuleType.SHA256:
        item = inputs.sha256
        if item.availability is Availability.AVAILABLE and isinstance(item.value, str):
            item = InputValue(item.availability, item.value.lower())
        return item, "sha256", False, None
    assert isinstance(rule.value, MetadataExpectation)
    field = rule.value.field
    item = cast(InputValue, getattr(inputs, field))
    if (
        field == "mime_type"
        and item.availability is Availability.AVAILABLE
        and isinstance(item.value, str)
    ):
        item = InputValue(item.availability, _normalize_mime(item.value))
    return (
        item,
        field,
        field in {"original_filename", "source_url"},
        None,
    )


def _nullable_input(rule: RuleDefinition) -> bool:
    return (
        rule.type is RuleType.METADATA_EQUALS
        and isinstance(rule.value, MetadataExpectation)
        and rule.value.field
        in {"source_url", "retrieved_at", "published_date", "page_count"}
    )


def _expected(rule: RuleDefinition) -> SafeEvidenceValue:
    if rule.type in {RuleType.TEXT_CONTAINS, RuleType.TEXT_REGEX}:
        return _safe(
            "expected_text" if rule.type is RuleType.TEXT_CONTAINS else "pattern",
            rule.value,
            sensitive=True,
        )
    if rule.type is RuleType.FILENAME_REGEX:
        return _safe("pattern", rule.value, sensitive=True)
    if rule.type is RuleType.PAGE_COUNT_BETWEEN:
        assert isinstance(rule.value, PageCountRange)
        return _safe("page_count_range", f"{rule.value.minimum}..{rule.value.maximum}")
    if rule.type is RuleType.METADATA_EQUALS:
        assert isinstance(rule.value, MetadataExpectation)
        field, expected = rule.value.field, rule.value.expected
        return _safe(
            field,
            expected,
            sensitive=field in {"original_filename", "source_url"}
            and isinstance(expected, str),
        )
    assert rule.type is not None
    return _safe(rule.type.value, rule.value)


def _matches(rule: RuleDefinition, actual: object) -> bool:
    if rule.type is RuleType.MIME_TYPE:
        return isinstance(actual, str) and _normalize_mime(actual) == rule.value
    if rule.type in {RuleType.FILENAME_REGEX, RuleType.TEXT_REGEX}:
        return (
            isinstance(actual, str)
            and re.search(cast(str, rule.value), actual) is not None
        )
    if rule.type is RuleType.TEXT_CONTAINS:
        return isinstance(actual, str) and cast(str, rule.value) in actual
    if rule.type is RuleType.PAGE_COUNT_BETWEEN:
        assert isinstance(rule.value, PageCountRange)
        return (
            isinstance(actual, int)
            and not isinstance(actual, bool)
            and rule.value.minimum <= actual <= rule.value.maximum
        )
    if rule.type is RuleType.SHA256:
        return isinstance(actual, str) and actual == rule.value
    assert isinstance(rule.value, MetadataExpectation)
    expected = rule.value.expected
    field = rule.value.field
    if field == "mime_type" and isinstance(actual, str):
        actual = _normalize_mime(actual)
    return type(actual) is type(expected) and actual == expected


def _normalize_mime(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()
