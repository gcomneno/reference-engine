"""Synthetic persistence tests for complete recognition-v1 invocations."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import traceback
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, cast

import pytest

from reference_engine.db import (
    apply_migrations,
    connect_database,
    get_recognition_results,
    get_recognition_run,
)
from reference_engine.db import (
    persist_completed_recognition_run as persist_typed_completion,
)
from reference_engine.db.recognition_runs import (
    _CompletedRecognitionRunWrite as CompletedRecognitionRunWrite,
)
from reference_engine.db.recognition_runs import (
    _persist_completed_recognition_write as persist_completed_recognition_run,
)
from reference_engine.db.recognition_runs import (
    _RecognitionResultWrite as RecognitionResultWrite,
)
from reference_engine.errors import RecognitionRepositoryError
from reference_engine.recognition.canonical import canonical_json, string_digest
from reference_engine.recognition.decimals import (
    ExactScore,
    add_decimals,
    canonical_decimal,
    display_score,
)
from reference_engine.recognition.outcomes import (
    complete_candidate,
    evaluate_candidate,
    rank_and_select,
    serialize_run_snapshot,
)
from reference_engine.recognition.rules import parse_recognition_definition
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
    RuleEvaluationStatus,
    RunFailureCode,
    RunOutcome,
    SafeConfigurationEntry,
    SafeDocumentInputSnapshot,
    SafeString,
    SafeTextProbeSnapshot,
    TechnicalDocumentInputs,
)

HASH = "a" * 64
NOW = "2026-01-01T00:00:00.000000Z"
DONE = "2026-01-01T00:00:01.000000Z"
STORED_NOW = "2026-01-01T00:00:00.000Z"
DEFINITION = canonical_json(
    {
        "recognition": {
            "ambiguity_policy": "reject",
            "minimum_score": 1,
            "rules": [
                {
                    "id": "mime",
                    "required": True,
                    "type": "mime_type",
                    "value": "application/pdf",
                    "weight": 1,
                }
            ],
        }
    }
)
DEFINITION_SHA256 = hashlib.sha256(DEFINITION.encode()).hexdigest()


@pytest.fixture
def database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "recognition.sqlite3")
    apply_migrations(connection)
    _seed(connection)
    connection.commit()
    return connection


def _seed(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO artifacts VALUES (1, 'document', 'vault', 'canonical', "
        "'document.pdf', ?, 'application/pdf', 1, ?, ?)",
        (HASH, STORED_NOW, STORED_NOW),
    )
    connection.execute(
        "INSERT INTO documents VALUES (1, 1, ?, 'document.pdf', NULL, NULL, "
        "NULL, NULL, ?)",
        (HASH, STORED_NOW),
    )
    for model_id in (1, 2, 3):
        digest = f"{model_id:064x}"
        connection.execute(
            "INSERT INTO artifacts VALUES (?, 'document_model', 'vault', "
            "'canonical', ?, ?, 'application/yaml', 1, ?, ?)",
            (model_id + 1, f"model-{model_id}.yaml", digest, STORED_NOW, STORED_NOW),
        )
        connection.execute(
            "INSERT INTO document_models VALUES (?, ?, 'title', 'document', "
            "'record', ?)",
            (model_id, f"model.{model_id}", STORED_NOW),
        )
        connection.execute(
            "INSERT INTO document_model_versions VALUES (?, ?, '1.0.0', 1, "
            "'active', 'v1', ?, ?, ?, ?)",
            (
                model_id,
                model_id,
                model_id + 1,
                DEFINITION,
                DEFINITION_SHA256,
                STORED_NOW,
            ),
        )


def _snapshot(model_ids: tuple[int, ...]) -> tuple[str, str]:
    value = {
        "capabilities": [
            {
                "availability": "available",
                "configuration": {},
                "identifier": "document_metadata.v1",
                "version": "1",
            }
        ],
        "candidates": [
            {
                "definition_sha256": DEFINITION_SHA256,
                "model_key": f"model.{model_id}",
                "model_version_id": model_id,
                "schema_version": 1,
                "semantic_version": "1.0.0",
                "status": "active",
            }
            for model_id in model_ids
        ],
        "document_id": 1,
        "document_sha256": HASH,
        "engine_version": "engine/1",
        "safe_document_inputs": {"mime_type": "application/pdf"},
        "snapshot_schema_version": "recognition-run-snapshot.v1",
        "source_artifact_id": 1,
    }
    text = canonical_json(value)
    return text, hashlib.sha256(text.encode()).hexdigest()


def _result(
    model_id: int, snapshot_hash: str, *, failed: bool = False
) -> RecognitionResultWrite:
    rule = {
        "actual": None if failed else {"kind": "mime_type", "value": "application/pdf"},
        "code": "EVALUATOR_ERROR" if failed else None,
        "expected": {"kind": "mime_type", "value": "application/pdf"},
        "id": "mime",
        "passed": None if failed else True,
        "required": True,
        "score_contribution": None if failed else "1.000000",
        "status": "technical_evaluation_error" if failed else "evaluated_pass",
        "type": "mime_type",
        "weight": "1",
    }
    evidence = canonical_json(
        {
            "candidate_state": None if failed else "evaluated",
            "display_score": None if failed else "1.000000",
            "eligible": None if failed else True,
            "exact_score": None if failed else {"denominator": "1", "numerator": "1"},
            "model": {
                "definition_sha256": DEFINITION_SHA256,
                "key": f"model.{model_id}",
                "semantic_version": "1.0.0",
                "version_id": model_id,
            },
            "recognition_evidence_schema": "recognition-candidate-evidence.v1",
            "required_rules_passed": None if failed else True,
            "rules": [rule],
            "run_snapshot_sha256": snapshot_hash,
            "threshold": "1",
        }
    )
    return RecognitionResultWrite(
        model_id,
        0.0 if failed else 1.0,
        not failed,
        not failed,
        None if failed else model_id,
        evidence,
        hashlib.sha256(evidence.encode()).hexdigest(),
    )


def _write(
    model_ids: tuple[int, ...] = (1,),
    *,
    outcome: RunOutcome = RunOutcome.MATCHED,
    failed_evidence: bool = False,
) -> CompletedRecognitionRunWrite:
    snapshot, digest = _snapshot(model_ids)
    results = tuple(_result(item, digest, failed=failed_evidence) for item in model_ids)
    return CompletedRecognitionRunWrite(
        1,
        "engine/1",
        NOW,
        DONE,
        outcome,
        "EVALUATION_FAILED" if outcome is RunOutcome.FAILED else None,
        "Recognition evaluation failed." if outcome is RunOutcome.FAILED else None,
        snapshot,
        digest,
        results,
        1 if outcome is RunOutcome.MATCHED else None,
    )


def _replace_json(
    result: RecognitionResultWrite, transform: Callable[[dict[str, Any]], None]
) -> RecognitionResultWrite:
    value: dict[str, Any] = json.loads(result.details_json)
    transform(value)
    text = canonical_json(value)
    return replace(
        result,
        details_json=text,
        details_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def _scored_result(
    result: RecognitionResultWrite,
    *,
    numerator: str,
    denominator: str,
    threshold: str,
    score: float,
    rank: int | None,
) -> RecognitionResultWrite:
    def transform(value: dict[str, Any]) -> None:
        numerator_value = Decimal(numerator)
        denominator_value = Decimal(denominator)
        failed_weight = denominator_value - numerator_value
        value["rules"] = [
            {
                "actual": {"kind": "mime_type", "value": "application/pdf"},
                "code": None,
                "expected": {"kind": "mime_type", "value": "application/pdf"},
                "id": "pass",
                "passed": True,
                "required": True,
                "score_contribution": display_score(
                    ExactScore(numerator_value, denominator_value)
                ),
                "status": "evaluated_pass",
                "type": "mime_type",
                "weight": numerator,
            },
            {
                "actual": {"kind": "mime_type", "value": "application/pdf"},
                "code": None,
                "expected": {"kind": "mime_type", "value": "text/plain"},
                "id": "fail",
                "passed": False,
                "required": False,
                "score_contribution": display_score(
                    ExactScore(Decimal(0), denominator_value)
                ),
                "status": "evaluated_fail",
                "type": "mime_type",
                "weight": str(failed_weight),
            },
        ]
        value["exact_score"] = {
            "numerator": numerator,
            "denominator": denominator,
        }
        value["display_score"] = display_score(
            ExactScore(Decimal(numerator), Decimal(denominator))
        )
        value["threshold"] = threshold

    return replace(_replace_json(result, transform), score=score, rank_position=rank)


def _replace_snapshot(
    write: CompletedRecognitionRunWrite, transform: Callable[[dict[str, Any]], None]
) -> CompletedRecognitionRunWrite:
    assert write.input_snapshot_json is not None
    value: dict[str, Any] = json.loads(write.input_snapshot_json)
    transform(value)
    text = canonical_json(value)
    digest = hashlib.sha256(text.encode()).hexdigest()
    results = tuple(
        _replace_json(
            item, lambda evidence: evidence.update(run_snapshot_sha256=digest)
        )
        for item in write.results
    )
    return replace(
        write,
        input_snapshot_json=text,
        input_snapshot_sha256=digest,
        results=results,
    )


def _bind_definition(
    connection: sqlite3.Connection,
    write: CompletedRecognitionRunWrite,
    model_id: int,
    rules: list[dict[str, Any]],
    threshold: int | str | Decimal,
) -> CompletedRecognitionRunWrite:
    threshold_text = canonical_decimal(Decimal(threshold))
    encoded_rules = [
        {**rule, "weight": f"__decimal_{canonical_decimal(Decimal(rule['weight']))}"}
        for rule in rules
    ]
    definition = json.dumps(
        {
            "recognition": {
                "ambiguity_policy": "reject",
                "minimum_score": f"__decimal_{threshold_text}",
                "rules": encoded_rules,
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    definition = re.sub(r'"__decimal_([0-9.]+)"', r"\1", definition)
    digest = hashlib.sha256(definition.encode()).hexdigest()
    connection.execute(
        "UPDATE document_model_versions SET definition_json = ?, definition_sha256 = ? "
        "WHERE id = ?",
        (definition, digest, model_id),
    )

    def alter_snapshot(snapshot: dict[str, Any]) -> None:
        for candidate in snapshot["candidates"]:
            if candidate["model_version_id"] == model_id:
                candidate["definition_sha256"] = digest

    bound = _replace_snapshot(write, alter_snapshot)
    return replace(
        bound,
        results=tuple(
            _replace_json(
                result,
                lambda evidence: evidence["model"].update(definition_sha256=digest),
            )
            if result.model_version_id == model_id
            else result
            for result in bound.results
        ),
    )


def test_successful_matched_run_persists_complete_typed_unit(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    persisted = persist_completed_recognition_run(database, write)
    result = persisted.results[0]
    assert persisted.id > 0 and persisted.outcome is RunOutcome.MATCHED
    assert persisted.document_sha256 == HASH
    assert persisted.input_snapshot_json == write.input_snapshot_json
    assert persisted.input_snapshot_sha256 == write.input_snapshot_sha256
    assert persisted.winner == result
    assert result.details_sha256 == write.results[0].details_sha256
    assert result.model_definition_sha256 == DEFINITION_SHA256
    assert result.candidate_state == "evaluated"
    assert result.evidence_eligible is True
    assert result.evidence_required_rules_passed is True
    assert result.exact_score_numerator == "1"
    assert result.exact_score_denominator == "1"
    assert result.display_score == "1.000000"
    assert result.threshold == "1"
    assert result.score == 1.0 and result.rank_position == 1
    assert result.model_version_id == persisted.winner.model_version_id
    assert database.in_transaction is False
    assert get_recognition_run(database, persisted.id) == persisted


def test_exact_weight_totals_ignore_caller_decimal_context(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    weights = (
        Decimal("123456.12345678901234567890123456789"),
        Decimal("234567.23456789012345678901234567891"),
        Decimal("345678.34567890123456789012345678912"),
    )
    numerator = add_decimals(weights[:2])
    denominator = add_decimals(weights)

    def alter(evidence: dict[str, Any]) -> None:
        template = evidence["rules"][0]
        rules = []
        for index, weight in enumerate(weights):
            passed = index < 2
            rule = dict(template)
            rule.update(
                id=f"rule-{index}",
                passed=passed,
                required=False,
                status="evaluated_pass" if passed else "evaluated_fail",
                weight=canonical_decimal(weight),
                score_contribution=display_score(
                    ExactScore(weight if passed else Decimal(0), denominator)
                ),
            )
            rules.append(rule)
        evidence.update(
            rules=rules,
            exact_score={
                "numerator": canonical_decimal(numerator),
                "denominator": canonical_decimal(denominator),
            },
            display_score=display_score(ExactScore(numerator, denominator)),
            threshold="0",
        )

    result = _replace_json(write.results[0], alter)
    shown = json.loads(result.details_json)["display_score"]
    result = replace(result, score=float(shown))
    write = replace(write, results=(result,))
    write = _bind_definition(
        database,
        write,
        1,
        [
            {
                "id": f"rule-{index}",
                "required": False,
                "type": "mime_type",
                "value": "application/pdf",
                "weight": weight,
            }
            for index, weight in enumerate(weights)
        ],
        Decimal(0),
    )

    with localcontext() as context:
        context.prec = 6
        persisted = persist_completed_recognition_run(database, write)

    assert persisted.results[0].exact_score_numerator == canonical_decimal(numerator)
    assert persisted.results[0].exact_score_denominator == canonical_decimal(
        denominator
    )


def test_valid_six_digit_run_timestamps_are_accepted(
    database: sqlite3.Connection,
) -> None:
    persisted = persist_completed_recognition_run(
        database,
        replace(
            _write(),
            started_at="2024-02-29T23:59:59.000001Z",
            completed_at="2024-02-29T23:59:59.999999Z",
        ),
    )
    assert persisted.started_at == "2024-02-29T23:59:59.000001Z"
    assert persisted.completed_at == "2024-02-29T23:59:59.999999Z"


@pytest.mark.parametrize(
    ("started_at", "completed_at"),
    [
        ("2026-01-01T00:00:00.000Z", DONE),
        ("2026-02-30T00:00:00.000000Z", DONE),
        (NOW, "2026-01-01T24:00:00.000000Z"),
        ("2026-01-01T00:00:00.000000+00:00", DONE),
        (DONE, NOW),
    ],
    ids=["milliseconds", "impossible-date", "impossible-time", "offset", "earlier"],
)
def test_invalid_run_timestamps_are_rejected(
    database: sqlite3.Connection, started_at: str, completed_at: str
) -> None:
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database,
            replace(_write(), started_at=started_at, completed_at=completed_at),
        )


def test_valid_snapshot_dates_and_millisecond_timestamps_are_accepted(
    database: sqlite3.Connection,
) -> None:
    write = _replace_snapshot(
        _write(),
        lambda snapshot: snapshot["safe_document_inputs"].update(
            published_date="2024-02-29",
            retrieved_at="2026-01-01T23:59:59.001Z",
            registered_at="2026-01-02T00:00:00.999Z",
        ),
    )
    assert persist_completed_recognition_run(database, write).id > 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("published_date", "2026-02-30"),
        ("published_date", "2026-99-99"),
        ("published_date", "2026-1-01"),
        ("retrieved_at", "2026-01-01T24:00:00.000Z"),
        ("retrieved_at", "2026-01-01T00:00:00.000000Z"),
        ("registered_at", "2026-02-30T00:00:00.000Z"),
        ("registered_at", "2026-01-01T00:00:00.000+00:00"),
    ],
)
def test_invalid_snapshot_dates_and_timestamps_are_rejected(
    database: sqlite3.Connection, field: str, value: str
) -> None:
    write = _replace_snapshot(
        _write(),
        lambda snapshot: snapshot["safe_document_inputs"].update({field: value}),
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)


def test_empty_candidate_not_matched_and_repeated_runs_are_append_only(
    database: sqlite3.Connection,
) -> None:
    write = _write((), outcome=RunOutcome.NOT_MATCHED)
    first = persist_completed_recognition_run(database, write)
    second = persist_completed_recognition_run(database, write)
    assert first.results == second.results == ()
    assert first.id != second.id
    assert database.execute("SELECT count(*) FROM recognition_runs").fetchone()[0] == 2


def test_failed_run_with_complete_candidate_evidence(
    database: sqlite3.Connection,
) -> None:
    write = _write((1, 2), outcome=RunOutcome.FAILED, failed_evidence=True)
    persisted = persist_completed_recognition_run(database, write)
    assert len(persisted.results) == 2
    assert persisted.winner is None
    assert all(
        item.rank_position is None and item.score == 0.0 for item in persisted.results
    )
    for item in persisted.results:
        assert item.eligible is False and item.required_rules_passed is False
        assert item.candidate_state is None
        assert item.evidence_eligible is None
        assert item.evidence_required_rules_passed is None
        assert item.exact_score_numerator is None
        assert item.exact_score_denominator is None
        assert item.display_score is None


def test_pre_snapshot_failed_run_has_no_results(database: sqlite3.Connection) -> None:
    write = replace(
        _write((), outcome=RunOutcome.FAILED),
        input_snapshot_json=None,
        input_snapshot_sha256=None,
    )
    persisted = persist_completed_recognition_run(database, write)
    assert persisted.input_snapshot_json is None and persisted.results == ()


def test_post_snapshot_run_level_failure_has_no_candidate_prefix(
    database: sqlite3.Connection,
) -> None:
    write = replace(
        _write((1, 2), outcome=RunOutcome.FAILED, failed_evidence=True), results=()
    )
    persisted = persist_completed_recognition_run(database, write)
    assert persisted.input_snapshot_sha256 is not None
    assert persisted.results == ()


def test_caller_transaction_survives_and_remains_open(
    database: sqlite3.Connection,
) -> None:
    database.execute("BEGIN")
    database.execute("UPDATE artifacts SET mime_type = 'kept' WHERE id = 1")
    persisted = persist_completed_recognition_run(database, _write())
    assert database.in_transaction and persisted.id > 0
    database.rollback()
    assert (
        database.execute("SELECT mime_type FROM artifacts WHERE id=1").fetchone()[0]
        == "application/pdf"
    )
    assert get_recognition_run(database, persisted.id) is None


def test_invalid_result_identity_is_rejected_before_write(
    database: sqlite3.Connection,
) -> None:
    write = _write((1, 2))
    write = replace(
        write,
        results=(write.results[0], replace(write.results[1], model_version_id=999)),
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)
    assert database.execute("SELECT count(*) FROM recognition_runs").fetchone()[0] == 0
    assert (
        database.execute("SELECT count(*) FROM recognition_results").fetchone()[0] == 0
    )


def test_database_late_failure_leaves_no_candidate_prefix(
    database: sqlite3.Connection,
) -> None:
    write = _write((1, 2), outcome=RunOutcome.AMBIGUOUS)
    database.execute("DELETE FROM document_model_versions WHERE id = 2")
    database.commit()
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)
    assert database.execute("SELECT count(*) FROM recognition_runs").fetchone()[0] == 0
    assert (
        database.execute("SELECT count(*) FROM recognition_results").fetchone()[0] == 0
    )


@pytest.mark.parametrize("field", ["input_snapshot_json", "input_snapshot_sha256"])
def test_invalid_canonical_snapshot_or_hash_is_rejected_before_writes(
    database: sqlite3.Connection, field: str
) -> None:
    write = _write()
    if field == "input_snapshot_json":
        assert write.input_snapshot_json is not None
        write = replace(write, input_snapshot_json=" " + write.input_snapshot_json)
    else:
        write = replace(write, input_snapshot_sha256="b" * 64)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)
    assert database.execute("SELECT count(*) FROM recognition_runs").fetchone()[0] == 0


def test_evidence_hash_uses_exact_persisted_utf8_bytes(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    result = write.results[0]
    assert "model.1" in result.details_json
    altered = replace(
        result,
        details_sha256=hashlib.sha256((result.details_json + " ").encode()).hexdigest(),
    )
    with pytest.raises(RecognitionRepositoryError, match="persistence data is invalid"):
        persist_completed_recognition_run(database, replace(write, results=(altered,)))


def _typed_inputs() -> TechnicalDocumentInputs:
    available = Availability.AVAILABLE
    return TechnicalDocumentInputs(
        mime_type=InputValue(available, "application/pdf"),
        original_filename=InputValue(available, "report.pdf"),
        byte_size=InputValue(available, 100),
        source_url=InputValue(available, None),
        retrieved_at=InputValue(available, None),
        published_date=InputValue(available, None),
        page_count=InputValue(available, 3),
        registered_at=InputValue(available, STORED_NOW),
        sha256=InputValue(available, HASH),
        recognition_text_probe=RecognitionProbeAcquisition(
            ProbeAcquisitionStatus.AVAILABLE_WITH_PROBE,
            RecognitionTextProbe(
                "alpha report text", 128, False, "recognition_text_probe.v1", "1"
            ),
        ),
    )


def _typed_completion(
    database: sqlite3.Connection, rule: dict[str, Any]
) -> RecognitionCompletion:
    definition = canonical_json(
        {
            "recognition": {
                "ambiguity_policy": "reject",
                "minimum_score": 1,
                "rules": [rule],
            }
        }
    )
    digest = hashlib.sha256(definition.encode()).hexdigest()
    database.execute(
        "UPDATE document_model_versions SET definition_json=?, definition_sha256=? "
        "WHERE id=1",
        (definition, digest),
    )
    database.commit()
    candidate = ActiveCandidateSnapshot(
        1, "model.1", "1.0.0", 1, "active", digest, definition
    )
    inputs = _typed_inputs()
    text_digest, text_length = string_digest("alpha report text")
    filename_digest, filename_length = string_digest("report.pdf")
    safe = SafeDocumentInputSnapshot(
        (
            ("mime_type", "application/pdf"),
            ("original_filename", SafeString(filename_digest, filename_length)),
            ("byte_size", 100),
            ("source_url", None),
            ("retrieved_at", None),
            ("published_date", None),
            ("page_count", 3),
            ("registered_at", STORED_NOW),
            ("sha256", HASH),
            (
                "recognition_text_probe",
                SafeTextProbeSnapshot(text_digest, text_length, 128, False),
            ),
        )
    )
    capabilities = (
        CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),
        CapabilitySnapshot(
            "recognition_text_probe.v1",
            "1",
            Availability.AVAILABLE,
            (SafeConfigurationEntry("maximum_code_points", 128),),
        ),
    )
    snapshot = RecognitionRunSnapshot(
        "recognition-run-snapshot.v1",
        1,
        1,
        HASH,
        "engine/1",
        capabilities,
        (candidate,),
        safe,
    )
    _, snapshot_hash = serialize_run_snapshot(snapshot)
    evaluation = evaluate_candidate(
        candidate,
        parse_recognition_definition(definition),
        inputs,
        capabilities,
        snapshot_hash,
    )
    return RecognitionCompletion(
        1,
        "engine/1",
        NOW,
        DONE,
        snapshot,
        inputs,
        rank_and_select((evaluation,)),
    )


def _assert_invalid_public_completion(
    database: sqlite3.Connection, completion: RecognitionCompletion
) -> None:
    with pytest.raises(RecognitionRepositoryError) as caught:
        persist_typed_completion(database, completion)
    error = caught.value
    assert type(error) is RecognitionRepositoryError
    assert error.code == "RECOGNITION_PERSISTENCE_INVALID"
    assert error.message == "Recognition persistence data is invalid."
    assert error.details is None
    assert error.__cause__ is None
    assert "has no attribute" not in str(error)


def test_public_typed_completion_persists_valid_graph(
    database: sqlite3.Connection,
) -> None:
    completion = _typed_completion(
        database,
        {
            "id": "r",
            "type": "mime_type",
            "value": "application/pdf",
            "required": True,
            "weight": 1,
        },
    )

    persisted = persist_typed_completion(database, completion)

    assert persisted.outcome is RunOutcome.MATCHED
    assert persisted.winner is not None
    assert persisted.winner.model_version_id == 1


@pytest.mark.parametrize("field", ["snapshot", "inputs", "ranking", "failure_code"])
def test_public_typed_boundary_closes_malformed_top_level_graph_members(
    database: sqlite3.Connection, field: str
) -> None:
    completion = _typed_completion(
        database,
        {
            "id": "r",
            "type": "mime_type",
            "value": "application/pdf",
            "required": True,
            "weight": 1,
        },
    )

    invalid = cast(Any, object())
    if field == "snapshot":
        malformed = replace(completion, snapshot=invalid)
    elif field == "inputs":
        malformed = replace(completion, inputs=invalid)
    elif field == "ranking":
        malformed = replace(completion, ranking=invalid)
    else:
        malformed = replace(completion, failure_code=invalid)

    _assert_invalid_public_completion(database, malformed)


def test_public_typed_boundary_closes_malformed_nested_snapshot_member(
    database: sqlite3.Connection,
) -> None:
    completion = _typed_completion(
        database,
        {
            "id": "r",
            "type": "mime_type",
            "value": "application/pdf",
            "required": True,
            "weight": 1,
        },
    )
    assert completion.snapshot is not None
    malformed_snapshot = replace(completion.snapshot, candidates=cast(Any, (object(),)))

    _assert_invalid_public_completion(
        database, replace(completion, snapshot=malformed_snapshot)
    )


@pytest.mark.parametrize(
    "rule",
    [
        {
            "id": "r",
            "type": "mime_type",
            "value": "application/pdf",
            "required": True,
            "weight": 1,
        },
        {
            "id": "r",
            "type": "filename_regex",
            "value": r"report\.pdf$",
            "required": True,
            "weight": 1,
        },
        {"id": "r", "type": "sha256", "value": HASH, "required": True, "weight": 1},
        {
            "id": "r",
            "type": "page_count_between",
            "value": {"minimum": 1, "maximum": 5},
            "required": True,
            "weight": 1,
        },
        {
            "id": "r",
            "type": "metadata_equals",
            "value": {"field": "mime_type", "expected": "application/pdf"},
            "required": True,
            "weight": 1,
        },
        {
            "id": "r",
            "type": "text_contains",
            "value": "report",
            "required": True,
            "weight": 1,
        },
        {
            "id": "r",
            "type": "text_regex",
            "value": r"alpha.*text",
            "required": True,
            "weight": 1,
        },
    ],
    ids=lambda rule: str(rule["type"]),
)
def test_typed_boundary_rejects_false_rule_semantics(
    database: sqlite3.Connection, rule: dict[str, Any]
) -> None:
    completion = _typed_completion(database, rule)
    assert completion.ranking is not None
    evaluation = completion.ranking.evaluations[0]
    evidence = evaluation.rules[0]
    assert evidence.expected is not None
    if evidence.expected.sha256 is not None:
        wrong_expected = replace(evidence.expected, sha256="b" * 64)
    else:
        wrong_values: dict[str, str] = {
            "mime_type": "text/plain",
            "sha256": "b" * 64,
            "page_count_range": "1..4",
        }
        wrong_expected = replace(
            evidence.expected,
            value=wrong_values[evidence.expected.kind],
        )
    definition = parse_recognition_definition(evaluation.candidate.definition_json)
    snapshot_hash = json.loads(evaluation.evidence_json)["run_snapshot_sha256"]
    wrong_evaluation = complete_candidate(
        evaluation.candidate,
        definition,
        (replace(evidence, expected=wrong_expected),),
        snapshot_hash,
    )
    wrong_completion = replace(completion, ranking=rank_and_select((wrong_evaluation,)))
    with pytest.raises(RecognitionRepositoryError):
        persist_typed_completion(database, wrong_completion)

    contradictory = replace(
        evidence,
        status=RuleEvaluationStatus.EVALUATED_FAIL,
        passed=False,
    )
    rebuilt = complete_candidate(
        evaluation.candidate,
        definition,
        (contradictory,),
        snapshot_hash,
    )
    forged = replace(completion, ranking=rank_and_select((rebuilt,)))
    with pytest.raises(RecognitionRepositoryError):
        persist_typed_completion(database, forged)


def test_public_failure_message_is_closed_and_redacted(
    database: sqlite3.Connection,
) -> None:
    completion = RecognitionCompletion(
        1,
        "engine/1",
        NOW,
        DONE,
        None,
        failure_code=RunFailureCode.SNAPSHOT_FAILED,
    )
    persisted = persist_typed_completion(database, completion)
    assert persisted.error_message == "Recognition snapshot creation failed."
    assert "document.pdf" not in persisted.error_message


def test_typed_boundary_rejects_impossible_technical_code(
    database: sqlite3.Connection,
) -> None:
    completion = _typed_completion(
        database,
        {
            "id": "r",
            "type": "mime_type",
            "value": "application/pdf",
            "required": True,
            "weight": 1,
        },
    )
    assert completion.ranking is not None
    evaluation = completion.ranking.evaluations[0]
    technical = replace(
        evaluation.rules[0],
        status=RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR,
        passed=None,
        code="REGEX_RESOURCE_EXHAUSTED",
    )
    rebuilt = complete_candidate(
        evaluation.candidate,
        parse_recognition_definition(evaluation.candidate.definition_json),
        (technical,),
        json.loads(evaluation.evidence_json)["run_snapshot_sha256"],
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_typed_completion(
            database, replace(completion, ranking=rank_and_select((rebuilt,)))
        )


def test_closed_connection_public_reads_and_writes_are_translated(
    database: sqlite3.Connection,
) -> None:
    completion = _typed_completion(
        database,
        {
            "id": "r",
            "type": "mime_type",
            "value": "application/pdf",
            "required": True,
            "weight": 1,
        },
    )
    database.close()
    with pytest.raises(RecognitionRepositoryError, match="repository operation failed"):
        get_recognition_run(database, 1)
    with pytest.raises(RecognitionRepositoryError, match="repository operation failed"):
        persist_typed_completion(database, completion)


class _CleanupFailConnection(sqlite3.Connection):
    fail_cleanup = False

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        if self.fail_cleanup and sql.startswith("ROLLBACK TO SAVEPOINT"):
            raise sqlite3.OperationalError("cleanup failed")
        return super().execute(sql, parameters)  # type: ignore[arg-type]


def test_original_exception_survives_savepoint_cleanup_failure() -> None:
    connection = sqlite3.connect(":memory:", factory=_CleanupFailConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection)
    _seed(connection)
    connection.commit()
    connection.execute("BEGIN")
    connection.fail_cleanup = True
    write = _write((1, 2), outcome=RunOutcome.AMBIGUOUS)
    connection.execute("DELETE FROM document_model_versions WHERE id = 2")
    with pytest.raises(RecognitionRepositoryError) as caught:
        persist_completed_recognition_run(connection, write)
    assert "cleanup failed" not in str(caught.value)
    assert connection.in_transaction


@pytest.mark.parametrize(
    "case",
    ["reversed", "missing-middle", "additional", "duplicate"],
    ids=["reversed", "missing-middle", "additional", "duplicate"],
)
def test_result_sequence_must_exactly_match_snapshot_order(
    database: sqlite3.Connection, case: str
) -> None:
    write = _write((1, 2, 3), outcome=RunOutcome.AMBIGUOUS)
    if case == "reversed":
        supplied = tuple(reversed(write.results))
    elif case == "missing-middle":
        supplied = (write.results[0], write.results[2])
    elif case == "additional":
        assert write.input_snapshot_sha256 is not None
        supplied = (*write.results, _result(4, write.input_snapshot_sha256))
    else:
        supplied = (write.results[0], write.results[1], write.results[1])
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=supplied))
    assert database.execute("SELECT count(*) FROM recognition_runs").fetchone()[0] == 0


def test_exact_result_order_is_persisted_and_read_back(
    database: sqlite3.Connection,
) -> None:
    write = _write((1, 2, 3), outcome=RunOutcome.AMBIGUOUS)
    persisted = persist_completed_recognition_run(database, write)
    expected = (1, 2, 3)
    assert tuple(item.model_version_id for item in persisted.results) == expected
    assert (
        tuple(
            item.model_version_id
            for item in get_recognition_results(database, persisted.id)
        )
        == expected
    )


@pytest.mark.parametrize("boolean", [True, False])
@pytest.mark.parametrize(
    "field",
    [
        "document_id",
        "winner_model_version_id",
        "result_model_version_id",
        "rank_position",
        "snapshot_document_id",
        "snapshot_source_artifact_id",
        "snapshot_candidate_model_version_id",
        "snapshot_candidate_schema_version",
    ],
)
def test_boolean_is_rejected_for_every_integer_identity_and_rank(
    database: sqlite3.Connection, field: str, boolean: bool
) -> None:
    write = _write()
    if field == "document_id":
        write = replace(write, document_id=boolean)
    elif field == "winner_model_version_id":
        write = replace(write, winner_model_version_id=boolean)
    elif field == "result_model_version_id":
        write = replace(
            write, results=(replace(write.results[0], model_version_id=boolean),)
        )
    elif field == "rank_position":
        write = replace(
            write, results=(replace(write.results[0], rank_position=boolean),)
        )
    else:
        key = field.removeprefix("snapshot_")

        def alter(snapshot: dict[str, Any]) -> None:
            if key.startswith("candidate_"):
                candidate_key = key.removeprefix("candidate_")
                snapshot["candidates"][0][candidate_key] = boolean
            else:
                snapshot[key] = boolean

        write = _replace_snapshot(write, alter)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)


@pytest.mark.parametrize("score", [math.nan, math.inf, -math.inf, -0.0])
def test_invalid_float_score_projections_are_rejected(
    database: sqlite3.Connection, score: float
) -> None:
    write = _write()
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database, replace(write, results=(replace(write.results[0], score=score),))
        )


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [("01", "1"), ("x", "1"), ("1", "0"), ("2", "1")],
)
def test_invalid_exact_score_is_rejected(
    database: sqlite3.Connection, numerator: str, denominator: str
) -> None:
    write = _write()

    def alter(evidence: dict[str, Any]) -> None:
        evidence["exact_score"] = {"numerator": numerator, "denominator": denominator}

    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database, replace(write, results=(_replace_json(write.results[0], alter),))
        )


def test_score_display_and_relational_projection_must_agree(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    wrong_display = _replace_json(
        write.results[0], lambda evidence: evidence.update(display_score="0.999999")
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database, replace(write, results=(wrong_display,))
        )
    wrong_relational = replace(write.results[0], score=0.5)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database, replace(write, results=(wrong_relational,))
        )
    malformed = _replace_json(
        write.results[0], lambda evidence: evidence.update(display_score="1.00000")
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database, replace(write, results=(malformed,))
        )


def test_scoreable_and_non_scoreable_projections_are_valid(
    database: sqlite3.Connection,
) -> None:
    assert persist_completed_recognition_run(database, _write()).results[0].score == 1.0
    not_matched = _write(outcome=RunOutcome.NOT_MATCHED)
    ineligible = _state_result(
        not_matched.results[0], "definitively_ineligible", False, False
    )
    persisted_ineligible = persist_completed_recognition_run(
        database, replace(not_matched, results=(ineligible,))
    )
    assert persisted_ineligible.results[0].score == 0.0
    failed = persist_completed_recognition_run(
        database, _write((1,), outcome=RunOutcome.FAILED, failed_evidence=True)
    )
    assert failed.results[0].score == 0.0
    bad = _write((1,), outcome=RunOutcome.FAILED, failed_evidence=True)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database, replace(bad, results=(replace(bad.results[0], score=0.1),))
        )


@pytest.mark.parametrize("location", ["snapshot", "evidence"])
def test_repository_validation_errors_redact_supplied_json_content(
    database: sqlite3.Connection, location: str
) -> None:
    marker = "SENSITIVE-path-and-url-marker"
    write = _write()
    if location == "snapshot":
        text = f'{{"../../{marker}":1,"../../{marker}":2}}'
        write = replace(
            write,
            input_snapshot_json=text,
            input_snapshot_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
    else:
        text = f'{{"https://example.invalid/{marker}":1,"unterminated":"{marker}'
        result = replace(
            write.results[0],
            details_json=text,
            details_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        write = replace(write, results=(result,))
    with pytest.raises(RecognitionRepositoryError) as caught:
        persist_completed_recognition_run(database, write)
    exposed = (
        str(caught.value),
        repr(caught.value),
        repr(caught.value.details),
        "".join(traceback.format_exception(caught.value)),
    )
    assert all(marker not in item for item in exposed)


def test_embedded_hash_validation_redacts_key_and_value(
    database: sqlite3.Connection,
) -> None:
    marker = "SENSITIVE-hash-marker"
    write = _write()

    def alter(evidence: dict[str, Any]) -> None:
        evidence[f"https://example.invalid/{marker}/sha256"] = marker

    result = _replace_json(write.results[0], alter)
    with pytest.raises(RecognitionRepositoryError) as caught:
        persist_completed_recognition_run(database, replace(write, results=(result,)))
    assert caught.value.details is None
    assert marker not in str(caught.value) and marker not in repr(caught.value)


def _state_result(
    result: RecognitionResultWrite,
    state: str | None,
    eligible: bool | None,
    required: bool | None,
) -> RecognitionResultWrite:
    def alter(evidence: dict[str, Any]) -> None:
        rule = evidence["rules"][0]
        rule.update(score_contribution=None)
        if state == "definitively_ineligible":
            rule.update(status="evaluated_fail", passed=False, code=None)
        elif state == "indeterminate":
            rule.update(
                actual=None,
                status="unavailable_input",
                passed=None,
                code="INPUT_UNAVAILABLE",
            )
        evidence.update(
            candidate_state=state,
            eligible=eligible,
            required_rules_passed=required,
            exact_score=None,
            display_score=None,
        )

    return replace(
        _replace_json(result, alter),
        score=0.0,
        eligible=eligible is True,
        required_rules_passed=required is True,
        rank_position=None,
    )


@pytest.mark.parametrize(
    ("actual", "claimed"),
    [
        ("indeterminate", RunOutcome.NOT_MATCHED),
        ("evaluated", RunOutcome.UNSUPPORTED),
    ],
)
def test_outcome_cannot_contradict_candidate_state(
    database: sqlite3.Connection, actual: str, claimed: RunOutcome
) -> None:
    write = _write(outcome=claimed)
    if actual == "indeterminate":
        result = _state_result(write.results[0], actual, None, None)
    else:
        result = write.results[0]
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(result,)))


def test_qualifiers_determine_matched_and_ambiguous_outcomes(
    database: sqlite3.Connection,
) -> None:
    tied = _write((1, 2), outcome=RunOutcome.MATCHED)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, tied)

    unique = _write((1, 2), outcome=RunOutcome.AMBIGUOUS)
    second = _scored_result(
        unique.results[1],
        numerator="1",
        denominator="2",
        threshold="0.75",
        score=0.5,
        rank=2,
    )
    unique = replace(unique, results=(unique.results[0], second))
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, unique)

    one = _write(outcome=RunOutcome.AMBIGUOUS)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, one)


def test_winner_and_rank_projections_are_derived_from_exact_scores(
    database: sqlite3.Connection,
) -> None:
    write = _write((1, 2), outcome=RunOutcome.MATCHED)
    second = _scored_result(
        write.results[1],
        numerator="1",
        denominator="2",
        threshold="0.75",
        score=0.5,
        rank=2,
    )
    write = replace(write, results=(write.results[0], second))
    write = _bind_definition(
        database,
        write,
        2,
        [
            {
                "id": "pass",
                "required": True,
                "type": "mime_type",
                "value": "application/pdf",
                "weight": 1,
            },
            {
                "id": "fail",
                "required": False,
                "type": "mime_type",
                "value": "text/plain",
                "weight": 1,
            },
        ],
        Decimal("0.75"),
    )
    persist_completed_recognition_run(database, write)
    for invalid in (
        replace(write, winner_model_version_id=2),
        replace(
            write,
            results=(
                replace(write.results[0], rank_position=2),
                replace(write.results[1], rank_position=1),
            ),
        ),
        replace(write, results=(replace(write.results[0], rank_position=2), second)),
        replace(write, results=(replace(write.results[0], rank_position=None), second)),
    ):
        with pytest.raises(RecognitionRepositoryError):
            persist_completed_recognition_run(database, invalid)


def test_equal_scores_rank_in_snapshot_order(
    database: sqlite3.Connection,
) -> None:
    write = _write((1, 2), outcome=RunOutcome.AMBIGUOUS)
    persisted = persist_completed_recognition_run(database, write)
    assert tuple(item.rank_position for item in persisted.results) == (1, 2)


def test_rank_assigned_to_ineligible_candidate_is_rejected(
    database: sqlite3.Connection,
) -> None:
    write = _write(outcome=RunOutcome.NOT_MATCHED)
    result = _state_result(write.results[0], "definitively_ineligible", False, False)
    result = replace(result, rank_position=1)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(result,)))


def test_failed_and_non_failed_error_field_invariants(
    database: sqlite3.Connection,
) -> None:
    failed = _write((), outcome=RunOutcome.FAILED)
    for invalid in (
        replace(failed, error_code=None),
        replace(failed, error_message=""),
    ):
        with pytest.raises(RecognitionRepositoryError):
            persist_completed_recognition_run(database, invalid)
    successful = _write()
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database, replace(successful, error_code="UNEXPECTED")
        )


def test_failed_candidate_evidence_requires_null_state(
    database: sqlite3.Connection,
) -> None:
    write = _write(outcome=RunOutcome.FAILED, failed_evidence=True)
    result = _replace_json(
        write.results[0], lambda evidence: evidence.update(candidate_state="evaluated")
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(result,)))


@pytest.mark.parametrize(
    "location",
    ["snapshot", "candidate", "model", "rule", "safe_value"],
)
def test_closed_persistence_objects_reject_additional_fields(
    database: sqlite3.Connection, location: str
) -> None:
    write = _write()
    if location in {"snapshot", "candidate"}:

        def alter_snapshot(snapshot: dict[str, Any]) -> None:
            target = snapshot if location == "snapshot" else snapshot["candidates"][0]
            target["extension"] = "forbidden"

        write = _replace_snapshot(write, alter_snapshot)
    else:

        def alter_evidence(evidence: dict[str, Any]) -> None:
            if location == "model":
                evidence["model"]["extension"] = "forbidden"
            elif location == "rule":
                evidence["rules"][0]["extension"] = "forbidden"
            else:
                evidence["rules"][0]["actual"]["extension"] = "forbidden"

        write = replace(
            write, results=(_replace_json(write.results[0], alter_evidence),)
        )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)


def test_snapshot_rejects_non_deterministic_candidate_order(
    database: sqlite3.Connection,
) -> None:
    write = _write((1, 2), outcome=RunOutcome.AMBIGUOUS)
    write = _replace_snapshot(write, lambda snapshot: snapshot["candidates"].reverse())
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)


@pytest.mark.parametrize("location", ["capability", "safe_input"])
def test_snapshot_rejects_unsafe_nested_content(
    database: sqlite3.Connection, location: str
) -> None:
    marker = "https://example.invalid/private/report.pdf"
    write = _write()

    def alter(snapshot: dict[str, Any]) -> None:
        if location == "capability":
            snapshot["capabilities"] = [
                {
                    "availability": "available",
                    "configuration": {"source_path": marker},
                    "identifier": "document_metadata.v1",
                    "version": "1",
                }
            ]
        else:
            snapshot["safe_document_inputs"] = {"original_filename": marker}

    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, _replace_snapshot(write, alter))


def _failed_result_with_null_threshold(
    result: RecognitionResultWrite,
) -> RecognitionResultWrite:
    return _replace_json(
        result,
        lambda evidence: evidence.update(threshold=None),
    )


def test_failed_candidate_with_null_threshold_requires_invalid_definition(
    database: sqlite3.Connection,
) -> None:
    write = _write(outcome=RunOutcome.FAILED, failed_evidence=True)
    result = _failed_result_with_null_threshold(write.results[0])
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(result,)))


def test_null_threshold_is_rejected_without_failed_rule(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    result = _replace_json(
        write.results[0], lambda evidence: evidence.update(threshold=None)
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(result,)))


@pytest.mark.parametrize("other_state", ["evaluated", "indeterminate"])
def test_mixed_candidate_state_and_failed_candidate_persists_failed_run(
    database: sqlite3.Connection, other_state: str
) -> None:
    write = _write((1, 2), outcome=RunOutcome.FAILED, failed_evidence=True)
    if other_state == "evaluated":
        other = _result(1, write.input_snapshot_sha256 or "")
        other = replace(other, rank_position=None)
    else:
        write = _replace_snapshot(
            write,
            lambda snapshot: snapshot["safe_document_inputs"].update(
                mime_type={"availability": "unavailable"}
            ),
        )
        other = _state_result(
            _result(1, write.input_snapshot_sha256 or ""),
            "indeterminate",
            None,
            None,
        )
    failed = write.results[1]
    persisted = persist_completed_recognition_run(
        database, replace(write, results=(other, failed))
    )
    assert persisted.outcome is RunOutcome.FAILED
    assert persisted.winner is None
    assert all(item.rank_position is None for item in persisted.results)


@pytest.mark.parametrize("case", ["reversed", "partial"])
def test_complete_failed_results_remain_ordered_and_non_partial(
    database: sqlite3.Connection, case: str
) -> None:
    write = _write((1, 2), outcome=RunOutcome.FAILED, failed_evidence=True)
    results = (
        tuple(reversed(write.results)) if case == "reversed" else write.results[:1]
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=results))


def test_sensitive_raw_evidence_and_nested_error_are_redacted(
    database: sqlite3.Connection,
) -> None:
    marker = "SECRET-source https://example.invalid/private/report.pdf"
    write = _write()

    def alter(evidence: dict[str, Any]) -> None:
        evidence["rules"][0]["actual"] = {
            "kind": "original_filename",
            "value": marker,
        }

    result = _replace_json(write.results[0], alter)
    with pytest.raises(RecognitionRepositoryError) as caught:
        persist_completed_recognition_run(database, replace(write, results=(result,)))
    assert caught.value.details is None
    assert marker not in str(caught.value)
    assert marker not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "other"),
        ("type", "sha256"),
        ("required", False),
        ("weight", "2"),
    ],
)
def test_rule_identity_is_bound_to_durable_definition(
    database: sqlite3.Connection, field: str, value: object
) -> None:
    write = _write()
    altered = _replace_json(
        write.results[0], lambda evidence: evidence["rules"][0].update({field: value})
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(altered,)))


def test_threshold_is_bound_to_durable_definition(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    altered = _replace_json(
        write.results[0], lambda evidence: evidence.update(threshold="0.5")
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(altered,)))


@pytest.mark.parametrize("case", ["omitted", "additional", "duplicate"])
def test_rule_sequence_is_complete_and_unique_for_definition(
    database: sqlite3.Connection, case: str
) -> None:
    write = _write()

    def alter(evidence: dict[str, Any]) -> None:
        rule = evidence["rules"][0]
        if case == "omitted":
            evidence["rules"] = []
        else:
            extra = dict(rule)
            if case == "additional":
                extra["id"] = "extra"
            evidence["rules"].append(extra)

    altered = _replace_json(write.results[0], alter)
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(altered,)))


def test_snapshot_candidate_must_match_exact_durable_definition_bytes(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    database.execute(
        "UPDATE document_model_versions SET definition_json = ? WHERE id = 1",
        (DEFINITION + " ",),
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, write)


@pytest.mark.parametrize(
    "mime",
    [
        "Application/PDF",
        "application/pdf; charset=utf-8",
        "../../private/pdf",
        "https://x/y",
    ],
)
@pytest.mark.parametrize("location", ["snapshot", "evidence"])
def test_mime_safe_values_are_strict_and_redacted(
    database: sqlite3.Connection, location: str, mime: str
) -> None:
    marker = mime
    write = _write()
    if location == "snapshot":
        write = _replace_snapshot(
            write,
            lambda snapshot: snapshot["safe_document_inputs"].update(mime_type=mime),
        )
    else:
        write = replace(
            write,
            results=(
                _replace_json(
                    write.results[0],
                    lambda evidence: evidence["rules"][0]["actual"].update(value=mime),
                ),
            ),
        )
    with pytest.raises(RecognitionRepositoryError) as caught:
        persist_completed_recognition_run(database, write)
    assert marker not in str(caught.value) and caught.value.details is None


def test_rule_actual_must_match_durable_snapshot(
    database: sqlite3.Connection,
) -> None:
    write = _write()
    altered = _replace_json(
        write.results[0],
        lambda evidence: evidence["rules"][0]["actual"].update(value="text/plain"),
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(altered,)))


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("published_date", "2025-02-29"),
        ("published_date", "2026-1-01"),
        ("retrieved_at", "2026-02-30T00:00:00.000Z"),
        ("registered_at", "2026-01-01T00:00:00Z"),
        ("page_count_range", "0..2"),
        ("page_count_range", "3..2"),
        ("page_count_range", "01..2"),
    ],
)
def test_evidence_safe_scalars_are_semantically_canonical(
    database: sqlite3.Connection, kind: str, value: str
) -> None:
    write = _write()

    def alter(evidence: dict[str, Any]) -> None:
        evidence["rules"][0]["expected"] = {"kind": kind, "value": value}

    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(
            database,
            replace(write, results=(_replace_json(write.results[0], alter),)),
        )


@pytest.mark.parametrize(
    "change",
    [
        {
            "status": "unavailable_capability",
            "code": "CAPABILITY_UNAVAILABLE",
            "passed": None,
            "actual": None,
        },
        {
            "status": "unavailable_input",
            "code": "INPUT_UNAVAILABLE",
            "passed": None,
            "actual": None,
        },
    ],
)
def test_unavailability_status_must_match_snapshot(
    database: sqlite3.Connection, change: dict[str, object]
) -> None:
    write = _write(outcome=RunOutcome.UNSUPPORTED)

    def alter(evidence: dict[str, Any]) -> None:
        evidence["rules"][0].update(change)
        evidence.update(
            candidate_state="indeterminate",
            display_score=None,
            eligible=None,
            exact_score=None,
            required_rules_passed=None,
        )

    altered = _replace_json(write.results[0], alter)
    altered = replace(
        altered,
        score=0.0,
        eligible=False,
        required_rules_passed=False,
        rank_position=None,
    )
    with pytest.raises(RecognitionRepositoryError):
        persist_completed_recognition_run(database, replace(write, results=(altered,)))


def test_public_reads_reject_committed_candidate_prefix(
    database: sqlite3.Connection,
) -> None:
    persisted = persist_completed_recognition_run(
        database, _write((1, 2), outcome=RunOutcome.AMBIGUOUS)
    )
    database.execute(
        "DELETE FROM recognition_results WHERE recognition_run_id = ? "
        "AND model_version_id = 2",
        (persisted.id,),
    )
    database.commit()
    with pytest.raises(RecognitionRepositoryError):
        get_recognition_run(database, persisted.id)
    with pytest.raises(RecognitionRepositoryError):
        get_recognition_results(database, persisted.id)


def test_public_reads_reject_committed_corrupt_evidence(
    database: sqlite3.Connection,
) -> None:
    persisted = persist_completed_recognition_run(database, _write())
    database.execute(
        "UPDATE recognition_results SET details_json = ? WHERE recognition_run_id = ?",
        ('{"corrupt":true}', persisted.id),
    )
    database.commit()
    with pytest.raises(RecognitionRepositoryError):
        get_recognition_run(database, persisted.id)


@pytest.mark.parametrize("case", ["reordered", "additional"])
def test_public_reads_reject_committed_non_snapshot_result_sets(
    database: sqlite3.Connection, case: str
) -> None:
    persisted = persist_completed_recognition_run(
        database, _write((1, 2), outcome=RunOutcome.AMBIGUOUS)
    )
    if case == "reordered":
        database.execute(
            "UPDATE recognition_results SET id = id + 100 "
            "WHERE recognition_run_id = ? AND model_version_id = 1",
            (persisted.id,),
        )
    else:
        source = database.execute(
            "SELECT score, eligible, required_rules_passed, details_json "
            "FROM recognition_results WHERE recognition_run_id = ? LIMIT 1",
            (persisted.id,),
        ).fetchone()
        assert source is not None
        database.execute(
            "INSERT INTO recognition_results "
            "(recognition_run_id, model_version_id, score, eligible, "
            "required_rules_passed, rank_position, details_json) "
            "VALUES (?, 3, ?, ?, ?, NULL, ?)",
            (
                persisted.id,
                source["score"],
                source["eligible"],
                source["required_rules_passed"],
                source["details_json"],
            ),
        )
    database.commit()
    with pytest.raises(RecognitionRepositoryError):
        get_recognition_run(database, persisted.id)
