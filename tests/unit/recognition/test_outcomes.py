from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from reference_engine.recognition import outcomes
from reference_engine.recognition.canonical import CanonicalJSONError
from reference_engine.recognition.outcomes import evaluate_candidate, rank_and_select
from reference_engine.recognition.rules import parse_recognition_definition
from reference_engine.recognition.types import (
    ActiveCandidateSnapshot,
    Availability,
    CandidateEvaluation,
    CapabilitySnapshot,
    InputValue,
    ProbeAcquisitionStatus,
    RecognitionProbeAcquisition,
    RunOutcome,
    TechnicalDocumentInputs,
)


def _candidate(identifier: int, definition_json: str) -> ActiveCandidateSnapshot:
    return ActiveCandidateSnapshot(
        identifier,
        f"model.{identifier}",
        "1.0.0",
        1,
        "active",
        f"{identifier:064x}",
        definition_json,
    )


def _definition(value: str, threshold: object = 1, *, required: bool = True) -> str:
    return json.dumps(
        {
            "recognition": {
                "minimum_score": threshold,
                "rules": [
                    {
                        "id": "mime",
                        "type": "mime_type",
                        "value": value,
                        "required": required,
                        "weight": 1,
                    }
                ],
            }
        }
    )


def _inputs() -> TechnicalDocumentInputs:
    a = Availability.AVAILABLE
    unavailable = InputValue(Availability.UNAVAILABLE)
    return TechnicalDocumentInputs(
        InputValue(a, "application/pdf"),
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        RecognitionProbeAcquisition(ProbeAcquisitionStatus.AVAILABLE_NO_INPUT),
    )


def _evaluate(identifier: int, definition_json: str) -> CandidateEvaluation:
    return evaluate_candidate(
        _candidate(identifier, definition_json),
        parse_recognition_definition(definition_json),
        _inputs(),
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
        "f" * 64,
    )


def test_outcomes_and_deterministic_ranking() -> None:
    winners = (
        _evaluate(2, _definition("application/pdf")),
        _evaluate(1, _definition("application/pdf")),
    )
    ambiguous = rank_and_select(winners)
    assert ambiguous.outcome is RunOutcome.AMBIGUOUS
    assert tuple(item.rank_position for item in ambiguous.evaluations) == (1, 2)
    assert (
        rank_and_select((_evaluate(1, _definition("text/plain")),)).outcome
        is RunOutcome.NOT_MATCHED
    )
    assert rank_and_select(()).outcome is RunOutcome.NOT_MATCHED


