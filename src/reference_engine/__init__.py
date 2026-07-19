"""Reference Engine package."""

from reference_engine.registration import (
    DocumentRegistrationInput,
    DocumentRegistrationResult,
    DocumentRegistrationStatus,
    register_pdf_document,
)

__all__ = [
    "DocumentRegistrationInput",
    "DocumentRegistrationResult",
    "DocumentRegistrationStatus",
    "register_pdf_document",
]
