"""Deterministic normalization and serialization of model definitions."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum

from reference_engine.errors import DocumentModelError


def _is_recognition_number_path(path: str) -> bool:
    if path == "/recognition/minimum_score":
        return True
    parts = path.split("/")
    return (
        len(parts) == 5
        and parts[1:3] == ["recognition", "rules"]
        and parts[3].isdigit()
        and parts[4] == "weight"
    )


def _normalize(value: object, path: str) -> object:
    if isinstance(value, Enum):
        raise DocumentModelError(
            "MODEL_SEMANTIC_INVALID", "Model definitions cannot contain enums.", path
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DocumentModelError(
                "MODEL_SEMANTIC_INVALID",
                "Model definitions cannot contain non-finite numbers.",
                path,
            )
        if _is_recognition_number_path(path):
            raise DocumentModelError(
                "MODEL_SEMANTIC_INVALID",
                "Recognition numbers must use integers or exact decimals.",
                path,
            )
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DocumentModelError(
                "MODEL_SEMANTIC_INVALID",
                "Model definitions cannot contain non-finite numbers.",
                path,
            )
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str):
                raise DocumentModelError(
                    "MODEL_SEMANTIC_INVALID",
                    "Model mapping keys must be strings.",
                    path,
                )
            child_path = f"{path}/{_escape_pointer(key)}"
            result[key] = _normalize(value[key], child_path)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize(item, f"{path}/{index}") for index, item in enumerate(value)]
    raise DocumentModelError(
        "MODEL_SEMANTIC_INVALID",
        f"Model contains a non-JSON-compatible value of type {type(value).__name__}.",
        path,
    )


def _escape_pointer(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def normalize_document_model(data: Mapping[str, object]) -> dict[str, object]:
    """Return a JSON-compatible copy with sorted mapping keys and ordered lists."""

    normalized = _normalize(data, "")
    if not isinstance(normalized, dict):
        raise DocumentModelError(
            "MODEL_TOP_LEVEL_INVALID", "The document model must be a mapping.", ""
        )
    return normalized


def canonicalize_document_model(data: Mapping[str, object]) -> str:
    """Serialize without insignificant whitespace or a trailing newline."""

    normalized = normalize_document_model(data)
    return _encode_json(normalized)


def _encode_json(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimal")
        fixed = format(value, "f")
        integer, separator, fractional = fixed.partition(".")
        if not separator:
            return f"{integer}.0"
        significant_fraction = fractional.rstrip("0")
        return f"{integer}.{significant_fraction or '0'}"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        return "{" + ",".join(
            f"{_encode_json(key)}:{_encode_json(value[key])}" for key in sorted(value)
        ) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_encode_json(item) for item in value) + "]"
    raise TypeError(type(value).__name__)


def compute_definition_sha256(data: Mapping[str, object]) -> str:
    """Hash the canonical UTF-8 JSON representation of a model definition."""

    canonical = canonicalize_document_model(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
