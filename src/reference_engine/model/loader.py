"""Safe document model file loading."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import ScalarNode

from reference_engine.errors import DocumentModelError
from reference_engine.model.normalization import (
    canonicalize_document_model,
    compute_definition_sha256,
    normalize_document_model,
)
from reference_engine.model.types import LoadedDocumentModel
from reference_engine.model.validation import validate_document_model


class _ExactNumberSafeLoader(yaml.SafeLoader):
    """Per-call safe loader retaining YAML floating scalars as exact decimals."""


def _construct_decimal(loader: yaml.SafeLoader, node: ScalarNode) -> Decimal:
    value = loader.construct_scalar(node).replace("_", "")
    try:
        return Decimal(value)
    except ArithmeticError as error:
        raise ConstructorError(
            None, None, "invalid decimal scalar", node.start_mark
        ) from error


_ExactNumberSafeLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def _read_model(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise DocumentModelError(
            "MODEL_FILE_NOT_FOUND", "The requested model file does not exist."
        ) from error
    except (OSError, UnicodeError) as error:
        raise DocumentModelError(
            "MODEL_FILE_UNREADABLE", "The requested model file could not be read."
        ) from error


def _parse_yaml(text: str) -> Mapping[str, object]:
    try:
        parsed: object = yaml.load(text, Loader=_ExactNumberSafeLoader)
    except ConstructorError as error:
        raise DocumentModelError(
            "MODEL_YAML_UNSAFE", "The model contains a prohibited YAML tag."
        ) from error
    except yaml.YAMLError as error:
        raise DocumentModelError(
            "MODEL_YAML_INVALID", "The model is not syntactically valid YAML."
        ) from error
    if parsed is None:
        raise DocumentModelError(
            "MODEL_TOP_LEVEL_INVALID", "The document model is empty.", ""
        )
    if not isinstance(parsed, Mapping) or not all(
        isinstance(key, str) for key in parsed
    ):
        raise DocumentModelError(
            "MODEL_TOP_LEVEL_INVALID",
            "The document model must be a string-keyed mapping.",
            "",
        )
    return parsed


def load_document_model(path: str | Path) -> LoadedDocumentModel:
    """Read exactly one YAML model, then validate, normalize, serialize, and hash it."""

    source_path = Path(path)
    data = _parse_yaml(_read_model(source_path))
    validate_document_model(data)
    normalized = normalize_document_model(data)
    canonical = canonicalize_document_model(normalized)
    definition_sha256 = compute_definition_sha256(normalized)
    return LoadedDocumentModel(
        source_path=source_path,
        data=data,
        normalized_data=normalized,
        canonical_json=canonical,
        definition_sha256=definition_sha256,
    )
