"""Canonical JSON for recognition snapshots and evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum


class CanonicalJSONError(ValueError):
    """A value is outside the recognition canonical-JSON value domain."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_sha256(value: object, field: str = "sha256") -> str:
    """Return a valid durable lowercase SHA-256 or fail explicitly."""

    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CanonicalJSONError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _copy(value: object) -> object:
    if isinstance(value, Enum):
        raise CanonicalJSONError("domain enums must be converted explicitly")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        raise CanonicalJSONError("recognition JSON numbers must be integers")
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError("recognition JSON object keys must be strings")
            result[key] = _copy(child)
        return result
    if isinstance(value, (list, tuple)):
        return [_copy(child) for child in value]
    raise CanonicalJSONError(
        f"unsupported recognition JSON value: {type(value).__name__}"
    )


def canonical_json_bytes(value: object) -> bytes:
    """Return the exact recognition-v1 canonical UTF-8 representation."""

    return json.dumps(
        _copy(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def bytes_sha256(value: bytes) -> str:
    """Hash bytes that have already crossed the canonical serialization boundary."""

    return hashlib.sha256(value).hexdigest()


def string_digest(value: str) -> tuple[str, int]:
    return hashlib.sha256(value.encode("utf-8")).hexdigest(), len(value)
