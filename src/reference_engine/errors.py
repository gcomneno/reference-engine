"""Structured errors exposed by the Reference Engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceEngineError(Exception):
    """Base class for stable, machine-readable Reference Engine errors."""

    code: str
    message: str
    data_path: str | None = None
    details: Mapping[str, object] | None = None

    def __str__(self) -> str:
        location = f" at {self.data_path}" if self.data_path is not None else ""
        return f"{self.code}{location}: {self.message}"


class DocumentModelError(ReferenceEngineError):
    """A document model could not be loaded or validated."""


class DatabaseError(ReferenceEngineError):
    """A database connection invariant could not be established."""


class MigrationError(DatabaseError):
    """A database schema migration could not be inspected or applied."""


class RepositoryError(DatabaseError):
    """A persisted repository value conflicts with an existing value."""


class ArtifactRepositoryError(RepositoryError):
    """An artifact could not be registered consistently."""


class DocumentModelRepositoryError(RepositoryError):
    """A document model could not be registered consistently."""


class DocumentRepositoryError(RepositoryError):
    """A document row could not be registered consistently."""


class RecognitionRepositoryError(RepositoryError):
    """A completed recognition run could not be persisted consistently."""


class RecognitionOrchestrationError(ReferenceEngineError):
    """A recognition invocation could not be started safely."""


class DocumentRegistrationError(ReferenceEngineError):
    """A source document could not be registered."""
