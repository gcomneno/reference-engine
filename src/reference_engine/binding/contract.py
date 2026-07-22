"""Pure, persistence-neutral document binding contract."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from reference_engine.errors import DocumentBindingError
from reference_engine.metadata_scalars import (
    MetadataScalarError,
    normalize_metadata_scalar,
)
from reference_engine.recognition.authorization import (
    SensitiveRuleInput,
    validate_recognition_authorization,
)
from reference_engine.recognition.canonical import canonical_json, validate_sha256
from reference_engine.recognition.types import RunOutcome

type MetadataScalar = str | int | bool | None

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_MISSING = object()


def _error(code: str) -> DocumentBindingError:
    messages = {
        "BINDING_REQUEST_INVALID": "The binding request is invalid.",
        "BINDING_POLICY_DENIED": "The requested binding method is not permitted.",
        "BINDING_RECOGNITION_INELIGIBLE": (
            "Recognition evidence does not authorize this binding."
        ),
        "BINDING_RECOGNITION_INCONSISTENT": "Recognition evidence is inconsistent.",
        "BINDING_METADATA_INVALID": "Document metadata is invalid.",
        "BINDING_METADATA_CONFLICT": (
            "Protected document metadata conflicts with an input layer."
        ),
        "BINDING_METADATA_REQUIRED": "Required document metadata is missing.",
        "BINDING_SUPERSESSION_INVALID": "The requested supersession is invalid.",
    }
    return DocumentBindingError(code, messages[code], details=None)


class SelectionMethod(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
    EXPLICIT_CLI = "explicit_cli"


@dataclass(frozen=True)
class BindingPolicy:
    """Closed policy surface; omitted permissions safely remain denied."""

    allow_automatic: bool = False
    allow_manual: bool = False
    allow_explicit_cli: bool = False

    def permits(self, method: SelectionMethod) -> bool:
        return {
            SelectionMethod.AUTOMATIC: self.allow_automatic,
            SelectionMethod.MANUAL: self.allow_manual,
            SelectionMethod.EXPLICIT_CLI: self.allow_explicit_cli,
        }[method]


@dataclass(frozen=True)
class ModelBindingPolicy(BindingPolicy):
    """Permissions declared by one immutable model definition."""


@dataclass(frozen=True)
class SystemBindingPolicy(BindingPolicy):
    """Permissions explicitly supplied by the calling system."""


def parse_model_binding_policy(model: Mapping[str, object]) -> ModelBindingPolicy:
    value = model.get("binding")
    if value is None:
        return ModelBindingPolicy()
    if not isinstance(value, Mapping) or set(value) != {
        "allow_automatic",
        "allow_manual",
        "allow_explicit_cli",
    }:
        raise _error("BINDING_REQUEST_INVALID")
    permissions = tuple(value[name] for name in sorted(value))
    if any(type(item) is not bool for item in permissions):
        raise _error("BINDING_REQUEST_INVALID")
    return ModelBindingPolicy(
        allow_automatic=value["allow_automatic"],
        allow_manual=value["allow_manual"],
        allow_explicit_cli=value["allow_explicit_cli"],
    )


@dataclass(frozen=True)
class SelectedModel:
    version_id: int
    model_key: str
    semantic_version: str
    definition_sha256: str


@dataclass(frozen=True)
class CandidateRecognitionEvidence:
    model: SelectedModel
    model_definition_json: str = field(repr=False)
    score: float
    required_rules_passed: bool | None
    eligible: bool | None
    rank_position: int | None
    evidence_json: str = field(repr=False)
    evidence_sha256: str
    rule_input_values: tuple[SensitiveRuleInput, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class RecognitionBindingEvidence:
    run_id: int
    document_id: int
    document_sha256: str
    engine_version: str
    started_at: str
    completed_at: str
    outcome: RunOutcome
    snapshot_json: str = field(repr=False)
    snapshot_sha256: str
    candidates: tuple[CandidateRecognitionEvidence, ...]
    winner_model_version_id: int | None = None


class MetadataType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"


@dataclass(frozen=True)
class MetadataField:
    name: str
    type: MetadataType
    required: bool
    nullable: bool = False
    default: object = field(default=_MISSING, repr=False)
    constant: object = field(default=_MISSING, repr=False)
    values: tuple[MetadataScalar, ...] = ()


@dataclass(frozen=True)
class MetadataContract:
    fields: tuple[MetadataField, ...] = ()


def parse_metadata_contract(model: Mapping[str, object]) -> MetadataContract:
    """Project the closed model metadata declaration into typed policy values."""

    section = model.get("document_metadata")
    if section is None:
        return MetadataContract()
    if not isinstance(section, Mapping) or set(section) != {"fields"}:
        raise _error("BINDING_METADATA_INVALID")
    raw_fields = section["fields"]
    if not isinstance(raw_fields, Mapping):
        raise _error("BINDING_METADATA_INVALID")
    projected: list[MetadataField] = []
    for name, raw in raw_fields.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            raise _error("BINDING_METADATA_INVALID")
        try:
            field_type = MetadataType(raw["type"])
            required = raw["required"]
            nullable = raw.get("nullable", False)
            values_value = raw.get("values", ())
            if type(required) is not bool or type(nullable) is not bool:
                raise ValueError
            if not isinstance(values_value, (list, tuple)):
                raise ValueError
            projected.append(
                MetadataField(
                    name,
                    field_type,
                    required,
                    nullable,
                    raw.get("default", _MISSING),
                    raw.get("constant", _MISSING),
                    tuple(values_value),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise _error("BINDING_METADATA_INVALID") from None
    return MetadataContract(tuple(projected))


@dataclass(frozen=True)
class SupersededBinding:
    binding_id: int
    document_id: int
    model: SelectedModel
    metadata_sha256: str


@dataclass(frozen=True)
class BindingRequest:
    document_id: int
    document_sha256: str
    selected_model: SelectedModel
    selection_method: SelectionMethod
    model_policy: ModelBindingPolicy
    system_policy: SystemBindingPolicy
    recognition: RecognitionBindingEvidence
    metadata_contract: MetadataContract = MetadataContract()
    extracted_metadata: Mapping[str, object] = field(default_factory=dict, repr=False)
    user_metadata: Mapping[str, object] = field(default_factory=dict, repr=False)
    supersedes: SupersededBinding | None = None
    existing_binding_id: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.extracted_metadata, Mapping):
            object.__setattr__(
                self,
                "extracted_metadata",
                MappingProxyType(dict(self.extracted_metadata)),
            )
        if isinstance(self.user_metadata, Mapping):
            object.__setattr__(
                self, "user_metadata", MappingProxyType(dict(self.user_metadata))
            )


@dataclass(frozen=True)
class BindingOutcome:
    document_id: int
    document_sha256: str
    selected_model: SelectedModel
    selection_method: SelectionMethod
    recognition_run_id: int
    document_metadata: tuple[tuple[str, MetadataScalar], ...]
    document_metadata_json: str = field(repr=False)
    metadata_sha256: str
    supersedes_binding_id: int | None


def _valid_model(model: SelectedModel) -> bool:
    try:
        validate_sha256(model.definition_sha256)
    except ValueError:
        return False
    return (
        type(model.version_id) is int
        and model.version_id > 0
        and isinstance(model.model_key, str)
        and bool(model.model_key)
        and isinstance(model.semantic_version, str)
        and _SEMVER.fullmatch(model.semantic_version) is not None
    )


def _authorize(request: BindingRequest) -> None:
    recognition = request.recognition
    policy_values = (
        request.model_policy.allow_automatic,
        request.model_policy.allow_manual,
        request.model_policy.allow_explicit_cli,
        request.system_policy.allow_automatic,
        request.system_policy.allow_manual,
        request.system_policy.allow_explicit_cli,
    )
    if (
        not isinstance(request.model_policy, ModelBindingPolicy)
        or not isinstance(request.system_policy, SystemBindingPolicy)
        or any(type(value) is not bool for value in policy_values)
    ):
        raise _error("BINDING_REQUEST_INVALID")
    if not request.model_policy.permits(
        request.selection_method
    ) or not request.system_policy.permits(request.selection_method):
        raise _error("BINDING_POLICY_DENIED")
    if (
        type(request.document_id) is not int
        or request.document_id <= 0
        or not _valid_model(request.selected_model)
        or type(recognition.run_id) is not int
        or recognition.run_id <= 0
        or recognition.document_id != request.document_id
        or recognition.document_sha256 != request.document_sha256
        or not isinstance(recognition.outcome, RunOutcome)
    ):
        raise _error("BINDING_RECOGNITION_INCONSISTENT")
    try:
        validate_sha256(request.document_sha256)
        validate_sha256(recognition.document_sha256)
    except ValueError:
        raise _error("BINDING_RECOGNITION_INCONSISTENT") from None
    try:
        validated = validate_recognition_authorization(recognition)
    except (ValueError, TypeError, AttributeError):
        raise _error("BINDING_RECOGNITION_INCONSISTENT") from None
    matches = [
        item
        for item in recognition.candidates
        if item.model.version_id == request.selected_model.version_id
    ]
    if len(matches) != 1 or matches[0].model != request.selected_model:
        raise _error("BINDING_RECOGNITION_INCONSISTENT")
    candidate = matches[0]
    if not any(item.source is candidate for item in validated):
        raise _error("BINDING_RECOGNITION_INCONSISTENT")
    if candidate.required_rules_passed is not True:
        raise _error("BINDING_RECOGNITION_INELIGIBLE")
    if request.selection_method is SelectionMethod.AUTOMATIC and (
        recognition.outcome is not RunOutcome.MATCHED
        or candidate.eligible is not True
        or candidate.rank_position != 1
        or recognition.winner_model_version_id != request.selected_model.version_id
    ):
        raise _error("BINDING_RECOGNITION_INELIGIBLE")


def _normalize(field: MetadataField, value: object) -> MetadataScalar:
    if value is None:
        if not field.nullable:
            raise _error("BINDING_METADATA_INVALID")
        result: MetadataScalar = None
    elif field.type is MetadataType.STRING:
        try:
            result = cast(
                MetadataScalar, normalize_metadata_scalar(field.type.value, value)
            )
        except MetadataScalarError:
            raise _error("BINDING_METADATA_INVALID") from None
    elif field.type in {
        MetadataType.INTEGER,
        MetadataType.BOOLEAN,
        MetadataType.DECIMAL,
        MetadataType.DATE,
        MetadataType.DATETIME,
    }:
        try:
            result = cast(
                MetadataScalar, normalize_metadata_scalar(field.type.value, value)
            )
        except MetadataScalarError:
            raise _error("BINDING_METADATA_INVALID") from None
    elif field.type is MetadataType.ENUM:
        if value is not None and not isinstance(value, (str, int, bool)):
            raise _error("BINDING_METADATA_INVALID")
        result = value
    else:
        raise _error("BINDING_METADATA_INVALID")
    if field.type is MetadataType.ENUM and not any(
        type(result) is type(item) and result == item for item in field.values
    ):
        raise _error("BINDING_METADATA_INVALID")
    return result


def _metadata(
    request: BindingRequest,
) -> tuple[tuple[tuple[str, MetadataScalar], ...], str, str]:
    declarations: dict[str, MetadataField] = {}
    for item in request.metadata_contract.fields:
        if (
            not isinstance(item, MetadataField)
            or not isinstance(item.type, MetadataType)
            or type(item.required) is not bool
            or type(item.nullable) is not bool
            or not _IDENTIFIER.fullmatch(item.name)
            or item.name in declarations
            or (
                item.type is MetadataType.ENUM
                and (
                    not item.values
                    or any(
                        value is not None and type(value) not in {str, int, bool}
                        for value in item.values
                    )
                    or any(
                        type(left) is type(right) and left == right
                        for index, left in enumerate(item.values)
                        for right in item.values[index + 1 :]
                    )
                )
            )
            or (item.type is not MetadataType.ENUM and bool(item.values))
        ):
            raise _error("BINDING_METADATA_INVALID")
        if item.default is not _MISSING and item.constant is not _MISSING:
            raise _error("BINDING_METADATA_INVALID")
        for declared_value in (item.default, item.constant):
            if declared_value is not _MISSING:
                _normalize(item, declared_value)
        declarations[item.name] = item
    for layer in (request.extracted_metadata, request.user_metadata):
        if not isinstance(layer, Mapping) or any(
            not isinstance(key, str) or key not in declarations for key in layer
        ):
            raise _error("BINDING_METADATA_INVALID")
    merged: dict[str, MetadataScalar] = {}
    for name, declaration in declarations.items():
        supplied = [
            layer[name]
            for layer in (request.extracted_metadata, request.user_metadata)
            if name in layer
        ]
        if declaration.constant is not _MISSING:
            if supplied and any(
                value != declaration.constant
                or type(value) is not type(declaration.constant)
                for value in supplied
            ):
                raise _error("BINDING_METADATA_CONFLICT")
            raw = declaration.constant
        elif name in request.user_metadata:
            raw = request.user_metadata[name]
        elif name in request.extracted_metadata:
            raw = request.extracted_metadata[name]
        else:
            raw = declaration.default
        if raw is _MISSING:
            if declaration.required:
                raise _error("BINDING_METADATA_REQUIRED")
            continue
        merged[name] = _normalize(declaration, raw)
    serialized = canonical_json(merged)
    return (
        tuple(sorted(merged.items())),
        serialized,
        hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    )


def _supersession(request: BindingRequest) -> int | None:
    prior = request.supersedes
    if request.existing_binding_id is not None and (
        type(request.existing_binding_id) is not int or request.existing_binding_id <= 0
    ):
        raise _error("BINDING_SUPERSESSION_INVALID")
    if prior is None:
        return None
    try:
        valid_hash = validate_sha256(prior.metadata_sha256) == prior.metadata_sha256
    except ValueError:
        valid_hash = False
    if (
        type(prior.binding_id) is not int
        or prior.binding_id <= 0
        or prior.binding_id == request.existing_binding_id
        or type(prior.document_id) is not int
        or prior.document_id <= 0
        or prior.document_id != request.document_id
        or not _valid_model(prior.model)
        or not valid_hash
    ):
        raise _error("BINDING_SUPERSESSION_INVALID")
    return prior.binding_id


def bind_document(request: BindingRequest) -> BindingOutcome:
    """Validate and deterministically project one immutable binding decision."""

    try:
        if not isinstance(request, BindingRequest) or not isinstance(
            request.selection_method, SelectionMethod
        ):
            raise _error("BINDING_REQUEST_INVALID")
        _authorize(request)
        metadata, metadata_json, metadata_sha256 = _metadata(request)
        supersedes = _supersession(request)
    except DocumentBindingError:
        raise
    except Exception:
        raise _error("BINDING_REQUEST_INVALID") from None
    return BindingOutcome(
        request.document_id,
        request.document_sha256,
        request.selected_model,
        request.selection_method,
        request.recognition.run_id,
        metadata,
        metadata_json,
        metadata_sha256,
        supersedes,
    )
