"""Deterministic normalization and serialization of model definitions."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

from reference_engine.errors import DocumentModelError


def _normalize(value: object, path: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
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
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_definition_sha256(data: Mapping[str, object]) -> str:
    """Hash the canonical UTF-8 JSON representation of a model definition."""

    canonical = canonicalize_document_model(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
