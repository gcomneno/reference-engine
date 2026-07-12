"""Focused tests for safe, deterministic document model loading."""

from __future__ import annotations

import importlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from reference_engine.errors import DocumentModelError, ReferenceEngineError
from reference_engine.model import (
    canonicalize_document_model,
    compute_definition_sha256,
    load_document_model,
    normalize_document_model,
    validate_document_model,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "models"


def fixture_data(name: str = "valid-minimal.yaml") -> dict[str, Any]:
    value: object = yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def write_yaml(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["valid-minimal.yaml", "valid-complete.yaml"])
def test_loads_existing_valid_models(name: str) -> None:
    loaded = load_document_model(FIXTURES / name)

    assert loaded.source_path == FIXTURES / name
    assert len(loaded.definition_sha256) == 64
    assert loaded.definition_sha256 == compute_definition_sha256(loaded.normalized_data)


def test_utf8_and_versions_are_preserved_exactly() -> None:
    loaded = load_document_model(FIXTURES / "valid-complete.yaml")
    model = cast(dict[str, object], loaded.normalized_data["model"])

    assert model["title"] == "Café observation model"
    assert model["version"] == "2.1.0-beta.1"
    assert loaded.normalized_data["schema_version"] == 1
    assert "Café" in loaded.canonical_json
    assert "città" in loaded.canonical_json


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("model: [unterminated", "MODEL_YAML_INVALID"),
        ("", "MODEL_TOP_LEVEL_INVALID"),
        ("- not\n- a\n- mapping\n", "MODEL_TOP_LEVEL_INVALID"),
    ],
)
def test_rejects_invalid_yaml_documents(
    tmp_path: Path, content: str, code: str
) -> None:
    path = tmp_path / "model.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(DocumentModelError) as caught:
        load_document_model(path)

    assert caught.value.code == code
    assert caught.value.data_path == ("" if code == "MODEL_TOP_LEVEL_INVALID" else None)


def test_dangerous_yaml_tag_is_rejected_without_side_effect(tmp_path: Path) -> None:
    sentinel = tmp_path / "must-not-exist"
    payload = (
        "!!python/object/apply:pathlib.Path.write_text\n"
        f"- {sentinel}\n"
        "- unsafe side effect\n"
    )
    path = tmp_path / "unsafe.yaml"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(DocumentModelError) as caught:
        load_document_model(path)

    assert caught.value.code == "MODEL_YAML_UNSAFE"
    assert not sentinel.exists()


def test_missing_model_file_has_stable_error(tmp_path: Path) -> None:
    with pytest.raises(DocumentModelError) as caught:
        load_document_model(tmp_path / "missing.yaml")

    assert caught.value.code == "MODEL_FILE_NOT_FOUND"
    assert str(tmp_path) not in caught.value.message


def test_directory_is_not_read_as_model_file(tmp_path: Path) -> None:
    with pytest.raises(DocumentModelError) as caught:
        load_document_model(tmp_path)

    assert caught.value.code == "MODEL_FILE_UNREADABLE"


def test_schema_failure_is_structured_and_deterministic(tmp_path: Path) -> None:
    data = fixture_data()
    data["unexpected"] = True
    del data["model"]
    path = write_yaml(tmp_path / "invalid.yaml", data)

    with pytest.raises(DocumentModelError) as first:
        load_document_model(path)
    with pytest.raises(DocumentModelError) as second:
        load_document_model(path)

    assert first.value.code == "MODEL_SCHEMA_INVALID"
    assert first.value.data_path == second.value.data_path == ""
    assert first.value.details == second.value.details
    assert first.value.details is not None
    assert cast(int, first.value.details["error_count"]) >= 2


def test_schema_rejects_unknown_closed_object_property(tmp_path: Path) -> None:
    data = fixture_data()
    cast(dict[str, object], data["records"])["unexpected"] = True

    with pytest.raises(DocumentModelError) as caught:
        load_document_model(write_yaml(tmp_path / "invalid.yaml", data))

    assert caught.value.code == "MODEL_SCHEMA_INVALID"
    assert caught.value.data_path == "/records"


def test_natural_key_must_reference_field() -> None:
    data = fixture_data()
    cast(dict[str, object], data["records"])["natural_key"] = ["missing"]

    with pytest.raises(DocumentModelError) as caught:
        validate_document_model(data)

    assert caught.value.code == "MODEL_SEMANTIC_INVALID"
    assert caught.value.data_path == "/records/natural_key/0"


