# ruff: noqa: E501
"""Distribution and CWD-independent migration resource tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


def test_wheel_contains_migrations_and_initializes_when_isolated(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[3]
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    wheel = next(wheelhouse.glob("*.whl"))
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert (
            "reference_engine/resources/migrations/__init__.py"
            in names
        )
        assert (
            "reference_engine/resources/migrations/"
            "001_initial_schema.sql"
            in names
        )
        assert (
            "reference_engine/resources/migrations/"
            "002_recognition_run_snapshots.sql"
            in names
        )
        archive.extractall(extracted)

    outside = tmp_path / "outside"
    outside.mkdir()
    program = """
import hashlib, json
from importlib.resources import files
import reference_engine
from reference_engine.db import apply_migrations, connect_database, get_applied_migrations

c = connect_database('isolated.sqlite3')
first = apply_migrations(c)
second = apply_migrations(c)
metadata = get_applied_migrations(c)
migration_names = [
    '001_initial_schema.sql',
    '002_recognition_run_snapshots.sql',
]
resources = [
    files('reference_engine.resources.migrations').joinpath(name).read_bytes()
    for name in migration_names
]
print(json.dumps({
    'package_file': reference_engine.__file__,
    'first': [item.version for item in first.applied],
    'second': [item.version for item in second.applied],
    'sha256': [item.sha256 for item in metadata],
    'resource_sha256': [
        hashlib.sha256(resource).hexdigest()
        for resource in resources
    ],
    'columns': [
        row[1]
        for row in c.execute(
            'PRAGMA table_info(recognition_runs)'
        )
    ],
    'triggers': [
        row[0]
        for row in c.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='trigger' "
            "ORDER BY name"
        )
    ],
    'foreign_keys': c.execute(
        'PRAGMA foreign_keys'
    ).fetchone()[0],
    'foreign_key_check': c.execute(
        'PRAGMA foreign_key_check'
    ).fetchall(),
    'views': [
        row[0]
        for row in c.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='view' "
            "ORDER BY name"
        )
    ],
}))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(extracted)
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=outside,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert Path(result["package_file"]).is_relative_to(extracted)
    assert result["first"] == [1, 2]
    assert result["second"] == []
    assert result["sha256"] == result["resource_sha256"]
    assert result["foreign_keys"] == 1
    assert result["foreign_key_check"] == []
    assert "input_snapshot_json" in result["columns"]
    assert "input_snapshot_sha256" in result["columns"]
    assert "recognition_runs_snapshot_insert" in result["triggers"]
    assert "recognition_runs_snapshot_update" in result["triggers"]
    assert result["views"] == [
        "active_dataset_versions",
        "latest_validation_decisions",
        "queryable_record_fields",
        "queryable_records",
    ]
