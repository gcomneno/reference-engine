from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from reference_engine.recognition.rules import (
    evaluate_rule,
    parse_recognition_definition,
)
from reference_engine.recognition.types import (
    Availability,
    CapabilitySnapshot,
    InputValue,
    ProbeAcquisitionStatus,
    RecognitionProbeAcquisition,
    RecognitionTextProbe,
    RuleEvaluationStatus,
    TechnicalDocumentInputs,
)


def _inputs() -> TechnicalDocumentInputs:
    available = Availability.AVAILABLE
    return TechnicalDocumentInputs(
        InputValue(available, "Application/PDF; charset=binary"),
        InputValue(available, "private-report.pdf"),
        InputValue(available, 123),
        InputValue(available, None),
        InputValue(available, "2026-01-01T00:00:00.000Z"),
        InputValue(available, "2026-01-01"),
        InputValue(available, 3),
        InputValue(available, "2026-01-02T00:00:00.000Z"),
        InputValue(available, "a" * 64),
        RecognitionProbeAcquisition(
            ProbeAcquisitionStatus.AVAILABLE_WITH_PROBE,
            RecognitionTextProbe("Secret marker heading", 100, False, "synthetic", "1"),
        ),
    )


def _definition(rule: dict[str, object]) -> str:
    return json.dumps(
        {
            "recognition": {
                "minimum_score": 1,
                "ambiguity_policy": "reject",
                "rules": [rule],
            }
        }
    )


@pytest.mark.parametrize(
    ("rule_type", "value"),
    [
        ("mime_type", "application/pdf"),
        ("filename_regex", r"report\.pdf$"),
        ("text_contains", "marker"),
        ("text_regex", r"(?i)secret"),
        ("page_count_between", {"minimum": 2, "maximum": 4}),
        ("sha256", "a" * 64),
        ("metadata_equals", {"field": "source_url", "expected": None}),
    ],
)
def test_every_rule_type_passes_and_can_fail(rule_type: str, value: object) -> None:
    raw = {
        "id": "rule",
        "type": rule_type,
        "value": value,
        "required": True,
        "weight": 1,
    }
    rule = parse_recognition_definition(_definition(raw)).rules[0]
    caps = (
        CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),
        CapabilitySnapshot("recognition_text_probe.v1", "1", Availability.AVAILABLE),
    )
    assert (
        evaluate_rule(rule, _inputs(), caps).status
        is RuleEvaluationStatus.EVALUATED_PASS
    )


