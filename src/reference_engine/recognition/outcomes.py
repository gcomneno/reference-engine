"""Candidate classification, evidence construction, ranking, and outcomes."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from functools import cmp_to_key

from reference_engine.recognition.canonical import (
    bytes_sha256,
    canonical_json_bytes,
    validate_sha256,
)
from reference_engine.recognition.decimals import (
    ExactScore,
    add_decimals,
    canonical_decimal,
    compare_scores,
    display_score,
)
from reference_engine.recognition.rules import evaluate_rule, safe_evidence_object
from reference_engine.recognition.scoring import score_equal, score_rules
from reference_engine.recognition.types import (
    ActiveCandidateSnapshot,
    CandidateEvaluation,
    CandidateState,
    CapabilitySnapshot,
    RankingResult,
    RecognitionDefinition,
    RecognitionRunSnapshot,
    RuleEvaluationStatus,
    RuleEvidence,
    RunOutcome,
    SafeString,
    SafeTextProbeSnapshot,
    TechnicalDocumentInputs,
)

_FAILED = frozenset(
    {
        RuleEvaluationStatus.INVALID_RULE_DEFINITION,
        RuleEvaluationStatus.TECHNICAL_EVALUATION_ERROR,
    }
)
_UNAVAILABLE = frozenset(
    {
        RuleEvaluationStatus.UNAVAILABLE_CAPABILITY,
        RuleEvaluationStatus.UNAVAILABLE_INPUT,
    }
)


def serialize_run_snapshot(snapshot: RecognitionRunSnapshot) -> tuple[str, str]:
    """Return the canonical durable projection and its exact UTF-8 SHA-256."""

    safe_inputs: dict[str, object] = {}
    for name, item in snapshot.safe_document_inputs.fields:
        if name in safe_inputs:
            raise ValueError("duplicate safe document input")
        if isinstance(item, SafeString):
            value: object = {"length": item.length, "sha256": item.sha256}
        elif isinstance(item, SafeTextProbeSnapshot):
            value = {
                "character_count": item.character_count,
                "limit": item.limit,
                "sha256": item.sha256,
                "truncated": item.truncated,
            }
        else:
            value = item
        safe_inputs[name] = value
    capabilities: list[dict[str, object]] = []
    for capability in snapshot.capabilities:
        configuration: dict[str, object] = {}
        for entry in capability.configuration:
            if entry.name in configuration:
                raise ValueError("duplicate capability configuration")
            configuration[entry.name] = entry.value
        capabilities.append(
            {
                "availability": capability.availability.value,
                "configuration": configuration,
                "identifier": capability.identifier,
                "version": capability.version,
            }
        )
    value = {
        "capabilities": capabilities,
        "candidates": [
            {
                "definition_sha256": item.definition_sha256,
                "model_key": item.model_key,
                "model_version_id": item.model_version_id,
                "schema_version": item.schema_version,
                "semantic_version": item.semantic_version,
                "status": item.status,
            }
            for item in snapshot.candidates
        ],
        "document_id": snapshot.document_id,
        "document_sha256": snapshot.document_sha256,
        "engine_version": snapshot.engine_version,
        "safe_document_inputs": safe_inputs,
        "snapshot_schema_version": snapshot.snapshot_schema_version,
        "source_artifact_id": snapshot.source_artifact_id,
    }
    encoded = canonical_json_bytes(value)
    return encoded.decode("utf-8"), bytes_sha256(encoded)


def _rule_object(
    rule: RuleEvidence, denominator: Decimal, *, scoreable: bool
) -> dict[str, object]:
    contribution = None
    if scoreable and rule.status in {
        RuleEvaluationStatus.EVALUATED_PASS,
        RuleEvaluationStatus.EVALUATED_FAIL,
    }:
        contribution = display_score(
            ExactScore(rule.weight if rule.passed else Decimal(0), denominator)
        )
    return {
        "actual": safe_evidence_object(rule.actual),
        "code": rule.code,
        "expected": safe_evidence_object(rule.expected),
        "id": rule.id,
        "passed": rule.passed,
        "required": rule.required,
        "score_contribution": contribution,
        "status": rule.status.value,
        "type": rule.type,
        "weight": canonical_decimal(rule.weight),
    }


def _evidence_object(
    candidate: ActiveCandidateSnapshot,
    threshold: Decimal | None,
    rules: tuple[RuleEvidence, ...],
    state: CandidateState | None,
    eligible: bool | None,
    required_passed: bool | None,
    score: ExactScore | None,
    shown_score: str | None,
    run_snapshot_sha256: str,
) -> dict[str, object]:
    denominator = (
        score.denominator
        if score is not None
        else add_decimals(tuple(rule.weight for rule in rules))
    )
    exact = (
        None
        if score is None
        else {
            "denominator": canonical_decimal(score.denominator),
            "numerator": canonical_decimal(score.numerator),
        }
    )
    return {
        "candidate_state": None if state is None else state.value,
        "display_score": shown_score,
        "eligible": eligible,
        "exact_score": exact,
        "model": {
            "definition_sha256": validate_sha256(
                candidate.definition_sha256, "model.definition_sha256"
            ),
            "key": candidate.model_key,
            "semantic_version": candidate.semantic_version,
            "version_id": candidate.model_version_id,
        },
        "recognition_evidence_schema": "recognition-candidate-evidence.v1",
        "required_rules_passed": required_passed,
        "rules": [
            _rule_object(rule, denominator, scoreable=score is not None)
            for rule in rules
        ],
        "run_snapshot_sha256": validate_sha256(
            run_snapshot_sha256, "run_snapshot_sha256"
        ),
        "threshold": None if threshold is None else canonical_decimal(threshold),
    }


def evaluate_candidate(
    candidate: ActiveCandidateSnapshot,
    definition: RecognitionDefinition,
    inputs: TechnicalDocumentInputs,
    capabilities: tuple[CapabilitySnapshot, ...],
    run_snapshot_sha256: str,
) -> CandidateEvaluation:
    rules = tuple(
        evaluate_rule(rule, inputs, capabilities) for rule in definition.rules
    )
    return complete_candidate(candidate, definition, rules, run_snapshot_sha256)


def complete_candidate(
    candidate: ActiveCandidateSnapshot,
    definition: RecognitionDefinition,
    rules: tuple[RuleEvidence, ...],
    run_snapshot_sha256: str,
) -> CandidateEvaluation:
    """Derive every candidate projection from typed evaluator evidence."""

    failed = (
        any(rule.status in _FAILED for rule in rules) or definition.threshold is None
    )
    required_failure = any(
        rule.required and rule.status is RuleEvaluationStatus.EVALUATED_FAIL
        for rule in rules
    )
    unavailable = any(rule.status in _UNAVAILABLE for rule in rules)
    unavailable_required = any(
        rule.required and rule.status in _UNAVAILABLE for rule in rules
    )
    if failed:
        state = None
        eligible = required_passed = None
        score = None
    elif required_failure:
        state, eligible, required_passed, score = (
            CandidateState.DEFINITIVELY_INELIGIBLE,
            False,
            False,
            None,
        )
    elif unavailable:
        state, eligible, required_passed, score = (
            CandidateState.INDETERMINATE,
            None,
            None if unavailable_required else True,
            None,
        )
    else:
        state = CandidateState.EVALUATED
        eligible = required_passed = True
        score = score_rules(rules)
    shown = None if score is None else display_score(score)
    obj = _evidence_object(
        candidate,
        definition.threshold,
        rules,
        state,
        eligible,
        required_passed,
        score,
        shown,
        run_snapshot_sha256,
    )
    evidence_bytes = canonical_json_bytes(obj)
    evidence_json = evidence_bytes.decode("utf-8")
    return CandidateEvaluation(
        candidate,
        definition.threshold,
        rules,
        state,
        eligible,
        required_passed,
        score,
        shown,
        evidence_json,
        bytes_sha256(evidence_bytes),
    )


def _ranking_compare(
    left: tuple[int, CandidateEvaluation], right: tuple[int, CandidateEvaluation]
) -> int:
    assert left[1].exact_score is not None and right[1].exact_score is not None
    score_order = compare_scores(left[1].exact_score, right[1].exact_score)
    return -score_order if score_order else left[0] - right[0]


def rank_and_select(evaluations: tuple[CandidateEvaluation, ...]) -> RankingResult:
    if any(item.candidate_state is None for item in evaluations):
        return RankingResult(
            tuple(replace(item, rank_position=None) for item in evaluations),
            None,
            RunOutcome.FAILED,
        )
    if any(
        item.candidate_state is CandidateState.INDETERMINATE for item in evaluations
    ):
        return RankingResult(
            tuple(replace(item, rank_position=None) for item in evaluations),
            None,
            RunOutcome.UNSUPPORTED,
        )
    ranked_pairs = sorted(
        (
            (index, item)
            for index, item in enumerate(evaluations)
            if item.eligible and item.exact_score is not None
        ),
        key=cmp_to_key(_ranking_compare),
    )
    ranks = {index: rank for rank, (index, _) in enumerate(ranked_pairs, 1)}
    ranked = tuple(
        replace(item, rank_position=ranks.get(index))
        for index, item in enumerate(evaluations)
    )
    qualifiers = [
        item
        for _, item in ranked_pairs
        if item.threshold is not None
        and item.exact_score is not None
        and item.exact_score.meets(item.threshold)
    ]
    if not qualifiers:
        return RankingResult(ranked, None, RunOutcome.NOT_MATCHED)
    highest = qualifiers[0]
    tied = [
        item
        for item in qualifiers
        if item.exact_score is not None
        and highest.exact_score is not None
        and score_equal(item.exact_score, highest.exact_score)
    ]
    if len(tied) > 1:
        return RankingResult(ranked, None, RunOutcome.AMBIGUOUS)
    winner = next(
        item
        for item in ranked
        if item.candidate.model_version_id == highest.candidate.model_version_id
    )
    return RankingResult(ranked, winner, RunOutcome.MATCHED)
