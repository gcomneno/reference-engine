"""SQLite database connection and schema migration API."""

from reference_engine.db.artifacts import (
    Artifact,
    get_artifact,
    get_artifact_by_content,
    get_artifact_by_storage,
    register_artifact,
)
from reference_engine.db.connection import connect_database
from reference_engine.db.document_models import (
    DocumentModel,
    DocumentModelVersion,
    ModelQueryDefinition,
    get_document_model,
    get_document_model_version,
    register_document_model,
    register_document_model_version,
)
from reference_engine.db.documents import (
    Document,
    get_document,
    get_document_by_sha256,
    register_document,
)
from reference_engine.db.migrations import (
    AppliedMigration,
    MigrationReport,
    apply_migrations,
    get_applied_migrations,
)
from reference_engine.db.recognition_runs import (
    RecognitionResult,
    RecognitionRun,
    get_recognition_results,
    get_recognition_run,
    persist_completed_recognition_run,
)

__all__ = [
    "AppliedMigration",
    "Artifact",
    "DocumentModel",
    "DocumentModelVersion",
    "Document",
    "MigrationReport",
    "ModelQueryDefinition",
    "RecognitionResult",
    "RecognitionRun",
    "apply_migrations",
    "connect_database",
    "get_applied_migrations",
    "get_artifact",
    "get_artifact_by_content",
    "get_artifact_by_storage",
    "get_document_model",
    "get_document_model_version",
    "get_document",
    "get_document_by_sha256",
    "get_recognition_results",
    "get_recognition_run",
    "register_artifact",
    "register_document_model",
    "register_document_model_version",
    "register_document",
    "persist_completed_recognition_run",
]
