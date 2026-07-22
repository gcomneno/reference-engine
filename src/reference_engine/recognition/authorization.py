"""Pure validation of durable recognition-v1 projections used for authorization."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import cmp_to_key
from typing import Any, NoReturn, Protocol, cast

from reference_engine.recognition.canonical import canonical_json, validate_sha256
from reference_engine.recognition.decimals import (
    ExactScore,
    add_decimals,
    canonical_decimal,
    compare_scores,
    display_score,
)
from reference_engine.recognition.rules import (
    evaluate_snapshot_rule,
    expected_rule_evidence,
    parse_recognition_definition,
    safe_evidence_object,
)
from reference_engine.recognition.types import (
    MetadataExpectation,
    RecognitionDefinition,
    RuleDefinition,
    RunOutcome,
)

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
_STATES = frozenset({"evaluated", "definitively_ineligible", "indeterminate"})
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
_RUN_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)


class ModelProjection(Protocol):
    version_id: int
    model_key: str
    semantic_version: str
    definition_sha256: str


class CandidateProjection(Protocol):
    model: ModelProjection
    model_definition_json: str
    score: float
    required_rules_passed: bool | None
    eligible: bool | None
    rank_position: int | None
    evidence_json: str
    evidence_sha256: str
    rule_input_values: tuple[SensitiveRuleInput, ...]


class RunProjection(Protocol):
    run_id: int
    document_id: int
    document_sha256: str
    engine_version: str
    started_at: str
    completed_at: str
    outcome: RunOutcome
    snapshot_json: str
    snapshot_sha256: str
    candidates: tuple[CandidateProjection, ...]
    winner_model_version_id: int | None


@dataclass(frozen=True)
class ValidatedCandidate:
    source: Any
    state: str | None
    exact_score: ExactScore | None
    threshold: Decimal | None


@dataclass(frozen=True)
class SensitiveRuleInput:
    """Caller-held preimage for one privacy-hashed recognition rule."""

    rule_id: str
    value: str = dataclass_field(repr=False)


def _fail() -> NoReturn:
    raise ValueError("invalid durable recognition-v1 projection")


def _positive(value: object) -> int:
    if type(value) is not int or value <= 0:
        _fail()
    return value


def _object(text: object, digest: object) -> dict[str, object]:
    if not isinstance(text, str):
        _fail()
    assert isinstance(text, str)
    try:
        validate_sha256(digest)
        value = json.loads(text, object_pairs_hook=_unique)
        if not isinstance(value, dict) or canonical_json(value) != text:
            _fail()
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
            _fail()
    except (TypeError, ValueError):
        _fail()
    return cast(dict[str, object], value)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _embedded_hashes(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith("sha256"):
                try:
                    validate_sha256(child)
                except ValueError:
                    _fail()
            _embedded_hashes(child)
    elif isinstance(value, list):
        for child in value:
            _embedded_hashes(child)


def _safe_value(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        _fail()
    if set(value) == {"kind", "value"}:
        scalar = value.get("value")
        if scalar is not None and type(scalar) not in {str, int, bool}:
            _fail()
    elif set(value) == {"kind", "sha256", "length"}:
        try:
            validate_sha256(value.get("sha256"))
        except ValueError:
            _fail()
        if type(value.get("length")) is not int or value["length"] < 0:
            _fail()
    else:
        _fail()


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        _fail()
    assert isinstance(value, str)
    try:
        parsed = Decimal(value)
        if canonical_decimal(parsed) != value:
            _fail()
        return parsed
    except InvalidOperation:
        _fail()


def _completed(started: object, completed: object) -> bool:
    if not isinstance(started, str) or not isinstance(completed, str):
        return False
    if _RUN_UTC.fullmatch(started) is None or _RUN_UTC.fullmatch(completed) is None:
        return False
    try:
        return datetime.strptime(
            completed, "%Y-%m-%dT%H:%M:%S.%fZ"
        ) >= datetime.strptime(started, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return False


def _definition(candidate: CandidateProjection) -> RecognitionDefinition:
    if not isinstance(candidate.model_definition_json, str):
        _fail()
    try:
        if hashlib.sha256(
            candidate.model_definition_json.encode("utf-8")
        ).hexdigest() != validate_sha256(candidate.model.definition_sha256):
            _fail()
    except ValueError:
        _fail()
    definition = parse_recognition_definition(candidate.model_definition_json)
    if definition.invalid_code is not None or any(
        rule.invalid_code is not None for rule in definition.rules
    ):
        _fail()
    return definition


def _rule_type(rule: RuleDefinition) -> str:
    if rule.type is None:
        _fail()
    assert rule.type is not None
    return rule.type.value


def _rule_input(rule: RuleDefinition) -> tuple[str, str]:
    rule_type = _rule_type(rule)
    if rule_type == "filename_regex":
        return "original_filename", "document_metadata.v1"
    if rule_type in {"text_contains", "text_regex"}:
        return "recognition_text_probe", "recognition_text_probe.v1"
    if rule_type == "page_count_between":
        return "page_count", "document_metadata.v1"
    if rule_type == "metadata_equals":
        if not isinstance(rule.value, MetadataExpectation):
            _fail()
        return rule.value.field, "document_metadata.v1"
    return rule_type, "document_metadata.v1"


def _requires_sensitive_input(rule: RuleDefinition) -> bool:
    return _rule_type(rule) in {"filename_regex", "text_contains", "text_regex"}


def _unavailable(value: object) -> bool:
    return value == {"availability": "unavailable"}


def _snapshot_actual(kind: str, value: object) -> dict[str, object]:
    if kind == "recognition_text_probe":
        if not isinstance(value, dict):
            _fail()
        return {
            "kind": kind,
            "length": value.get("character_count"),
            "sha256": value.get("sha256"),
        }
    if kind in {"original_filename", "source_url"} and isinstance(value, dict):
        return {
            "kind": kind,
            "length": value.get("length"),
            "sha256": value.get("sha256"),
        }
    return {"kind": kind, "value": value}


def _validate_rule_snapshot(
    raw: dict[str, object], declared: RuleDefinition, snapshot: dict[str, object]
) -> None:
    input_name, capability_name = _rule_input(declared)
    capabilities = snapshot.get("capabilities")
    inputs = snapshot.get("safe_document_inputs")
    if not isinstance(capabilities, list) or not isinstance(inputs, dict):
        _fail()
    relevant = [
        item
        for item in capabilities
        if isinstance(item, dict) and item.get("identifier") == capability_name
    ]
    if len(relevant) != 1:
        _fail()
    capability_available = relevant[0].get("availability") == "available"
    present = input_name in inputs
    value = inputs.get(input_name)
    available_input = present and not _unavailable(value)
    status = raw.get("status")
    if status == "unavailable_capability":
        if capability_available:
            _fail()
    elif status == "unavailable_input":
        if not capability_available or available_input:
            _fail()
    elif status in {"evaluated_pass", "evaluated_fail"}:
        if not capability_available or not available_input:
            _fail()
        if raw.get("actual") != _snapshot_actual(input_name, value):
            _fail()


def _validate_candidate(
    candidate: CandidateProjection,
    snapshot_hash: str,
    snapshot: dict[str, object],
) -> ValidatedCandidate:
    if (
        type(candidate.score) is not float
        or not math.isfinite(candidate.score)
        or not 0.0 <= candidate.score <= 1.0
    ):
        _fail()
    if candidate.rank_position is not None:
        _positive(candidate.rank_position)
    definition = _definition(candidate)
    evidence = _object(candidate.evidence_json, candidate.evidence_sha256)
    _embedded_hashes(evidence)
    if (
        set(evidence) != _EVIDENCE_FIELDS
        or evidence.get("recognition_evidence_schema")
        != "recognition-candidate-evidence.v1"
        or evidence.get("run_snapshot_sha256") != snapshot_hash
    ):
        _fail()
    model = evidence.get("model")
    if not isinstance(model, dict) or set(model) != _MODEL_FIELDS:
        _fail()
    if model != {
        "definition_sha256": candidate.model.definition_sha256,
        "key": candidate.model.model_key,
        "semantic_version": candidate.model.semantic_version,
        "version_id": candidate.model.version_id,
    }:
        _fail()
    rules = evidence.get("rules")
    if (
        not isinstance(rules, list)
        or len(rules) != len(definition.rules)
        or not isinstance(candidate.rule_input_values, tuple)
    ):
        _fail()
    assert isinstance(rules, list)
    sensitive_inputs: dict[str, str] = {}
    for supplied in candidate.rule_input_values:
        if (
            not isinstance(supplied, SensitiveRuleInput)
            or not isinstance(supplied.rule_id, str)
            or not isinstance(supplied.value, str)
            or supplied.rule_id in sensitive_inputs
        ):
            _fail()
        sensitive_inputs[supplied.rule_id] = supplied.value
    required_sensitive_ids = {
        declared.id
        for raw, declared in zip(rules, definition.rules, strict=True)
        if isinstance(raw, dict)
        and raw.get("status") in {"evaluated_pass", "evaluated_fail"}
        and _requires_sensitive_input(declared)
    }
    if set(sensitive_inputs) != required_sensitive_ids:
        _fail()
    failed = unavailable = required_failed = False
    weights: list[Decimal] = []
    passed_weights: list[Decimal] = []
    for raw, declared in zip(rules, definition.rules, strict=True):
        if not isinstance(raw, dict) or set(raw) != _RULE_FIELDS:
            _fail()
        weight = _decimal(raw.get("weight"))
        if (
            raw.get("id") != declared.id
            or raw.get("type") != _rule_type(declared)
            or raw.get("required") is not declared.required
            or weight != declared.weight
        ):
            _fail()
        status, passed, code = raw.get("status"), raw.get("passed"), raw.get("code")
        _safe_value(raw.get("expected"))
        _safe_value(raw.get("actual"))
        _validate_rule_snapshot(raw, declared, snapshot)
        if raw.get("expected") != safe_evidence_object(
            expected_rule_evidence(declared)
        ):
            _fail()
        input_name, _ = _rule_input(declared)
        inputs = snapshot.get("safe_document_inputs")
        assert isinstance(inputs, dict)
        if status in {"evaluated_pass", "evaluated_fail"}:
            input_value = (
                sensitive_inputs[declared.id]
                if _requires_sensitive_input(declared)
                else inputs.get(input_name)
            )
            recomputed = evaluate_snapshot_rule(
                declared, inputs.get(input_name), input_value
            )
            if (
                raw.get("status") != recomputed.status.value
                or raw.get("passed") is not recomputed.passed
                or raw.get("actual") != safe_evidence_object(recomputed.actual)
            ):
                _fail()
        if status not in _STATUSES:
            _fail()
        if status == "evaluated_pass":
            valid = passed is True and code is None
        elif status == "evaluated_fail":
            valid = passed is False and code is None
            required_failed |= declared.required
        elif status in {"unavailable_capability", "unavailable_input"}:
            expected_code = (
                "CAPABILITY_UNAVAILABLE"
                if status == "unavailable_capability"
                else "INPUT_UNAVAILABLE"
            )
            valid = (
                passed is None and code == expected_code and raw.get("actual") is None
            )
            unavailable = True
        elif status == "invalid_rule_definition":
            valid = passed is None and code in _INVALID_CODES
            failed = True
        else:
            valid = passed is None and code in _TECHNICAL_CODES
            failed = True
        if not valid:
            _fail()
        weights.append(weight)
        passed_weights.append(weight if passed is True else Decimal(0))
    state = evidence.get("candidate_state")
    expected_state = (
        None
        if failed
        else "definitively_ineligible"
        if required_failed
        else "indeterminate"
        if unavailable
        else "evaluated"
    )
    if state != expected_state or (state is not None and state not in _STATES):
        _fail()
    expected_required: bool | None = (
        None
        if failed
        else False
        if required_failed
        else None
        if any(
            r["required"] is True
            and r["status"] in {"unavailable_capability", "unavailable_input"}
            for r in rules
        )
        else True
    )
    expected_eligible: bool | None = (
        True
        if state == "evaluated"
        else False
        if state == "definitively_ineligible"
        else None
    )
    if (
        evidence.get("required_rules_passed") is not expected_required
        or candidate.required_rules_passed is not expected_required
        or evidence.get("eligible") is not expected_eligible
        or candidate.eligible is not expected_eligible
    ):
        _fail()
    threshold_raw = evidence.get("threshold")
    threshold = None if threshold_raw is None else _decimal(threshold_raw)
    if threshold != definition.threshold:
        _fail()
    exact_raw, shown = evidence.get("exact_score"), evidence.get("display_score")
    exact: ExactScore | None = None
    if state == "evaluated":
        if not isinstance(exact_raw, dict) or set(exact_raw) != {
            "numerator",
            "denominator",
        }:
            _fail()
        exact = ExactScore(
            _decimal(exact_raw.get("numerator")), _decimal(exact_raw.get("denominator"))
        )
        if (
            exact.numerator != add_decimals(tuple(passed_weights))
            or exact.denominator != add_decimals(tuple(weights))
            or not isinstance(shown, str)
            or shown != display_score(exact)
            or Decimal(str(candidate.score)) != Decimal(shown)
        ):
            _fail()
    elif exact_raw is not None or shown is not None or candidate.score != 0.0:
        _fail()
    denominator = add_decimals(tuple(weights))
    for raw, weight in zip(rules, weights, strict=True):
        expected = (
            None
            if exact is None
            else display_score(
                ExactScore(weight if raw["passed"] is True else Decimal(0), denominator)
            )
        )
        if raw.get("score_contribution") != expected:
            _fail()
    if state != "evaluated" and candidate.rank_position is not None:
        _fail()
    return ValidatedCandidate(candidate, state, exact, threshold)


def validate_recognition_authorization(run: Any) -> tuple[ValidatedCandidate, ...]:
    """Validate a complete persisted recognition-v1 projection without I/O."""
    _positive(run.run_id)
    _positive(run.document_id)
    if (
        not isinstance(run.outcome, RunOutcome)
        or not isinstance(run.engine_version, str)
        or not run.engine_version
        or not _completed(run.started_at, run.completed_at)
    ):
        _fail()
    if run.winner_model_version_id is not None:
        _positive(run.winner_model_version_id)
    snapshot = _object(run.snapshot_json, run.snapshot_sha256)
    _embedded_hashes(snapshot)
    if (
        set(snapshot) != _SNAPSHOT_FIELDS
        or snapshot.get("snapshot_schema_version") != "recognition-run-snapshot.v1"
        or snapshot.get("document_id") != run.document_id
        or snapshot.get("document_sha256") != run.document_sha256
        or snapshot.get("engine_version") != run.engine_version
    ):
        _fail()
    _positive(snapshot.get("source_artifact_id"))
    validate_sha256(run.document_sha256)
    capabilities = snapshot.get("capabilities")
    safe_inputs = snapshot.get("safe_document_inputs")
    if not isinstance(capabilities, list) or not isinstance(safe_inputs, dict):
        _fail()
    seen_capabilities: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, dict) or set(capability) != {
            "availability",
            "configuration",
            "identifier",
            "version",
        }:
            _fail()
        identifier = capability.get("identifier")
        if (
            not isinstance(identifier, str)
            or identifier in seen_capabilities
            or capability.get("availability") not in {"available", "unavailable"}
            or not isinstance(capability.get("version"), str)
            or not isinstance(capability.get("configuration"), dict)
        ):
            _fail()
        seen_capabilities.add(identifier)
    raw_candidates = snapshot.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) != len(
        run.candidates
    ):
        _fail()
    assert isinstance(raw_candidates, list)
    identities: set[int] = set()
    ordering: list[tuple[str, str, int]] = []
    for raw, supplied in zip(raw_candidates, run.candidates, strict=True):
        if (
            not isinstance(raw, dict)
            or set(raw) != _CANDIDATE_FIELDS
            or raw.get("schema_version") != 1
            or raw.get("status") != "active"
        ):
            _fail()
        identifier = _positive(raw.get("model_version_id"))
        model_key = supplied.model.model_key
        semantic_version = supplied.model.semantic_version
        if (
            identifier in identities
            or type(supplied.model.version_id) is not int
            or identifier != supplied.model.version_id
            or not isinstance(model_key, str)
            or not model_key
            or not isinstance(semantic_version, str)
            or not semantic_version
        ):
            _fail()
        identities.add(identifier)
        if (
            raw.get("model_key") != model_key
            or raw.get("semantic_version") != semantic_version
            or raw.get("definition_sha256") != supplied.model.definition_sha256
        ):
            _fail()
        ordering.append((model_key, semantic_version, identifier))
    if ordering != sorted(ordering):
        _fail()
    validated = tuple(
        _validate_candidate(item, run.snapshot_sha256, snapshot)
        for item in run.candidates
    )
    if any(item.state in {None, "indeterminate"} for item in validated):
        if any(item.source.rank_position is not None for item in validated):
            _fail()
    else:

        def compare(left: ValidatedCandidate, right: ValidatedCandidate) -> int:
            assert left.exact_score is not None and right.exact_score is not None
            return -compare_scores(left.exact_score, right.exact_score)

        ordered = sorted(
            (item for item in validated if item.exact_score is not None),
            key=cmp_to_key(compare),
        )
        ranks = {
            item.source.model.version_id: rank for rank, item in enumerate(ordered, 1)
        }
        if any(
            item.source.rank_position != ranks.get(item.source.model.version_id)
            for item in validated
        ):
            _fail()
    qualifiers = tuple(
        item
        for item in validated
        if item.exact_score is not None
        and item.threshold is not None
        and item.exact_score.meets(item.threshold)
    )
    top: tuple[ValidatedCandidate, ...] = ()
    if qualifiers:
        best = qualifiers[0].exact_score
        assert best is not None
        for item in qualifiers[1:]:
            assert item.exact_score is not None
            if compare_scores(item.exact_score, best) > 0:
                best = item.exact_score
        top = tuple(
            item
            for item in qualifiers
            if item.exact_score is not None
            and compare_scores(item.exact_score, best) == 0
        )
    failed = any(item.state is None for item in validated)
    indeterminate = any(item.state == "indeterminate" for item in validated)
    winner = run.winner_model_version_id
    if run.outcome is RunOutcome.FAILED:
        if winner is not None or (validated and not failed):
            _fail()
    elif run.outcome is RunOutcome.UNSUPPORTED:
        if failed or not indeterminate or winner is not None:
            _fail()
    elif run.outcome is RunOutcome.AMBIGUOUS:
        if failed or indeterminate or len(top) < 2 or winner is not None:
            _fail()
    elif run.outcome is RunOutcome.MATCHED:
        if (
            failed
            or indeterminate
            or len(top) != 1
            or winner != top[0].source.model.version_id
            or top[0].source.rank_position != 1
        ):
            _fail()
    elif failed or indeterminate or qualifiers or winner is not None:
        _fail()
    return validated
