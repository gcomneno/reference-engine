"""Immutable recognition-v1 domain values."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from decimal import Decimal
from enum import StrEnum

from reference_engine.recognition.canonical import validate_sha256
from reference_engine.recognition.decimals import ExactScore

type JSONScalar = str | int | bool | None


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SafeConfigurationEntry:
    name: str
    value: JSONScalar


@dataclass(frozen=True)
class CapabilitySnapshot:
    identifier: str
    version: str
    availability: Availability
    configuration: tuple[SafeConfigurationEntry, ...] = ()


@dataclass(frozen=True)
class InputValue:
    """Availability is separate so an available JSON null remains meaningful."""

    availability: Availability
    value: JSONScalar = dataclass_field(default=None, repr=False)


@dataclass(frozen=True)
class RecognitionTextProbe:
    text: str = dataclass_field(repr=False)
    limit: int
    truncated: bool
    producer_identifier: str
    producer_version: str


class ProbeAcquisitionStatus(StrEnum):
    AVAILABLE_NO_INPUT = "available_no_input"
    ATTEMPT_FAILED = "attempt_failed"
    AVAILABLE_WITH_PROBE = "available_with_probe"


@dataclass(frozen=True)
class RecognitionProbeAcquisition:
    status: ProbeAcquisitionStatus
    probe: RecognitionTextProbe | None = dataclass_field(default=None, repr=False)


@dataclass(frozen=True)
class TechnicalDocumentInputs:
    mime_type: InputValue
    original_filename: InputValue
    byte_size: InputValue
    source_url: InputValue
    retrieved_at: InputValue
    published_date: InputValue
    page_count: InputValue
    registered_at: InputValue
    sha256: InputValue
    recognition_text_probe: RecognitionProbeAcquisition


@dataclass(frozen=True)
class SafeString:
    sha256: str
    length: int

    def __post_init__(self) -> None:
        validate_sha256(self.sha256, "SafeString.sha256")


@dataclass(frozen=True)
class SafeTextProbeSnapshot:
    sha256: str
    character_count: int
    limit: int
    truncated: bool

    def __post_init__(self) -> None:
        validate_sha256(self.sha256, "SafeTextProbeSnapshot.sha256")


@dataclass(frozen=True)
class SafeDocumentInputSnapshot:
    fields: tuple[tuple[str, JSONScalar | SafeString | SafeTextProbeSnapshot], ...]


@dataclass(frozen=True)
class RecognitionRunSnapshot:
    snapshot_schema_version: str
    document_id: int
    source_artifact_id: int
    document_sha256: str
    engine_version: str
    capabilities: tuple[CapabilitySnapshot, ...]
    candidates: tuple[ActiveCandidateSnapshot, ...]
    safe_document_inputs: SafeDocumentInputSnapshot

    def __post_init__(self) -> None:
        validate_sha256(self.document_sha256, "RecognitionRunSnapshot.document_sha256")


@dataclass(frozen=True)
class ActiveCandidateSnapshot:
    model_version_id: int
    model_key: str
    semantic_version: str
    schema_version: int
    status: str
    definition_sha256: str
    definition_json: str = dataclass_field(repr=False)

    def __post_init__(self) -> None:
        validate_sha256(
            self.definition_sha256, "ActiveCandidateSnapshot.definition_sha256"
        )


class RuleType(StrEnum):
    MIME_TYPE = "mime_type"
    FILENAME_REGEX = "filename_regex"
    TEXT_CONTAINS = "text_contains"
    TEXT_REGEX = "text_regex"
    PAGE_COUNT_BETWEEN = "page_count_between"
    SHA256 = "sha256"
    METADATA_EQUALS = "metadata_equals"


@dataclass(frozen=True)
class PageCountRange:
    minimum: int
    maximum: int


@dataclass(frozen=True)
class MetadataExpectation:
    field: str
    expected: JSONScalar = dataclass_field(repr=False)


type RuleValue = str | PageCountRange | MetadataExpectation | None


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    type: RuleType | None
    value: RuleValue = dataclass_field(repr=False)
    required: bool
    weight: Decimal
    invalid_code: str | None = None


@dataclass(frozen=True)
class RecognitionDefinition:
    threshold: Decimal | None
    rules: tuple[RuleDefinition, ...]
    invalid_code: str | None = None


class RuleEvaluationStatus(StrEnum):
    EVALUATED_PASS = "evaluated_pass"
    EVALUATED_FAIL = "evaluated_fail"
    UNAVAILABLE_CAPABILITY = "unavailable_capability"
    UNAVAILABLE_INPUT = "unavailable_input"
    INVALID_RULE_DEFINITION = "invalid_rule_definition"
    TECHNICAL_EVALUATION_ERROR = "technical_evaluation_error"


@dataclass(frozen=True)
class SafeEvidenceValue:
    kind: str
    value: JSONScalar | None = dataclass_field(default=None, repr=False)
    sha256: str | None = None
    length: int | None = None

    def __post_init__(self) -> None:
        if self.sha256 is not None:
            validate_sha256(self.sha256, "SafeEvidenceValue.sha256")


@dataclass(frozen=True)
class RuleEvidence:
    id: str
    type: str
    required: bool
    weight: Decimal
    status: RuleEvaluationStatus
    passed: bool | None
    expected: SafeEvidenceValue | None
    actual: SafeEvidenceValue | None
    code: str | None = None


class CandidateState(StrEnum):
    DEFINITIVELY_INELIGIBLE = "definitively_ineligible"
    INDETERMINATE = "indeterminate"
    EVALUATED = "evaluated"


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: ActiveCandidateSnapshot
    threshold: Decimal | None
    rules: tuple[RuleEvidence, ...]
    candidate_state: CandidateState | None
    eligible: bool | None
    required_rules_passed: bool | None
    exact_score: ExactScore | None
    display_score: str | None
    evidence_json: str = dataclass_field(repr=False)
    evidence_sha256: str
    rank_position: int | None = None

    def __post_init__(self) -> None:
        validate_sha256(self.evidence_sha256, "CandidateEvaluation.evidence_sha256")


class RunOutcome(StrEnum):
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"


@dataclass(frozen=True)
class RankingResult:
    evaluations: tuple[CandidateEvaluation, ...]
    winner: CandidateEvaluation | None
    outcome: RunOutcome


class RunFailureCode(StrEnum):
    """Closed, public reasons for a run which could not be completed."""

    SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
    ORCHESTRATION_FAILED = "ORCHESTRATION_FAILED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


@dataclass(frozen=True)
class RecognitionCompletion:
    """Immutable domain output accepted by the persistence repository.

    ``ranking`` is absent only when a run-level failure prevented completion of
    the candidate set.  Raw inputs are retained only in memory and allow the
    repository to prove deterministic evaluator results without rereading a
    document or any mutable catalogue state.
    """

    document_id: int
    engine_version: str
    started_at: str
    completed_at: str
    snapshot: RecognitionRunSnapshot | None
    inputs: TechnicalDocumentInputs | None = dataclass_field(default=None, repr=False)
    ranking: RankingResult | None = None
    failure_code: RunFailureCode | None = None
