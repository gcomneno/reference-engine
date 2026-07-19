"""Pure deterministic recognition core."""

from reference_engine.recognition.outcomes import evaluate_candidate, rank_and_select
from reference_engine.recognition.rules import (
    evaluate_rule,
    parse_recognition_definition,
)

__all__ = [
    "evaluate_candidate",
    "evaluate_rule",
    "parse_recognition_definition",
    "rank_and_select",
]
