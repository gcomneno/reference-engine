"""Reference Engine package."""

from reference_engine.binding import (
    BindingOutcome,
    BindingRequest,
    ModelBindingPolicy,
    SelectionMethod,
    SystemBindingPolicy,
    bind_document,
)
from reference_engine.registration import (
    DocumentRegistrationInput,
    DocumentRegistrationResult,
    DocumentRegistrationStatus,
    register_pdf_document,
)

__all__ = [
    "BindingOutcome",
    "BindingRequest",
    "DocumentRegistrationInput",
    "DocumentRegistrationResult",
    "DocumentRegistrationStatus",
    "ModelBindingPolicy",
    "SelectionMethod",
    "SystemBindingPolicy",
    "bind_document",
    "register_pdf_document",
]
