"""Public recognition orchestration tests with synthetic durable inputs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from reference_engine.db import (
    RecognitionRun,
    apply_migrations,
    connect_database,
    get_recognition_run,
)
from reference_engine.errors import (
    RecognitionOrchestrationError,
    RecognitionRepositoryError,
)
from reference_engine.recognition import recognize_document
from reference_engine.recognition.canonical import canonical_json
from reference_engine.recognition.types import (
    Availability,
    CandidateState,
    CapabilitySnapshot,
    ProbeAcquisitionStatus,
    RecognitionProbeAcquisition,
    RecognitionTextProbe,
    RuleEvaluationStatus,
    RunOutcome,
    SafeConfigurationEntry,
)

HASH = "a" * 64
REGISTERED = "2026-01-01T00:00:00.000Z"


@pytest.fixture
def database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "orchestration.sqlite3")
    apply_migrations(connection)
    connection.execute(
        "INSERT INTO artifacts VALUES "
        "(1, 'source_document', 'vault', 'canonical', 'private/document.pdf', "
        "?, 'application/pdf', 123, ?, ?)",
        (HASH, REGISTERED, REGISTERED),
    )
    connection.execute(
        "INSERT INTO documents VALUES "
        "(1, 1, ?, 'private-name.pdf', 'https://secret.invalid/document', NULL, "
        "NULL, 3, ?)",
        (HASH, REGISTERED),
    )
    connection.commit()
    return connection


def _capabilities(limit: int = 32) -> tuple[CapabilitySnapshot, ...]:
    return (
        CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),
        CapabilitySnapshot(
            "recognition_text_probe.v1",
            "1",
            Availability.AVAILABLE,
            (SafeConfigurationEntry("maximum_code_points", limit),),
        ),
    )


def _clock() -> Callable[[], datetime]:
    current = datetime(2026, 1, 1, tzinfo=UTC)

    def tick() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    return tick


def _probe(text: str = "sensitive probe phrase") -> RecognitionProbeAcquisition:
    return RecognitionProbeAcquisition(
        ProbeAcquisitionStatus.AVAILABLE_WITH_PROBE,
        RecognitionTextProbe(text, 32, False, "recognition_text_probe.v1", "1"),
    )


def _model(
    connection: sqlite3.Connection,
    *,
    identifier: int = 1,
    key: str = "model.alpha",
    version: str = "1.0.0",
    value: str = "application/pdf",
    status: str = "active",
    rules: list[dict[str, object]] | None = None,
    minimum_score: int = 1,
) -> None:
    definition = canonical_json(
        {
            "recognition": {
                "ambiguity_policy": "reject",
                "minimum_score": minimum_score,
                "rules": rules
                or [
                    {
                        "id": f"mime{identifier}",
                        "required": True,
                        "type": "mime_type",
                        "value": value,
                        "weight": 1,
                    }
                ],
            }
        }
    )
    digest = hashlib.sha256(definition.encode()).hexdigest()
    artifact_id = identifier + 10
    connection.execute(
        "INSERT INTO artifacts VALUES (?, 'document_model', 'vault', 'canonical', "
        "?, ?, 'application/json', 1, ?, ?)",
        (artifact_id, f"models/{identifier}.json", digest, REGISTERED, REGISTERED),
    )
    connection.execute(
        "INSERT INTO document_models VALUES (?, ?, 'title', 'document', 'record', ?)",
        (identifier, key, REGISTERED),
    )
    connection.execute(
        "INSERT INTO document_model_versions VALUES (?, ?, ?, 1, ?, '1', ?, ?, ?, ?)",
        (
            identifier,
            identifier,
            version,
            status,
            artifact_id,
            definition,
            digest,
            REGISTERED,
        ),
    )
    connection.commit()


def _recognize(database: sqlite3.Connection) -> RecognitionRun:
    return recognize_document(
        database,
        document_id=1,
        engine_version="engine/1",
        capabilities=_capabilities(),
        probe_acquisition=_probe(),
        clock=_clock(),
    )


def test_unambiguous_match_round_trips_without_binding(
    database: sqlite3.Connection,
) -> None:
    _model(database)
    run = _recognize(database)
    assert run.outcome is RunOutcome.MATCHED
    assert run.winner is not None
    assert run == get_recognition_run(database, run.id)
    assert database.execute("SELECT count(*) FROM document_bindings").fetchone()[0] == 0


def test_empty_candidates_and_no_qualifier(database: sqlite3.Connection) -> None:
    empty = _recognize(database)
    assert empty.outcome is RunOutcome.NOT_MATCHED
    assert empty.results == ()
    _model(database, value="text/plain")
    no_match = _recognize(database)
    assert no_match.outcome is RunOutcome.NOT_MATCHED
    assert len(no_match.results) == 1


def test_repeated_runs_are_append_only_semantically_equivalent_and_private(
    database: sqlite3.Connection,
) -> None:
    _model(database)
    first = _recognize(database)
    second = _recognize(database)
    assert first.id != second.id
    assert first.input_snapshot_sha256 == second.input_snapshot_sha256
    assert first.results[0].details_sha256 == second.results[0].details_sha256
    durable = (first.input_snapshot_json or "") + first.results[0].details_json
    assert "private-name.pdf" not in durable
    assert "https://secret.invalid" not in durable
    assert "sensitive probe phrase" not in durable


def test_candidate_order_is_key_then_lexical_version_then_id(
    database: sqlite3.Connection,
) -> None:
    _model(database, identifier=1, key="model.z", version="2.0.0")
    _model(database, identifier=2, key="model.a", version="10.0.0")
    run = _recognize(database)
    snapshot = json.loads(run.input_snapshot_json or "{}")
    assert [item["model_key"] for item in snapshot["candidates"]] == [
        "model.a",
        "model.z",
    ]


def test_unknown_document_writes_nothing(database: sqlite3.Connection) -> None:
    with pytest.raises(RecognitionOrchestrationError) as caught:
        recognize_document(
            database,
            document_id=999,
            engine_version="engine/1",
            capabilities=_capabilities(),
            probe_acquisition=_probe(),
            clock=_clock(),
        )
    assert caught.value.code == "RECOGNITION_DOCUMENT_UNKNOWN"
    assert database.execute("SELECT count(*) FROM recognition_runs").fetchone()[0] == 0


def test_caller_transaction_is_preserved_and_can_rollback(
    database: sqlite3.Connection,
) -> None:
    _model(database)
    database.execute("BEGIN")
    database.execute("UPDATE documents SET page_count = 4 WHERE id = 1")
    run = _recognize(database)
    assert database.in_transaction
    assert get_recognition_run(database, run.id) == run
    database.rollback()
    assert get_recognition_run(database, run.id) is None
    assert database.execute("SELECT page_count FROM documents").fetchone()[0] == 3


def test_pre_snapshot_failure_is_durable_without_snapshot(
    database: sqlite3.Connection,
) -> None:
    database.execute("UPDATE artifacts SET kind = 'document' WHERE id = 1")
    database.commit()
    run = _recognize(database)
    assert run.outcome is RunOutcome.FAILED
    assert run.error_code == "SNAPSHOT_FAILED"
    assert run.input_snapshot_json is None
    assert run.results == ()


def test_post_snapshot_interruption_keeps_snapshot_without_prefix(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(database)

    def interrupted(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private failure")

    monkeypatch.setattr(
        "reference_engine.recognition.orchestration.evaluate_candidate", interrupted
    )
    run = _recognize(database)
    assert run.outcome is RunOutcome.FAILED
    assert run.error_code == "EVALUATION_FAILED"
    assert run.input_snapshot_json is not None
    assert run.results == ()
    assert "private failure" not in (run.error_message or "")


def test_invalid_active_definition_is_failed_with_complete_results(
    database: sqlite3.Connection,
) -> None:
    _model(database)
    invalid = canonical_json({"recognition": {"rules": []}})
    digest = hashlib.sha256(invalid.encode()).hexdigest()
    database.execute(
        "UPDATE document_model_versions SET definition_json = ?, definition_sha256 = ?",
        (invalid, digest),
    )
    database.commit()
    run = _recognize(database)
    assert run.outcome is RunOutcome.FAILED
    assert run.error_code == "EVALUATION_FAILED"
    assert len(run.results) == 1
    assert run.winner is None


def test_unavailable_probe_input_is_unsupported(database: sqlite3.Connection) -> None:
    _model(database)
    definition = canonical_json(
        {
            "recognition": {
                "ambiguity_policy": "reject",
                "minimum_score": 1,
                "rules": [
                    {
                        "id": "text1",
                        "required": True,
                        "type": "text_contains",
                        "value": "needle",
                        "weight": 1,
                    }
                ],
            }
        }
    )
    digest = hashlib.sha256(definition.encode()).hexdigest()
    database.execute(
        "UPDATE document_model_versions SET definition_json = ?, definition_sha256 = ?",
        (definition, digest),
    )
    database.commit()
    run = recognize_document(
        database,
        document_id=1,
        engine_version="engine/1",
        capabilities=_capabilities(),
        probe_acquisition=RecognitionProbeAcquisition(
            ProbeAcquisitionStatus.AVAILABLE_NO_INPUT
        ),
        clock=_clock(),
    )
    assert run.outcome is RunOutcome.UNSUPPORTED
    assert run.winner is None
    assert database.execute("SELECT count(*) FROM document_bindings").fetchone()[0] == 0


def test_definitive_candidate_ineligibility_is_not_matched(
    database: sqlite3.Connection,
) -> None:
    _model(database, value="text/plain")
    run = _recognize(database)
    assert run.outcome is RunOutcome.NOT_MATCHED
    assert run.winner is None
    details = json.loads(run.results[0].details_json)
    assert details["candidate_state"] == CandidateState.DEFINITIVELY_INELIGIBLE


def test_top_score_ambiguity_has_no_winner(database: sqlite3.Connection) -> None:
    _model(database, identifier=1, key="model.alpha")
    _model(database, identifier=2, key="model.beta")
    run = _recognize(database)
    assert run.outcome is RunOutcome.AMBIGUOUS
    assert run.winner is None
    assert [item.rank_position for item in run.results] == [1, 2]


def test_optional_unavailable_input_allows_definitive_result(
    database: sqlite3.Connection,
) -> None:
    _model(
        database,
        rules=[
            {
                "id": "required",
                "required": True,
                "type": "mime_type",
                "value": "text/plain",
                "weight": 1,
            },
            {
                "id": "optional",
                "required": False,
                "type": "text_contains",
                "value": "needle",
                "weight": 1,
            },
        ],
    )
    run = recognize_document(
        database,
        document_id=1,
        engine_version="engine/1",
        capabilities=_capabilities(),
        probe_acquisition=RecognitionProbeAcquisition(
            ProbeAcquisitionStatus.AVAILABLE_NO_INPUT
        ),
        clock=_clock(),
    )
    assert run.outcome is RunOutcome.NOT_MATCHED
    assert run.winner is None
    rules = json.loads(run.results[0].details_json)["rules"]
    assert rules[1]["status"] == RuleEvaluationStatus.UNAVAILABLE_INPUT


def test_unavailable_required_capability_is_unsupported(
    database: sqlite3.Connection,
) -> None:
    _model(
        database,
        rules=[
            {
                "id": "text",
                "required": True,
                "type": "text_contains",
                "value": "needle",
                "weight": 1,
            }
        ],
    )
    capabilities = (
        CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),
        CapabilitySnapshot(
            "recognition_text_probe.v1",
            "1",
            Availability.UNAVAILABLE,
            (SafeConfigurationEntry("maximum_code_points", 32),),
        ),
    )
    run = recognize_document(
        database,
        document_id=1,
        engine_version="engine/1",
        capabilities=capabilities,
        probe_acquisition=RecognitionProbeAcquisition(
            ProbeAcquisitionStatus.AVAILABLE_NO_INPUT
        ),
        clock=_clock(),
    )
    assert run.outcome is RunOutcome.UNSUPPORTED
    assert run.winner is None
    assert database.execute("SELECT count(*) FROM document_bindings").fetchone()[0] == 0


def test_bounded_text_probe_is_evaluated_successfully(
    database: sqlite3.Connection,
) -> None:
    _model(
        database,
        rules=[
            {
                "id": "text",
                "required": True,
                "type": "text_contains",
                "value": "probe phrase",
                "weight": 1,
            }
        ],
    )
    run = _recognize(database)
    assert run.outcome is RunOutcome.MATCHED
    details = json.loads(run.results[0].details_json)
    assert details["rules"][0]["status"] == RuleEvaluationStatus.EVALUATED_PASS
    snapshot = json.loads(run.input_snapshot_json or "{}")
    safe_probe = snapshot["safe_document_inputs"]["recognition_text_probe"]
    assert safe_probe["character_count"] == len("sensitive probe phrase")
    assert "sensitive probe phrase" not in (run.input_snapshot_json or "")


def test_technical_evaluation_error_is_a_complete_failed_candidate(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(database)

    def fail_evaluator(*args: object) -> bool:
        raise RuntimeError("private evaluator detail")

    monkeypatch.setattr("reference_engine.recognition.rules._matches", fail_evaluator)
    run = _recognize(database)
    assert run.outcome is RunOutcome.FAILED
    assert run.winner is None
    assert len(run.results) == 1
    details = json.loads(run.results[0].details_json)
    assert (
        details["rules"][0]["status"] == RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR
    )
    assert "private evaluator detail" not in run.results[0].details_json
    assert database.execute("SELECT count(*) FROM document_bindings").fetchone()[0] == 0


def _repository_failure(*args: object, **kwargs: object) -> RecognitionRun:
    raise RecognitionRepositoryError(
        "RECOGNITION_REPOSITORY_ERROR",
        "Recognition repository operation failed.",
        details=None,
    )


def test_service_owned_transaction_rolls_back_persistence_failure(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "reference_engine.recognition.orchestration.persist_completed_recognition_run",
        _repository_failure,
    )
    with pytest.raises(RecognitionRepositoryError):
        _recognize(database)
    assert not database.in_transaction
    assert database.execute("SELECT count(*) FROM recognition_runs").fetchone()[0] == 0


def test_caller_owned_transaction_survives_persistence_failure(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    database.execute("BEGIN")
    database.execute("UPDATE documents SET page_count = 9 WHERE id = 1")
    monkeypatch.setattr(
        "reference_engine.recognition.orchestration.persist_completed_recognition_run",
        _repository_failure,
    )
    with pytest.raises(RecognitionRepositoryError):
        _recognize(database)
    assert database.in_transaction
    assert database.execute("SELECT page_count FROM documents").fetchone()[0] == 9
    database.rollback()
    assert database.execute("SELECT page_count FROM documents").fetchone()[0] == 3


@pytest.mark.parametrize("malformed", ["entry", "status"])
def test_malformed_public_context_is_closed_and_private(
    database: sqlite3.Connection, malformed: str
) -> None:
    private = "private-malformed-value"
    capabilities = _capabilities()
    acquisition = _probe()
    if malformed == "entry":
        capabilities = (
            capabilities[0],
            CapabilitySnapshot(
                "recognition_text_probe.v1",
                "1",
                Availability.AVAILABLE,
                cast(Any, (private,)),
            ),
        )
    else:
        acquisition = RecognitionProbeAcquisition(cast(Any, private))
    with pytest.raises(RecognitionOrchestrationError) as caught:
        recognize_document(
            database,
            document_id=1,
            engine_version="engine/1",
            capabilities=capabilities,
            probe_acquisition=acquisition,
            clock=_clock(),
        )
    assert caught.value.code == "RECOGNITION_INVOCATION_INVALID"
    assert caught.value.details is None
    assert caught.value.__cause__ is None
    assert private not in str(caught.value)
