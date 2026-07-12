"""Public document model loading and validation API."""

from reference_engine.model.loader import load_document_model
from reference_engine.model.normalization import (
    canonicalize_document_model,
    compute_definition_sha256,
    normalize_document_model,
)
from reference_engine.model.types import LoadedDocumentModel
from reference_engine.model.validation import validate_document_model

__all__ = [
    "LoadedDocumentModel",
    "canonicalize_document_model",
    "compute_definition_sha256",
    "load_document_model",
    "normalize_document_model",
    "validate_document_model",
]
