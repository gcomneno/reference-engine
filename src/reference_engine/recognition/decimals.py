"""Exact decimal and rational operations used by recognition decisions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast


class RecognitionDecimalError(ValueError):
    pass


def parse_decimal(value: object) -> Decimal:
    """Accept a finite JSON decimal value without accepting Boolean or float."""

    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise RecognitionDecimalError("value must be an exact JSON number")
    result = Decimal(value)
    if not result.is_finite() or result.is_zero() and result.is_signed():
        raise RecognitionDecimalError("value must be finite")
    return result


def canonical_decimal(value: Decimal) -> str:
    if not value.is_finite() or value < 0 or value.is_zero() and value.is_signed():
        raise RecognitionDecimalError("canonical decimals are finite and non-negative")
    if value.is_zero():
        return "0"
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    if sign:
        raise RecognitionDecimalError("canonical decimals are non-negative")
    raw = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        return raw + "0" * exponent
    point = len(raw) + exponent
    if point <= 0:
        result = "0." + "0" * (-point) + raw
    else:
        result = raw[:point] + "." + raw[point:]
    return result.rstrip("0").rstrip(".")


def validate_threshold(value: object) -> Decimal:
    result = parse_decimal(value)
    if result < 0 or result > 1:
        raise RecognitionDecimalError("threshold must be in [0, 1]")
    return result


def validate_weight(value: object) -> Decimal:
    result = parse_decimal(value)
    if result < 0 or result > 1_000_000:
        raise RecognitionDecimalError("weight must be in [0, 1000000]")
    return result


@dataclass(frozen=True)
class ExactScore:
    numerator: Decimal
    denominator: Decimal

    def meets(self, threshold: Decimal) -> bool:
        if not self.denominator:
            return threshold == 0
        numerator_num, numerator_den = self.numerator.as_integer_ratio()
        denominator_num, denominator_den = self.denominator.as_integer_ratio()
        threshold_num, threshold_den = threshold.as_integer_ratio()
        return (
            numerator_num * denominator_den * threshold_den
            >= denominator_num * threshold_num * numerator_den
        )


def add_decimals(values: tuple[Decimal, ...]) -> Decimal:
    """Add finite non-negative decimals without consulting Decimal context."""

    if not values:
        return Decimal(0)
    exponents = tuple(value.as_tuple().exponent for value in values)
    assert all(isinstance(exponent, int) for exponent in exponents)
    common_exponent = min(cast(int, exponent) for exponent in exponents)
    total = 0
    for value in values:
        sign, digits, exponent = value.as_tuple()
        assert isinstance(exponent, int) and not sign
        coefficient = int("".join(str(digit) for digit in digits) or "0")
        total += coefficient * 10 ** (exponent - common_exponent)
    digits = tuple(int(character) for character in str(total))
    return Decimal((0, digits, common_exponent))


def compare_scores(left: ExactScore, right: ExactScore) -> int:
    left_num, left_den = (
        (Decimal(0), Decimal(1))
        if not left.denominator
        else (left.numerator, left.denominator)
    )
    right_num, right_den = (
        (Decimal(0), Decimal(1))
        if not right.denominator
        else (right.numerator, right.denominator)
    )
    ln, lns = left_num.as_integer_ratio()
    ld, lds = left_den.as_integer_ratio()
    rn, rns = right_num.as_integer_ratio()
    rd, rds = right_den.as_integer_ratio()
    product_left = ln * rd * lds * rns
    product_right = rn * ld * rds * lns
    return (product_left > product_right) - (product_left < product_right)


def display_score(score: ExactScore) -> str:
    """Round an exact decimal ratio to six places using integer half-even."""

    if not score.denominator:
        scaled = 0
    else:
        n_num, n_den = score.numerator.as_integer_ratio()
        d_num, d_den = score.denominator.as_integer_ratio()
        numerator = n_num * d_den * 1_000_000
        denominator = n_den * d_num
        quotient, remainder = divmod(numerator, denominator)
        doubled = remainder * 2
        scaled = quotient + int(
            doubled > denominator or (doubled == denominator and quotient % 2 == 1)
        )
    return f"{scaled // 1_000_000}.{scaled % 1_000_000:06d}"
