"""Managed SQLite connections."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from reference_engine.errors import DatabaseError


def connect_database(path: str | Path) -> sqlite3.Connection:
    """Open SQLite with row access and mandatory foreign-key enforcement."""

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise DatabaseError(
            code="DATABASE_FOREIGN_KEYS_UNAVAILABLE",
            message="SQLite foreign-key enforcement could not be enabled.",
        )
    return connection