def test_capability_and_input_unavailability_are_distinct() -> None:
    raw = {
        "id": "mime",
        "type": "mime_type",
        "value": "application/pdf",
        "required": True,
        "weight": 1,
    }
    rule = parse_recognition_definition(_definition(raw)).rules[0]
    assert (
        evaluate_rule(rule, _inputs(), ()).status
        is RuleEvaluationStatus.UNAVAILABLE_CAPABILITY
    )
    inputs = _inputs()
    missing = replace(inputs, mime_type=InputValue(Availability.UNAVAILABLE))
    assert (
        evaluate_rule(
            rule,
            missing,
            (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
        ).status
        is RuleEvaluationStatus.UNAVAILABLE_INPUT
    )


@pytest.mark.parametrize(
    ("rule_type", "value", "field"),
    [
        ("mime_type", "application/pdf", "mime_type"),
        ("filename_regex", "pdf$", "original_filename"),
        ("page_count_between", {"minimum": 1, "maximum": 2}, "page_count"),
        ("sha256", "a" * 64, "sha256"),
        (
            "metadata_equals",
            {"field": "registered_at", "expected": "2026-01-02T00:00:00.000Z"},
            "registered_at",
        ),
    ],
)
def test_null_non_nullable_rule_input_is_unavailable(
    rule_type: str, value: object, field: str
) -> None:
    rule = parse_recognition_definition(
        _definition(
            {
                "id": "null-input",
                "type": rule_type,
                "value": value,
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    null_input = InputValue(Availability.AVAILABLE, None)
    inputs = _inputs()
    if field == "mime_type":
        inputs = replace(inputs, mime_type=null_input)
    elif field == "original_filename":
        inputs = replace(inputs, original_filename=null_input)
    elif field == "page_count":
        inputs = replace(inputs, page_count=null_input)
    elif field == "sha256":
        inputs = replace(inputs, sha256=null_input)
    else:
        assert field == "registered_at"
        inputs = replace(inputs, registered_at=null_input)
    evidence = evaluate_rule(
        rule,
        inputs,
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
    )
    assert evidence.status is RuleEvaluationStatus.UNAVAILABLE_INPUT
    assert evidence.passed is None and evidence.actual is None


def test_nullable_metadata_null_remains_an_evaluated_value() -> None:
    rule = parse_recognition_definition(
        _definition(
            {
                "id": "nullable",
                "type": "metadata_equals",
                "value": {"field": "source_url", "expected": None},
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    evidence = evaluate_rule(
        rule,
        _inputs(),
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
    )
    assert evidence.status is RuleEvaluationStatus.EVALUATED_PASS
    assert evidence.actual is not None and evidence.actual.value is None


@pytest.mark.parametrize(
    ("rule_type", "value"),
    [
        ("unknown", "x"),
        ("metadata_equals", {"field": "publisher", "expected": "x"}),
        ("page_count_between", {"minimum": True, "maximum": 2}),
        ("text_regex", "(?L)x"),
        ("text_regex", "["),
        ("text_regex", "x" * 4097),
    ],
)
def test_invalid_definitions_are_rule_evidence(rule_type: str, value: object) -> None:
    raw = {
        "id": "rule",
        "type": rule_type,
        "value": value,
        "required": True,
        "weight": 1,
    }
    definition = _definition(raw)
    rule = parse_recognition_definition(definition).rules[0]
    evidence = evaluate_rule(rule, _inputs(), ())
    assert evidence.status is RuleEvaluationStatus.INVALID_RULE_DEFINITION


def test_sensitive_evidence_is_hashed() -> None:
    raw = {
        "id": "text",
        "type": "text_contains",
        "value": "marker",
        "required": True,
        "weight": 1,
    }
    rule = parse_recognition_definition(_definition(raw)).rules[0]
    evidence = evaluate_rule(
        rule,
        _inputs(),
        (CapabilitySnapshot("recognition_text_probe.v1", "1", Availability.AVAILABLE),),
    )
    assert evidence.expected is not None and evidence.expected.value is None
    assert evidence.actual is not None and evidence.actual.value is None
    assert evidence.expected.sha256 and evidence.actual.sha256


@pytest.mark.parametrize(
    ("rule_type", "value"),
    [
        ("mime_type", "text/plain"),
        ("filename_regex", r"never\.txt$"),
        ("text_contains", "absent"),
        ("text_regex", r"^absent$"),
        ("page_count_between", {"minimum": 4, "maximum": 5}),
        ("sha256", "b" * 64),
        ("metadata_equals", {"field": "source_url", "expected": "https://x"}),
    ],
)
def test_every_rule_type_has_an_explicit_evaluated_failure(
    rule_type: str, value: object
) -> None:
    rule = parse_recognition_definition(
        _definition(
            {
                "id": "rule",
                "type": rule_type,
                "value": value,
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    caps = (
        CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),
        CapabilitySnapshot("recognition_text_probe.v1", "1", Availability.AVAILABLE),
    )
    assert (
        evaluate_rule(rule, _inputs(), caps).status
        is RuleEvaluationStatus.EVALUATED_FAIL
    )


@pytest.mark.parametrize(
    "definition",
    [
        '{"recognition":{"minimum_score":-0,"rules":[{"id":"x","type":"mime_type","value":"application/pdf","required":true,"weight":1}]}}',
        '{"recognition":{"minimum_score":1,"rules":[{"id":"x","type":"mime_type","value":"application/pdf","required":true,"weight":-0}]}}',
    ],
)
def test_integer_negative_zero_is_invalid(definition: str) -> None:
    parsed = parse_recognition_definition(definition)
    assert parsed.invalid_code or parsed.rules[0].invalid_code


@pytest.mark.parametrize(
    "definition",
    [
        '{"recognition":{"minimum_score":1,"rules":[{"id":"x","id":"y","type":"mime_type","value":"application/pdf","required":true,"weight":1}]}}',
        '{"recognition":{"minimum_score":1,"rules":[{"id":"x","type":"mime_type","value":"application/pdf","required":true,"weight":1,"weight":2}]}}',
        '{"recognition":{"minimum_score":1,"rules":[{"id":"x","type":"metadata_equals","value":{"field":"mime_type","field":"source_url","expected":"x"},"required":true,"weight":1}]}}',
    ],
)
def test_duplicate_json_members_are_invalid_at_every_level(definition: str) -> None:
    assert (
        parse_recognition_definition(definition).invalid_code
        == "DEFINITION_JSON_INVALID"
    )


def test_filename_regex_uses_portable_basename_and_hashes_only_basename() -> None:
    rule = parse_recognition_definition(
        _definition(
            {
                "id": "file",
                "type": "filename_regex",
                "value": "secret-dir",
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    inputs = replace(
        _inputs(),
        original_filename=InputValue(Availability.AVAILABLE, r"secret-dir\\public.pdf"),
    )
    evidence = evaluate_rule(
        rule,
        inputs,
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
    )
    assert evidence.status is RuleEvaluationStatus.EVALUATED_FAIL
    assert evidence.actual is not None
    assert evidence.actual.sha256 == hashlib.sha256(b"public.pdf").hexdigest()
    assert "secret-dir" not in repr(evidence) and "public.pdf" not in repr(evidence)


def test_sha256_and_metadata_mime_are_normalized_in_comparison_and_evidence() -> None:
    sha = parse_recognition_definition(
        _definition(
            {
                "id": "sha",
                "type": "sha256",
                "value": "A" * 64,
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    sha_evidence = evaluate_rule(
        sha,
        replace(_inputs(), sha256=InputValue(Availability.AVAILABLE, "A" * 64)),
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
    )
    assert (
        sha_evidence.passed
        and sha_evidence.expected is not None
        and sha_evidence.actual is not None
    )
    assert sha_evidence.expected.value == sha_evidence.actual.value == "a" * 64

    mime = parse_recognition_definition(
        _definition(
            {
                "id": "mime",
                "type": "metadata_equals",
                "value": {
                    "field": "mime_type",
                    "expected": "Application/PDF; Charset=UTF-8",
                },
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    mime_evidence = evaluate_rule(
        mime,
        _inputs(),
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
    )
    assert mime_evidence.passed
    assert (
        mime_evidence.expected is not None
        and mime_evidence.expected.value == "application/pdf"
    )
    assert (
        mime_evidence.actual is not None
        and mime_evidence.actual.value == "application/pdf"
    )


def test_mime_validation_accepts_complete_rfc_token_alphabet() -> None:
    mime = "x!#$%&'*+.^_`|~-0/y!#$%&'*+.^_`|~-9"
    for rule_type, value in (
        ("mime_type", mime),
        ("metadata_equals", {"field": "mime_type", "expected": mime}),
    ):
        rule = parse_recognition_definition(
            _definition(
                {
                    "id": "mime-token",
                    "type": rule_type,
                    "value": value,
                    "required": True,
                    "weight": 1,
                }
            )
        ).rules[0]
        assert rule.invalid_code is None


def test_invalid_available_sha256_is_a_technical_error_without_evidence() -> None:
    rule = parse_recognition_definition(
        _definition(
            {
                "id": "sha",
                "type": "sha256",
                "value": "a" * 64,
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    evidence = evaluate_rule(
        rule,
        replace(_inputs(), sha256=InputValue(Availability.AVAILABLE, "not-a-hash")),
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
    )
    assert evidence.status is RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR
    assert evidence.code == "HASH_INPUT_INVALID"
    assert evidence.actual is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("published_date", "2026-02-30"),
        ("published_date", "2024-2-29"),
        ("registered_at", "2026-02-30T00:00:00.000Z"),
        ("registered_at", "2026-01-01T00:00:00Z"),
        ("registered_at", "2026-01-01T24:00:00.000Z"),
    ],
)
def test_invalid_civil_dates_and_stored_timestamps_are_rejected(
    field: str, value: str
) -> None:
    rule = parse_recognition_definition(
        _definition(
            {
                "id": "date",
                "type": "metadata_equals",
                "value": {"field": field, "expected": value},
                "required": True,
                "weight": 1,
            }
        )
    ).rules[0]
    assert rule.invalid_code == "RULE_VALUE_INVALID"


def test_sensitive_default_reprs_are_redacted() -> None:
    inputs = _inputs()
    candidate_text = '{"secret":"complete definition"}'
    from reference_engine.recognition.types import ActiveCandidateSnapshot

    candidate = ActiveCandidateSnapshot(
        1, "model", "1", 1, "active", "a" * 64, candidate_text
    )
    text = repr((inputs, candidate))
    for secret in ("private-report.pdf", "Secret marker heading", candidate_text):
        assert secret not in text


def test_valid_civil_date_and_stored_millisecond_timestamp_are_accepted() -> None:
    for field, value in (
        ("published_date", "2024-02-29"),
        ("registered_at", "2026-12-31T23:59:59.999Z"),
    ):
        rule = parse_recognition_definition(
            _definition(
                {
                    "id": "date",
                    "type": "metadata_equals",
                    "value": {"field": field, "expected": value},
                    "required": True,
                    "weight": 1,
                }
            )
        ).rules[0]
        assert rule.invalid_code is None


def test_persisted_complete_fixture_publisher_selector_is_invalid() -> None:
    from pathlib import Path

    from reference_engine.model import load_document_model

    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/models/valid-complete.yaml"
    )
    definition = parse_recognition_definition(
        load_document_model(fixture).canonical_json
    )
    publisher = next(rule for rule in definition.rules if rule.id == "authority")
    assert publisher.invalid_code == "RULE_VALUE_INVALID"
