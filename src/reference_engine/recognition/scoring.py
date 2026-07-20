"""Exact candidate scoring and comparisons."""

from __future__ import annotations

from reference_engine.recognition.decimals import (
    ExactScore,
    add_decimals,
    compare_scores,
)
from reference_engine.recognition.types import RuleEvaluationStatus, RuleEvidence


def score_rules(rules: tuple[RuleEvidence, ...]) -> ExactScore:
    numerator = add_decimals(
        tuple(
            rule.weight
            for rule in rules
            if rule.status is RuleEvaluationStatus.EVALUATED_PASS
        )
    )
    denominator = add_decimals(tuple(rule.weight for rule in rules))
    return ExactScore(numerator, denominator)


def score_equal(left: ExactScore, right: ExactScore) -> bool:
    return compare_scores(left, right) == 0
