from __future__ import annotations

from decimal import Decimal, getcontext
from enum import IntEnum, StrEnum

import pytest

from reference_engine.recognition.canonical import (
    CanonicalJSONError,
    canonical_json,
    canonical_sha256,
)
from reference_engine.recognition.decimals import (
    ExactScore,
    RecognitionDecimalError,
    canonical_decimal,
    compare_scores,
    display_score,
    parse_decimal,
)


def test_canonical_json_orders_keys_preserves_unicode_and_escapes() -> None:
    value = {"z": ["café", "\n"], "a": 'quote"\\'}
    assert canonical_json(value) == '{"a":"quote\\"\\\\","z":["café","\\n"]}'
    assert canonical_sha256(value) == canonical_sha256(
        {"a": 'quote"\\', "z": ["café", "\n"]}
    )


@pytest.mark.parametrize("value", [1.0, Decimal("1"), {1: "x"}, {"x": {1}}, object()])
def test_canonical_json_rejects_values_outside_domain(value: object) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1.2300"), "1.23"),
        (Decimal("1E+3"), "1000"),
        (Decimal("0.00100"), "0.001"),
    ],
)
def test_canonical_decimal_normalizes(value: Decimal, expected: str) -> None:
    assert canonical_decimal(value) == expected


def test_exact_values_reject_boolean_and_float() -> None:
    for value in (True, 0.1):
        with pytest.raises(RecognitionDecimalError):
            parse_decimal(value)
    with pytest.raises(RecognitionDecimalError):
        canonical_decimal(Decimal("-0"))


def test_display_rounding_is_half_even_and_context_independent() -> None:
    old_precision = getcontext().prec
    getcontext().prec = 2
    try:
        assert display_score(ExactScore(Decimal(1), Decimal(2000000))) == "0.000000"
        assert display_score(ExactScore(Decimal(3), Decimal(2000000))) == "0.000002"
    finally:
        getcontext().prec = old_precision


def test_exact_comparison_handles_scaled_ties_and_zero_denominator() -> None:
    assert (
        compare_scores(
            ExactScore(Decimal("1"), Decimal("2")),
            ExactScore(Decimal("5.0"), Decimal("10.0")),
        )
        == 0
    )
    assert ExactScore(Decimal(0), Decimal(0)).meets(Decimal(0))
    assert not ExactScore(Decimal(0), Decimal(0)).meets(
        Decimal("0.0000000000000000001")
    )


def test_canonical_json_rejects_string_and_integer_enums() -> None:
    class Text(StrEnum):
        VALUE = "value"

    class Number(IntEnum):
        VALUE = 1

    for value in (Text.VALUE, Number.VALUE):
        with pytest.raises(CanonicalJSONError):
            canonical_json(value)
