"""Pure deterministic recognition core and public orchestration boundary."""

from typing import TYPE_CHECKING, Any

from reference_engine.recognition.outcomes import (
    evaluate_candidate,
    rank_and_select,
    serialize_run_snapshot,
)
from reference_engine.recognition.rules import (
    evaluate_rule,
    parse_recognition_definition,
)

__all__ = [
    "evaluate_candidate",
    "evaluate_rule",
    "parse_recognition_definition",
    "rank_and_select",
    "recognize_document",
    "serialize_run_snapshot",
]

if TYPE_CHECKING:
    from reference_engine.recognition.orchestration import recognize_document


def __getattr__(name: str) -> Any:
    if name == "recognize_document":
        from reference_engine.recognition.orchestration import recognize_document

        return recognize_document
    raise AttributeError(name)
