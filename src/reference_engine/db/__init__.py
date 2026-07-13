"""SQLite database connection and schema migration API."""

from reference_engine.db.connection import connect_database
from reference_engine.db.migrations import (
    AppliedMigration,
    MigrationReport,
    apply_migrations,
    get_applied_migrations,
)

__all__ = [
    "AppliedMigration",
    "MigrationReport",
    "apply_migrations",
    "connect_database",
    "get_applied_migrations",
]