def test_matched_and_complete_stable_candidate_evidence() -> None:
    evaluation = _evaluate(1, _definition("application/pdf"))
    result = rank_and_select((evaluation,))
    assert result.outcome is RunOutcome.MATCHED and result.winner is not None
    evidence = json.loads(evaluation.evidence_json)
    assert (
        evidence["recognition_evidence_schema"] == "recognition-candidate-evidence.v1"
    )
    assert evidence["exact_score"] == {"denominator": "1", "numerator": "1"}
    assert len(evaluation.evidence_sha256) == 64
    assert (
        evaluation.evidence_sha256
        == hashlib.sha256(evaluation.evidence_json.encode("utf-8")).hexdigest()
    )
    assert evidence["run_snapshot_sha256"] == "f" * 64
    assert set(evidence) == {
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
    assert set(evidence["rules"][0]) == {
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


def test_non_scoreable_candidate_has_only_null_rule_contributions() -> None:
    definition_json = json.dumps(
        {
            "recognition": {
                "minimum_score": 1,
                "rules": [
                    {
                        "id": "required-fail",
                        "type": "mime_type",
                        "value": "text/plain",
                        "required": True,
                        "weight": 1,
                    },
                    {
                        "id": "optional-pass",
                        "type": "mime_type",
                        "value": "application/pdf",
                        "required": False,
                        "weight": 2,
                    },
                ],
            }
        }
    )
    evidence = json.loads(_evaluate(10, definition_json).evidence_json)
    assert evidence["exact_score"] is None and evidence["display_score"] is None
    assert [rule["score_contribution"] for rule in evidence["rules"]] == [None, None]


@pytest.mark.parametrize(
    ("candidate_hash", "snapshot_hash"),
    [("x" * 64, "f" * 64), ("a" * 64, "F" * 64)],
)
def test_invalid_durable_evidence_hashes_fail_before_serialization(
    candidate_hash: str, snapshot_hash: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition_json = _definition("application/pdf")
    serialized = False

    def forbidden_serialization(value: object) -> bytes:
        nonlocal serialized
        serialized = True
        return b"{}"

    monkeypatch.setattr(outcomes, "canonical_json_bytes", forbidden_serialization)
    with pytest.raises(CanonicalJSONError):
        candidate = replace(
            _candidate(1, definition_json), definition_sha256=candidate_hash
        )
        evaluate_candidate(
            candidate,
            parse_recognition_definition(definition_json),
            _inputs(),
            (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
            snapshot_hash,
        )
    assert not serialized


def test_evidence_hash_uses_the_exact_already_serialized_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = b'{"persisted":"exact bytes"}'
    calls = 0

    def serialize_once(value: object) -> bytes:
        nonlocal calls
        calls += 1
        return persisted

    monkeypatch.setattr(outcomes, "canonical_json_bytes", serialize_once)
    evaluation = _evaluate(1, _definition("application/pdf"))
    assert calls == 1
    assert evaluation.evidence_json.encode("utf-8") == persisted
    assert evaluation.evidence_sha256 == hashlib.sha256(persisted).hexdigest()


def test_failed_and_unsupported_precedence() -> None:
    invalid = _evaluate(1, _definition("INVALID MIME"))
    assert rank_and_select((invalid,)).outcome is RunOutcome.FAILED
    definition_json = _definition("application/pdf")
    unsupported = evaluate_candidate(
        _candidate(2, definition_json),
        parse_recognition_definition(definition_json),
        _inputs(),
        (),
        "f" * 64,
    )
    assert rank_and_select((unsupported,)).outcome is RunOutcome.UNSUPPORTED

    failed_and_unsupported = rank_and_select((unsupported, invalid))
    assert failed_and_unsupported.outcome is RunOutcome.FAILED
    assert all(
        item.rank_position is None for item in failed_and_unsupported.evaluations
    )


def test_probe_attempt_failure_is_a_technical_error_and_failed_run() -> None:
    definition_json = json.dumps(
        {
            "recognition": {
                "minimum_score": 1,
                "rules": [
                    {
                        "id": "text",
                        "type": "text_contains",
                        "value": "private expected",
                        "required": True,
                        "weight": 1,
                    }
                ],
            }
        }
    )
    inputs = replace(
        _inputs(),
        recognition_text_probe=RecognitionProbeAcquisition(
            ProbeAcquisitionStatus.ATTEMPT_FAILED
        ),
    )
    evaluation = evaluate_candidate(
        _candidate(9, definition_json),
        parse_recognition_definition(definition_json),
        inputs,
        (CapabilitySnapshot("recognition_text_probe.v1", "1", Availability.AVAILABLE),),
        "e" * 64,
    )
    assert evaluation.rules[0].status.value == "technical_evaluation_error"
    assert evaluation.rules[0].code == "PROBE_ACQUISITION_FAILED"
    result = rank_and_select((evaluation,))
    assert result.outcome is RunOutcome.FAILED
    assert result.evaluations[0].rank_position is None


def test_optional_unavailable_plus_required_failure_is_not_unsupported() -> None:
    definition_json = json.dumps(
        {
            "recognition": {
                "minimum_score": 1,
                "rules": [
                    {
                        "id": "required",
                        "type": "mime_type",
                        "value": "text/plain",
                        "required": True,
                        "weight": 1,
                    },
                    {
                        "id": "optional",
                        "type": "text_contains",
                        "value": "x",
                        "required": False,
                        "weight": 1,
                    },
                ],
            }
        }
    )
    evaluation = evaluate_candidate(
        _candidate(8, definition_json),
        parse_recognition_definition(definition_json),
        _inputs(),
        (CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),),
        "f" * 64,
    )
    assert rank_and_select((evaluation,)).outcome is RunOutcome.NOT_MATCHED


def test_lower_score_tie_does_not_create_ambiguity() -> None:
    high = _evaluate(1, _definition("application/pdf", 1))
    low_definition = json.dumps(
        {
            "recognition": {
                "minimum_score": 0,
                "rules": [
                    {
                        "id": "pass",
                        "type": "mime_type",
                        "value": "application/pdf",
                        "required": True,
                        "weight": 1,
                    },
                    {
                        "id": "fail",
                        "type": "mime_type",
                        "value": "text/plain",
                        "required": False,
                        "weight": 1,
                    },
                ],
            }
        }
    )
    low_a = _evaluate(2, low_definition)
    low_b = _evaluate(3, low_definition)
    result = rank_and_select((high, low_a, low_b))
    assert result.outcome is RunOutcome.MATCHED
    assert result.winner is not None and result.winner.candidate.model_version_id == 1


def test_unsupported_results_have_no_ranks() -> None:
    definition_json = _definition("application/pdf")
    unsupported = evaluate_candidate(
        _candidate(2, definition_json),
        parse_recognition_definition(definition_json),
        _inputs(),
        (),
        "f" * 64,
    )
    result = rank_and_select((_evaluate(1, definition_json), unsupported))
    assert result.outcome is RunOutcome.UNSUPPORTED
    assert all(item.rank_position is None for item in result.evaluations)
