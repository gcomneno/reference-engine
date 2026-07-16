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
from reference_engine.db.migrations import (
    AppliedMigration,
    MigrationReport,
    apply_migrations,
    get_applied_migrations,
)

__all__ = [
    "AppliedMigration",
    "Artifact",
    "DocumentModel",
    "DocumentModelVersion",
    "MigrationReport",
    "ModelQueryDefinition",
    "apply_migrations",
    "connect_database",
    "get_applied_migrations",
    "get_artifact",
    "get_artifact_by_content",
    "get_artifact_by_storage",
    "get_document_model",
    "get_document_model_version",
    "register_artifact",
    "register_document_model",
    "register_document_model_version",
]
