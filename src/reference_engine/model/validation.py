"""Schema and semantic validation for document model YAML v1."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from reference_engine.errors import DocumentModelError
from reference_engine.metadata_scalars import is_canonical_metadata_scalar


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    try:
        resource = files("reference_engine.resources").joinpath(
            "document-model-v1.schema.json"
        )
        raw: object = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        message = "The bundled document model schema is unavailable."
        raise RuntimeError(message) from error
    if not isinstance(raw, dict):
        message = "The bundled document model schema is not an object."
        raise RuntimeError(message)
    try:
        Draft202012Validator.check_schema(raw)
    except SchemaError as error:
        message = "The bundled document model schema is invalid."
        raise RuntimeError(message) from error
    return Draft202012Validator(raw)


def _pointer(parts: Iterable[object]) -> str:
    encoded = (str(part).replace("~", "~0").replace("/", "~1") for part in parts)
    return "".join(f"/{part}" for part in encoded)


def _error_key(error: ValidationError) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    validator = error.validator if isinstance(error.validator, str) else ""
    return (
        tuple(str(part) for part in error.absolute_path),
        tuple(str(part) for part in error.absolute_schema_path),
        validator,
    )


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    if not all(isinstance(key, str) for key in value):
        return None
    return value


def _sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, list):
        return value
    return None


def _semantic_error(message: str, path: str, **details: object) -> DocumentModelError:
    return DocumentModelError("MODEL_SEMANTIC_INVALID", message, path, details or None)


def _validate_semantics(data: Mapping[str, object]) -> None:
    document_metadata = _mapping(data.get("document_metadata"))
    if document_metadata is not None:
        metadata_fields = _mapping(document_metadata.get("fields"))
        assert metadata_fields is not None
        for metadata_name, field_value in metadata_fields.items():
            assert isinstance(metadata_name, str)
            metadata_field = _mapping(field_value)
            assert metadata_field is not None
            if "default" in metadata_field and "constant" in metadata_field:
                raise _semantic_error(
                    "A metadata field cannot declare both default and constant.",
                    f"/document_metadata/fields/{_escape_pointer(metadata_name)}",
                )
            for value_name in ("default", "constant"):
                if value_name not in metadata_field:
                    continue
                value = metadata_field[value_name]
                nullable = metadata_field.get("nullable", False) is True
                field_type = metadata_field.get("type")
                valid = value is None and nullable
                if value is not None:
                    if field_type in {
                        "string",
                        "date",
                        "datetime",
                        "decimal",
                        "integer",
                        "boolean",
                    }:
                        valid = is_canonical_metadata_scalar(field_type, value)
                    elif field_type == "enum":
                        values = metadata_field.get("values")
                        valid = isinstance(values, Sequence) and any(
                            type(value) is type(member) and value == member
                            for member in values
                        )
                if not valid:
                    raise _semantic_error(
                        "A metadata default or constant must match its field type.",
                        f"/document_metadata/fields/{_escape_pointer(metadata_name)}/{value_name}",
                    )

    records = _mapping(data.get("records"))
    assert records is not None
    fields = _mapping(records.get("fields"))
    natural_key = _sequence(records.get("natural_key"))
    assert fields is not None and natural_key is not None
    field_names = set(fields)

    for index, name in enumerate(natural_key):
        assert isinstance(name, str)
        if name not in field_names:
            raise _semantic_error(
                f"Natural-key field {name!r} is not declared.",
                f"/records/natural_key/{index}",
                field=name,
            )

    recognition = _mapping(data.get("recognition"))
    assert recognition is not None
    rules = _sequence(recognition.get("rules"))
    assert rules is not None
    seen_rule_ids: set[str] = set()
    for index, rule_value in enumerate(rules):
        rule = _mapping(rule_value)
        assert rule is not None
        rule_id = rule.get("id")
        assert isinstance(rule_id, str)
        if rule_id in seen_rule_ids:
            raise _semantic_error(
                f"Recognition rule ID {rule_id!r} is duplicated.",
                f"/recognition/rules/{index}/id",
                rule_id=rule_id,
            )
        seen_rule_ids.add(rule_id)

    queries = _sequence(data.get("queries"))
    assert queries is not None
    seen_query_names: set[str] = set()
    for query_index, query_value in enumerate(queries):
        query = _mapping(query_value)
        assert query is not None
        query_name = query.get("name")
        assert isinstance(query_name, str)
        if query_name in seen_query_names:
            raise _semantic_error(
                f"Query name {query_name!r} is duplicated.",
                f"/queries/{query_index}/name",
                query_name=query_name,
            )
        seen_query_names.add(query_name)
        parameters = _mapping(query.get("parameters"))
        filters = _sequence(query.get("filters"))
        returns = _sequence(query.get("returns"))
        context = _sequence(query.get("context", []))
        assert parameters is not None and filters is not None and returns is not None
        assert context is not None
        parameter_names = set(parameters)
        context_names = {item for item in context if isinstance(item, str)}
        for filter_index, filter_value in enumerate(filters):
            query_filter = _mapping(filter_value)
            assert query_filter is not None
            field = query_filter.get("field")
            assert isinstance(field, str)
            if field not in field_names:
                raise _semantic_error(
                    f"Query filter field {field!r} is not declared.",
                    f"/queries/{query_index}/filters/{filter_index}/field",
                    field=field,
                )
            for operand_name in ("value", "minimum", "maximum"):
                _validate_operand(
                    query_filter.get(operand_name),
                    f"/queries/{query_index}/filters/{filter_index}/{operand_name}",
                    parameter_names,
                    context_names,
                )
            values = _sequence(query_filter.get("values"))
            if values is not None:
                for value_index, operand in enumerate(values):
                    _validate_operand(
                        operand,
                        f"/queries/{query_index}/filters/{filter_index}/values/{value_index}",
                        parameter_names,
                        context_names,
                    )
        for return_index, field in enumerate(returns):
            assert isinstance(field, str)
            if field not in field_names:
                raise _semantic_error(
                    f"Returned field {field!r} is not declared.",
                    f"/queries/{query_index}/returns/{return_index}",
                    field=field,
                )

    indexes = _sequence(data.get("indexes", []))
    assert indexes is not None
    for index, index_value in enumerate(indexes):
        index_definition = _mapping(index_value)
        assert index_definition is not None
        index_fields = _sequence(index_definition.get("fields"))
        assert index_fields is not None
        for field_index, field in enumerate(index_fields):
            assert isinstance(field, str)
            if field not in field_names:
                raise _semantic_error(
                    f"Index field {field!r} is not declared.",
                    f"/indexes/{index}/fields/{field_index}",
                    field=field,
                )


def _validate_operand(
    operand: object,
    path: str,
    parameter_names: set[str],
    context_names: set[str],
) -> None:
    value = _mapping(operand)
    if value is None:
        return
    parameter = value.get("parameter")
    if isinstance(parameter, str) and parameter not in parameter_names:
        raise _semantic_error(
            f"Query parameter {parameter!r} is not declared.",
            f"{path}/parameter",
            parameter=parameter,
        )
    context = value.get("context")
    if isinstance(context, str) and context not in context_names:
        raise _semantic_error(
            f"Query context {context!r} is not declared by the query.",
            f"{path}/context",
            context=context,
        )


def validate_document_model(data: Mapping[str, object]) -> None:
    """Validate a model against Draft 2020-12 and cross-reference semantics."""

    errors = sorted(_validator().iter_errors(data), key=_error_key)
    if errors:
        primary = errors[0]
        validator = (
            primary.validator if isinstance(primary.validator, str) else "unknown"
        )
        raise DocumentModelError(
            "MODEL_SCHEMA_INVALID",
            primary.message,
            _pointer(primary.absolute_path),
            {
                "validator": validator,
                "schema_path": _pointer(primary.absolute_schema_path),
                "error_count": len(errors),
            },
        )
    _validate_semantics(data)


def _escape_pointer(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")
