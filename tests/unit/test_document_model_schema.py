"""Contract tests for the document model YAML v1 JSON Schema."""

from __future__ import annotations

import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "document-model-v1.schema.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "models"


def load_schema() -> dict[str, Any]:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("The document model schema must be a JSON object.")
    return cast(dict[str, Any], data)


def load_fixture(name: str) -> dict[str, Any]:
    data = yaml.safe_load((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Fixture {name} must contain a YAML mapping.")
    return cast(dict[str, Any], data)


def validation_errors(data: dict[str, Any]) -> list[ValidationError]:
    validator = Draft202012Validator(load_schema())
    return sorted(
        validator.iter_errors(data),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )


def assert_error_at(data: dict[str, Any], path: tuple[object, ...]) -> None:
    errors = validation_errors(data)
    actual_paths = [tuple(error.absolute_path) for error in errors]
    assert path in actual_paths, (
        f"Expected validation error at {path!r}; got {actual_paths!r}"
    )


def test_schema_is_valid_draft_2020_12() -> None:
    schema = load_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_packaged_schema_matches_authoritative_schema_byte_for_byte() -> None:
    packaged_schema = files("reference_engine.resources").joinpath(
        "document-model-v1.schema.json"
    )

    assert packaged_schema.read_bytes() == SCHEMA_PATH.read_bytes()


@pytest.mark.parametrize(
    "fixture_name",
    ["valid-minimal.yaml", "valid-complete.yaml"],
)
def test_valid_fixtures_pass(fixture_name: str) -> None:
    assert validation_errors(load_fixture(fixture_name)) == []


@pytest.mark.parametrize(
    ("fixture_name", "expected_path"),
    [
        ("invalid-schema-version.yaml", ("schema_version",)),
        ("invalid-field-type.yaml", ("records", "fields", "record_id")),
        ("invalid-recognition-rule.yaml", ("recognition", "rules", 0)),
        ("invalid-query.yaml", ("queries", 0)),
        ("invalid-natural-key.yaml", ("records", "natural_key")),
    ],
)
def test_invalid_fixtures_fail_at_intended_path(
    fixture_name: str,
    expected_path: tuple[object, ...],
) -> None:
    assert_error_at(load_fixture(fixture_name), expected_path)


def test_complete_fixture_preserves_utf8_content() -> None:
    data = load_fixture("valid-complete.yaml")

    assert data["model"]["title"] == "Café observation model"
    assert validation_errors(data) == []


def test_closed_top_level_rejects_unknown_properties() -> None:
    data = load_fixture("valid-minimal.yaml")
    data["unexpected"] = True

    assert_error_at(data, ())


def test_binding_policy_is_closed_and_explicit() -> None:
    data = load_fixture("valid-minimal.yaml")
    data["binding"] = {
        "allow_automatic": False,
        "allow_manual": True,
        "allow_explicit_cli": True,
    }
    assert validation_errors(data) == []
    del data["binding"]["allow_automatic"]
    assert_error_at(data, ("binding",))


def test_metadata_constant_is_declared_data_not_code() -> None:
    data = load_fixture("valid-minimal.yaml")
    data["document_metadata"] = {
        "fields": {
            "authority": {
                "type": "string",
                "required": True,
                "constant": "Synthetic Authority",
            }
        }
    }
    assert validation_errors(data) == []


@pytest.mark.parametrize(
    ("field_type", "value"),
    [("integer", True), ("boolean", 1), ("string", 1), ("decimal", 1.5)],
)
def test_metadata_scalar_declaration_rejects_wrong_non_null_type(
    field_type: str, value: object
) -> None:
    data = load_fixture("valid-minimal.yaml")
    data["document_metadata"] = {
        "fields": {
            "choice": {
                "type": field_type,
                "required": False,
                "default": value,
            }
        }
    }
    assert_error_at(data, ("document_metadata", "fields", "choice"))


def test_non_nullable_metadata_declaration_rejects_null_default() -> None:
    data = load_fixture("valid-minimal.yaml")
    data["document_metadata"] = {
        "fields": {"choice": {"type": "string", "required": False, "default": None}}
    }
    assert_error_at(data, ("document_metadata", "fields", "choice"))


@pytest.mark.parametrize(
    ("field_type", "value"),
    [
        ("date", "not-a-date"),
        ("datetime", "tomorrow"),
        ("decimal", "NaN"),
        ("decimal", "Infinity"),
    ],
)
def test_schema_leaves_lexical_metadata_checks_to_semantic_validation(
    field_type: str, value: str
) -> None:
    data = load_fixture("valid-minimal.yaml")
    data["document_metadata"] = {
        "fields": {"choice": {"type": field_type, "required": False, "default": value}}
    }
    assert validation_errors(data) == []


@pytest.mark.parametrize("forbidden_property", ["sql", "python", "shell", "command"])
def test_query_rejects_executable_properties(
    forbidden_property: str,
) -> None:
    data = load_fixture("valid-minimal.yaml")
    query = cast(dict[str, Any], data["queries"][0])
    query[forbidden_property] = "do something unsafe"

    assert_error_at(data, ("queries", 0))


@pytest.mark.parametrize(
    "handler",
    [
        "/tmp/handler.py",
        "sample.module:callable",
        "sample.module();",
        "import os",
        "sample-module.handler",
        "sample",
    ],
)
def test_python_handler_requires_registered_identifier(handler: str) -> None:
    data = deepcopy(load_fixture("valid-complete.yaml"))
    extraction = cast(dict[str, Any], data["extraction"])
    extraction["handler"] = handler

    assert_error_at(data, ("extraction",))


def test_schema_contains_no_ascit_specific_vocabulary() -> None:
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    assert "ascit" not in schema_text