@pytest.mark.parametrize(
    ("location", "expected_path"),
    [
        ("filter", "/queries/0/filters/0/field"),
        ("return", "/queries/0/returns/0"),
        ("index", "/indexes/0/fields/0"),
    ],
)
def test_record_field_references_are_validated(
    location: str, expected_path: str
) -> None:
    data = fixture_data()
    query = cast(dict[str, Any], data["queries"][0])
    if location == "filter":
        query["filters"][0]["field"] = "missing"
    elif location == "return":
        query["returns"][0] = "missing"
    else:
        data["indexes"] = [{"fields": ["missing"]}]

    with pytest.raises(DocumentModelError) as caught:
        validate_document_model(data)

    assert caught.value.code == "MODEL_SEMANTIC_INVALID"
    assert caught.value.data_path == expected_path


def test_duplicate_recognition_rule_id_points_to_later_id() -> None:
    data = fixture_data()
    recognition = cast(dict[str, Any], data["recognition"])
    recognition["rules"].append(deepcopy(recognition["rules"][0]))

    with pytest.raises(DocumentModelError) as caught:
        validate_document_model(data)

    assert caught.value.data_path == "/recognition/rules/1/id"


def test_duplicate_query_name_points_to_later_name() -> None:
    data = fixture_data()
    cast(list[object], data["queries"]).append(deepcopy(data["queries"][0]))

    with pytest.raises(DocumentModelError) as caught:
        validate_document_model(data)

    assert caught.value.data_path == "/queries/1/name"


def test_unknown_parameter_reference_is_rejected() -> None:
    data = fixture_data()
    query = cast(dict[str, Any], data["queries"][0])
    query["filters"][0]["value"]["parameter"] = "missing"

    with pytest.raises(DocumentModelError) as caught:
        validate_document_model(data)

    assert caught.value.data_path == "/queries/0/filters/0/value/parameter"


def test_documented_context_reference_is_accepted() -> None:
    data = fixture_data()
    query = cast(dict[str, Any], data["queries"][0])
    query["context"] = ["current_date"]
    query["filters"][0]["value"] = {"context": "current_date"}

    validate_document_model(data)


def test_undeclared_context_reference_is_rejected() -> None:
    data = fixture_data()
    query = cast(dict[str, Any], data["queries"][0])
    query["filters"][0]["value"] = {"context": "current_date"}

    with pytest.raises(DocumentModelError) as caught:
        validate_document_model(data)

    assert caught.value.data_path == "/queries/0/filters/0/value/context"


def test_handler_and_custom_normalizer_are_only_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_import(*args: object, **kwargs: object) -> object:
        raise AssertionError("model loading must never import identifiers")

    monkeypatch.setattr(importlib, "import_module", forbidden_import)
    loaded = load_document_model(FIXTURES / "valid-complete.yaml")

    extraction = cast(dict[str, object], loaded.normalized_data["extraction"])
    records = cast(dict[str, Any], loaded.normalized_data["records"])
    assert extraction["handler"] == "sample.observations.extract"
    assert records["fields"]["code"]["normalize"][0]["handler"] == (
        "sample.normalize.code"
    )


def test_mapping_order_does_not_affect_canonical_json_or_hash() -> None:
    first: dict[str, object] = {"z": [3, 2, 1], "a": {"é": True, "b": None}}
    second: dict[str, object] = {"a": {"b": None, "é": True}, "z": [3, 2, 1]}

    assert normalize_document_model(first) == normalize_document_model(second)
    assert canonicalize_document_model(first) == canonicalize_document_model(second)
    assert compute_definition_sha256(first) == compute_definition_sha256(second)


def test_meaningful_change_changes_hash() -> None:
    first = fixture_data()
    second = deepcopy(first)
    cast(dict[str, object], second["model"])["title"] = "Changed title"

    assert compute_definition_sha256(first) != compute_definition_sha256(second)


def test_canonical_json_has_compact_utf8_form_and_no_newline() -> None:
    canonical = canonicalize_document_model({"z": 1, "label": "città café"})

    assert canonical == '{"label":"città café","z":1}'
    assert not canonical.endswith("\n")
    assert json.loads(canonical) == {"label": "città café", "z": 1}


def test_loading_is_independent_of_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    loaded = load_document_model(FIXTURES / "valid-minimal.yaml")

    assert loaded.normalized_data["schema_version"] == 1


def test_errors_are_typed_and_have_stable_string_form() -> None:
    error = DocumentModelError("MODEL_SEMANTIC_INVALID", "Unknown field.", "/x/0")

    assert isinstance(error, ReferenceEngineError)
    assert str(error) == "MODEL_SEMANTIC_INVALID at /x/0: Unknown field."


def test_loader_performs_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_system(command: str) -> int:
        raise AssertionError(f"unexpected command execution: {command}")

    monkeypatch.setattr(os, "system", forbidden_system)

    load_document_model(FIXTURES / "valid-minimal.yaml")
