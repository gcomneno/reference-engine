"""Pure normalization for declared document-metadata scalar values."""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation


class MetadataScalarError(ValueError):
    """A declared metadata scalar cannot be normalized."""


def normalize_metadata_scalar(field_type: str, value: object) -> object:
    """Normalize one non-null scalar according to the binding contract."""

    if field_type == "string":
        if not isinstance(value, str):
            raise MetadataScalarError
        return unicodedata.normalize("NFC", value)
    if field_type == "integer":
        if type(value) is not int:
            raise MetadataScalarError
        return value
    if field_type == "boolean":
        if type(value) is not bool:
            raise MetadataScalarError
        return value
    if field_type == "decimal":
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise MetadataScalarError
        try:
            number = Decimal(value)
            if not number.is_finite():
                raise InvalidOperation
            return format(number.normalize(), "f")
        except (InvalidOperation, ValueError):
            raise MetadataScalarError from None
    if field_type == "date":
        try:
            if isinstance(value, date) and not isinstance(value, datetime):
                parsed = value
            elif isinstance(value, str):
                parsed = date.fromisoformat(value)
            else:
                raise TypeError
            return parsed.isoformat()
        except (TypeError, ValueError):
            raise MetadataScalarError from None
    if field_type == "datetime":
        try:
            if isinstance(value, datetime):
                parsed = value
            elif isinstance(value, str):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                raise TypeError
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
            return (
                parsed.astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
        except (AttributeError, TypeError, ValueError):
            raise MetadataScalarError from None
    raise MetadataScalarError


def is_canonical_metadata_scalar(field_type: str, value: object) -> bool:
    """Return whether a model declaration already uses canonical scalar syntax."""

    try:
        normalized = normalize_metadata_scalar(field_type, value)
        return type(value) is type(normalized) and value == normalized
    except MetadataScalarError:
        return False
