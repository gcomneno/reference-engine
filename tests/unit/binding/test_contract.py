from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from reference_engine.binding import (
    BindingRequest,
    CandidateRecognitionEvidence,
    MetadataContract,
    MetadataField,
    MetadataType,
    ModelBindingPolicy,
    RecognitionBindingEvidence,
    SelectedModel,
    SelectionMethod,
    SensitiveRuleInput,
    SupersededBinding,
    SystemBindingPolicy,
    bind_document,
    parse_model_binding_policy,
)
from reference_engine.errors import DocumentBindingError
from reference_engine.recognition.canonical import canonical_json
from reference_engine.recognition.outcomes import (
    evaluate_candidate,
    rank_and_select,
    serialize_run_snapshot,
)
from reference_engine.recognition.rules import parse_recognition_definition
from reference_engine.recognition.snapshots import project_safe_document_inputs
from reference_engine.recognition.types import (
    ActiveCandidateSnapshot,
    Availability,
    CapabilitySnapshot,
    InputValue,
    ProbeAcquisitionStatus,
    RecognitionProbeAcquisition,
    RecognitionRunSnapshot,
    RecognitionTextProbe,
    RunOutcome,
    SafeDocumentInputSnapshot,
    TechnicalDocumentInputs,
)

SNAPSHOT_HASH = "b" * 64
DEFINITION_JSON = canonical_json(
    {
        "recognition": {
            "ambiguity_policy": "reject",
            "minimum_score": 1,
            "rules": [
                {
                    "id": "mime",
                    "required": True,
                    "type": "mime_type",
                    "value": "application/pdf",
                    "weight": 1,
                }
            ],
        }
    }
)
HASH = hashlib.sha256(DEFINITION_JSON.encode()).hexdigest()
MODEL = SelectedModel(7, "sample.model", "1.2.3", HASH)


def _serialized(value: object) -> tuple[str, str]:
    text = canonical_json(value)
    return text, hashlib.sha256(text.encode()).hexdigest()


def _request(
    method: SelectionMethod = SelectionMethod.AUTOMATIC,
    outcome: RunOutcome = RunOutcome.MATCHED,
    *,
    required: bool | None = True,
    eligible: bool | None = True,
    rank: int | None = 1,
) -> BindingRequest:
    definition = json.loads(DEFINITION_JSON)
    definition["recognition"]["rules"][0]["required"] = not (
        required is True and outcome is RunOutcome.NOT_MATCHED
    )
    definition_json = canonical_json(definition)
    definition_hash = hashlib.sha256(definition_json.encode()).hexdigest()
    selected = replace(MODEL, definition_sha256=definition_hash)
    active = ActiveCandidateSnapshot(
        selected.version_id,
        selected.model_key,
        selected.semantic_version,
        1,
        "active",
        definition_hash,
        definition_json,
    )
    capability = CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE)
    snapshot = RecognitionRunSnapshot(
        "recognition-run-snapshot.v1",
        3,
        5,
        SNAPSHOT_HASH,
        "test-engine/1",
        (capability,),
        (active,),
        SafeDocumentInputSnapshot(
            (
                (
                    "mime_type",
                    "text/plain"
                    if eligible is False
                    or required is False
                    or outcome is RunOutcome.NOT_MATCHED
                    else "application/pdf",
                ),
            )
        ),
    )
    snapshot_json, snapshot_hash = serialize_run_snapshot(snapshot)
    unavailable = InputValue(Availability.UNAVAILABLE)
    inputs = TechnicalDocumentInputs(
        InputValue(
            Availability.AVAILABLE,
            "text/plain"
            if eligible is False
            or required is False
            or outcome is RunOutcome.NOT_MATCHED
            else "application/pdf",
        ),
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        RecognitionProbeAcquisition(ProbeAcquisitionStatus.AVAILABLE_NO_INPUT),
    )
    evaluated = evaluate_candidate(
        active,
        parse_recognition_definition(definition_json),
        inputs,
        (capability,),
        snapshot_hash,
    )
    ranking = rank_and_select((evaluated,))
    outcome = ranking.outcome
    evaluated = ranking.evaluations[0]
    candidate = CandidateRecognitionEvidence(
        selected,
        definition_json,
        float(evaluated.display_score or "0"),
        evaluated.required_rules_passed,
        evaluated.eligible,
        evaluated.rank_position if rank is not None else None,
        evaluated.evidence_json,
        evaluated.evidence_sha256,
    )
    recognition = RecognitionBindingEvidence(
        11,
        3,
        SNAPSHOT_HASH,
        "test-engine/1",
        "2026-01-01T00:00:00.000000Z",
        "2026-01-01T00:00:01.000000Z",
        outcome,
        snapshot_json,
        snapshot_hash,
        (candidate,),
        selected.version_id if outcome is RunOutcome.MATCHED else None,
    )
    policy = dict(allow_automatic=True, allow_manual=True, allow_explicit_cli=True)
    return BindingRequest(
        3,
        SNAPSHOT_HASH,
        selected,
        method,
        ModelBindingPolicy(**policy),
        SystemBindingPolicy(**policy),
        recognition,
    )


def _request_for_rules(
    rules: list[dict[str, object]],
    inputs: TechnicalDocumentInputs,
    *,
    method: SelectionMethod = SelectionMethod.AUTOMATIC,
    supplied: tuple[SensitiveRuleInput, ...] = (),
) -> BindingRequest:
    definition_json = canonical_json(
        {
            "recognition": {
                "ambiguity_policy": "reject",
                "minimum_score": 1,
                "rules": rules,
            }
        }
    )
    definition_hash = hashlib.sha256(definition_json.encode()).hexdigest()
    selected = replace(MODEL, definition_sha256=definition_hash)
    active = ActiveCandidateSnapshot(
        selected.version_id,
        selected.model_key,
        selected.semantic_version,
        1,
        "active",
        definition_hash,
        definition_json,
    )
    capabilities = (
        CapabilitySnapshot("document_metadata.v1", "1", Availability.AVAILABLE),
        CapabilitySnapshot("recognition_text_probe.v1", "1", Availability.AVAILABLE),
    )
    snapshot = RecognitionRunSnapshot(
        "recognition-run-snapshot.v1",
        3,
        5,
        SNAPSHOT_HASH,
        "test-engine/1",
        capabilities,
        (active,),
        project_safe_document_inputs(inputs),
    )
    snapshot_json, snapshot_hash = serialize_run_snapshot(snapshot)
    evaluated = evaluate_candidate(
        active,
        parse_recognition_definition(definition_json),
        inputs,
        capabilities,
        snapshot_hash,
    )
    ranking = rank_and_select((evaluated,))
    evaluated = ranking.evaluations[0]
    candidate = CandidateRecognitionEvidence(
        selected,
        definition_json,
        float(evaluated.display_score or "0"),
        evaluated.required_rules_passed,
        evaluated.eligible,
        evaluated.rank_position,
        evaluated.evidence_json,
        evaluated.evidence_sha256,
        supplied,
    )
    recognition = RecognitionBindingEvidence(
        11,
        3,
        SNAPSHOT_HASH,
        "test-engine/1",
        "2026-01-01T00:00:00.000000Z",
        "2026-01-01T00:00:01.000000Z",
        ranking.outcome,
        snapshot_json,
        snapshot_hash,
        (candidate,),
        selected.version_id if ranking.outcome is RunOutcome.MATCHED else None,
    )
    policy = dict(allow_automatic=True, allow_manual=True, allow_explicit_cli=True)
    return BindingRequest(
        3,
        SNAPSHOT_HASH,
        selected,
        method,
        ModelBindingPolicy(**policy),
        SystemBindingPolicy(**policy),
        recognition,
    )


def _technical_inputs(
    *, filename: str = "private-report.pdf", text: str = "Secret marker heading"
) -> TechnicalDocumentInputs:
    available = Availability.AVAILABLE
    return TechnicalDocumentInputs(
        InputValue(available, "application/pdf"),
        InputValue(available, filename),
        InputValue(available, 123),
        InputValue(available, None),
        InputValue(available, "2026-01-01T00:00:00.000Z"),
        InputValue(available, "2026-01-01"),
        InputValue(available, 3),
        InputValue(available, "2026-01-02T00:00:00.000Z"),
        InputValue(available, "a" * 64),
        RecognitionProbeAcquisition(
            ProbeAcquisitionStatus.AVAILABLE_WITH_PROBE,
            RecognitionTextProbe(text, 100, False, "synthetic", "1"),
        ),
    )


def _assert_code(request: BindingRequest, code: str) -> None:
    with pytest.raises(DocumentBindingError) as caught:
        bind_document(request)
    assert caught.value.code == code
    assert caught.value.details is None
    assert str(caught.value) == f"{code}: {caught.value.message}"


def _replace_evidence_rules(request: BindingRequest, rules: object) -> BindingRequest:
    candidate = request.recognition.candidates[0]
    evidence = json.loads(candidate.evidence_json)
    evidence["rules"] = rules
    evidence_json, evidence_hash = _serialized(evidence)
    candidate = replace(
        candidate, evidence_json=evidence_json, evidence_sha256=evidence_hash
    )
    return replace(
        request,
        recognition=replace(request.recognition, candidates=(candidate,)),
    )


def _replace_evidence_member(
    request: BindingRequest, name: str, value: object
) -> BindingRequest:
    candidate = request.recognition.candidates[0]
    evidence = json.loads(candidate.evidence_json)
    evidence[name] = value
    evidence_json, evidence_hash = _serialized(evidence)
    candidate = replace(
        candidate, evidence_json=evidence_json, evidence_sha256=evidence_hash
    )
    return replace(
        request,
        recognition=replace(request.recognition, candidates=(candidate,)),
    )


def _replace_rule_actual(request: BindingRequest, value: object) -> BindingRequest:
    candidate = request.recognition.candidates[0]
    evidence = json.loads(candidate.evidence_json)
    evidence["rules"][0]["actual"] = value
    evidence_json, evidence_hash = _serialized(evidence)
    candidate = replace(
        candidate, evidence_json=evidence_json, evidence_sha256=evidence_hash
    )
    return replace(
        request,
        recognition=replace(request.recognition, candidates=(candidate,)),
    )


def _fabricate_passing_mime_evidence(request: BindingRequest) -> BindingRequest:
    candidate = request.recognition.candidates[0]
    evidence = json.loads(candidate.evidence_json)
    rule = evidence["rules"][0]
    rule.update(
        status="evaluated_pass",
        passed=True,
        code=None,
        score_contribution="1.000000",
    )
    evidence.update(
        candidate_state="evaluated",
        required_rules_passed=True,
        eligible=True,
        exact_score={"numerator": "1", "denominator": "1"},
        display_score="1.000000",
    )
    evidence_json, evidence_sha256 = _serialized(evidence)
    candidate = replace(
        candidate,
        score=1.0,
        required_rules_passed=True,
        eligible=True,
        rank_position=1,
        evidence_json=evidence_json,
        evidence_sha256=evidence_sha256,
    )
    recognition = replace(
        request.recognition,
        outcome=RunOutcome.MATCHED,
        candidates=(candidate,),
        winner_model_version_id=candidate.model.version_id,
    )
    return replace(request, recognition=recognition)


@pytest.mark.parametrize("method", tuple(SelectionMethod))
def test_mime_only_binding_uses_default_empty_rule_inputs(
    method: SelectionMethod,
) -> None:
    request = _request(method)
    assert request.recognition.candidates[0].rule_input_values == ()
    result = bind_document(request)
    assert result.selection_method is method
    assert result.recognition_run_id == 11


@pytest.mark.parametrize(
    "field",
    ["eligible", "rank_position", "winner_model_version_id"],
)
def test_automatic_requires_complete_eligible_winner_evidence(field: str) -> None:
    request = _request()
    if field == "winner_model_version_id":
        changed = replace(
            request,
            recognition=replace(request.recognition, winner_model_version_id=None),
        )
    else:
        candidate = request.recognition.candidates[0]
        candidate = (
            replace(candidate, eligible=None)
            if field == "eligible"
            else replace(candidate, rank_position=None)
        )
        evidence = json.loads(candidate.evidence_json)
        if field == "eligible":
            evidence["eligible"] = None
            evidence_json, evidence_hash = _serialized(evidence)
            candidate = replace(
                candidate,
                evidence_json=evidence_json,
                evidence_sha256=evidence_hash,
            )
        changed = replace(
            request,
            recognition=replace(request.recognition, candidates=(candidate,)),
        )
    _assert_code(changed, "BINDING_RECOGNITION_INCONSISTENT")


@pytest.mark.parametrize("outcome", tuple(RunOutcome))
def test_every_non_matched_outcome_cannot_auto_bind(outcome: RunOutcome) -> None:
    if outcome is RunOutcome.MATCHED:
        assert bind_document(_request()).recognition_run_id == 11
    elif outcome is RunOutcome.NOT_MATCHED:
        _assert_code(_request(outcome=outcome), "BINDING_RECOGNITION_INELIGIBLE")
    else:
        request = _request()
        _assert_code(
            replace(request, recognition=replace(request.recognition, outcome=outcome)),
            "BINDING_RECOGNITION_INCONSISTENT",
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda r: replace(r, recognition=replace(r.recognition, snapshot_json="{}")),
        lambda r: replace(r, recognition=replace(r.recognition, document_id=4)),
        lambda r: replace(r, recognition=replace(r.recognition, candidates=())),
        lambda r: _replace_evidence_rules(r, []),
        lambda r: replace(
            r,
            recognition=replace(
                r.recognition,
                candidates=(
                    replace(r.recognition.candidates[0], evidence_sha256="c" * 64),
                ),
            ),
        ),
    ],
)
def test_missing_stale_or_inconsistent_evidence_is_rejected(change: object) -> None:
    _assert_code(change(_request()), "BINDING_RECOGNITION_INCONSISTENT")  # type: ignore[operator]


def test_manual_rejects_fabricated_passing_failed_run() -> None:
    request = _request(SelectionMethod.MANUAL)
    _assert_code(
        replace(
            request,
            recognition=replace(request.recognition, outcome=RunOutcome.FAILED),
        ),
        "BINDING_RECOGNITION_INCONSISTENT",
    )


@pytest.mark.parametrize("method", tuple(SelectionMethod))
def test_every_method_rejects_recomputed_fabricated_rule_result(
    method: SelectionMethod,
) -> None:
    request = _request(method, RunOutcome.NOT_MATCHED, required=False, eligible=False)
    assert json.loads(request.recognition.snapshot_json)["safe_document_inputs"] == {
        "mime_type": "text/plain"
    }
    _assert_code(
        _fabricate_passing_mime_evidence(request),
        "BINDING_RECOGNITION_INCONSISTENT",
    )


@pytest.mark.parametrize(
    ("rule", "rule_id", "preimage"),
    [
        (
            {
                "id": "filename",
                "required": True,
                "type": "filename_regex",
                "value": r"report\.pdf$",
                "weight": 1,
            },
            "filename",
            "private-report.pdf",
        ),
        (
            {
                "id": "contains",
                "required": True,
                "type": "text_contains",
                "value": "marker",
                "weight": 1,
            },
            "contains",
            "Secret marker heading",
        ),
        (
            {
                "id": "regex",
                "required": True,
                "type": "text_regex",
                "value": "(?i)secret",
                "weight": 1,
            },
            "regex",
            "Secret marker heading",
        ),
    ],
)
def test_sensitive_rules_require_keyed_snapshot_matching_preimages(
    rule: dict[str, object], rule_id: str, preimage: str
) -> None:
    inputs = _technical_inputs()
    valid = _request_for_rules(
        [rule], inputs, supplied=(SensitiveRuleInput(rule_id, preimage),)
    )
    assert bind_document(valid).recognition_run_id == 11
    _assert_code(_request_for_rules([rule], inputs), "BINDING_RECOGNITION_INCONSISTENT")
    for wrong in ("x" * len(preimage), preimage[:-1]):
        rejected = _request_for_rules(
            [rule], inputs, supplied=(SensitiveRuleInput(rule_id, wrong),)
        )
        _assert_code(rejected, "BINDING_RECOGNITION_INCONSISTENT")
        assert preimage not in repr(rejected.recognition.candidates[0])
        sensitive_input = rejected.recognition.candidates[0].rule_input_values[0]
        assert preimage not in repr(sensitive_input)
        with pytest.raises(DocumentBindingError) as caught:
            bind_document(rejected)
        assert preimage not in str(caught.value)


def test_mixed_rules_only_require_sensitive_keyed_preimages() -> None:
    rules = [
        {
            "id": "mime",
            "required": True,
            "type": "mime_type",
            "value": "application/pdf",
            "weight": 1,
        },
        {
            "id": "text",
            "required": True,
            "type": "text_contains",
            "value": "marker",
            "weight": 1,
        },
    ]
    inputs = _technical_inputs()
    valid = _request_for_rules(
        rules,
        inputs,
        supplied=(SensitiveRuleInput("text", "Secret marker heading"),),
    )
    assert bind_document(valid).recognition_run_id == 11
    for supplied in (
        (SensitiveRuleInput("mime", "application/pdf"),),
        (SensitiveRuleInput("other", "Secret marker heading"),),
        (
            SensitiveRuleInput("text", "Secret marker heading"),
            SensitiveRuleInput("other", "unused"),
        ),
        (
            SensitiveRuleInput("text", "Secret marker heading"),
            SensitiveRuleInput("text", "Secret marker heading"),
        ),
    ):
        _assert_code(
            _request_for_rules(rules, inputs, supplied=supplied),
            "BINDING_RECOGNITION_INCONSISTENT",
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda r: replace(
            r,
            recognition=replace(r.recognition, completed_at=""),
        ),
        lambda r: replace(
            r,
            recognition=replace(r.recognition, candidates=()),
        ),
        lambda r: replace(
            r,
            recognition=replace(
                r.recognition,
                candidates=(replace(r.recognition.candidates[0], score=0.5),),
            ),
        ),
        lambda r: _replace_evidence_member(r, "recognition_evidence_schema", "v2"),
        lambda r: _replace_evidence_member(r, "unexpected", True),
        lambda r: _replace_rule_actual(r, {"kind": "mime_type", "value": "text/plain"}),
    ],
)
def test_complete_recognition_v1_projection_is_required(change: object) -> None:
    _assert_code(change(_request()), "BINDING_RECOGNITION_INCONSISTENT")  # type: ignore[operator]


@pytest.mark.parametrize(
    "change",
    [
        lambda r: replace(
            r,
            recognition=replace(
                r.recognition,
                candidates=(replace(r.recognition.candidates[0], rank_position=True),),
            ),
        ),
        lambda r: replace(r, selected_model=replace(r.selected_model, version_id=True)),
        lambda r: replace(
            r, recognition=replace(r.recognition, winner_model_version_id=True)
        ),
    ],
)
def test_boolean_integer_projections_are_rejected(change: object) -> None:
    _assert_code(change(_request()), "BINDING_RECOGNITION_INCONSISTENT")  # type: ignore[operator]


@pytest.mark.parametrize(
    "method", [SelectionMethod.MANUAL, SelectionMethod.EXPLICIT_CLI]
)
def test_explicit_modes_need_required_rules_but_not_threshold(
    method: SelectionMethod,
) -> None:
    result = bind_document(_request(method, RunOutcome.NOT_MATCHED, eligible=True))
    assert result.selection_method is method
    _assert_code(
        _request(method, RunOutcome.NOT_MATCHED, required=False, eligible=False),
        "BINDING_RECOGNITION_INELIGIBLE",
    )


@pytest.mark.parametrize(
    ("method", "policy"),
    [
        (SelectionMethod.AUTOMATIC, ModelBindingPolicy()),
        (SelectionMethod.MANUAL, ModelBindingPolicy(allow_automatic=True)),
        (
            SelectionMethod.EXPLICIT_CLI,
            ModelBindingPolicy(allow_automatic=True, allow_manual=True),
        ),
    ],
)
def test_model_and_system_policy_both_deny_by_default(
    method: SelectionMethod, policy: ModelBindingPolicy
) -> None:
    _assert_code(
        replace(_request(method), model_policy=policy), "BINDING_POLICY_DENIED"
    )
    _assert_code(
        replace(_request(method), system_policy=SystemBindingPolicy()),
        "BINDING_POLICY_DENIED",
    )


def test_absent_model_policy_is_deny_by_default() -> None:
    assert parse_model_binding_policy({}) == ModelBindingPolicy()


def test_metadata_precedence_constants_and_canonical_hash() -> None:
    contract = MetadataContract(
        (
            MetadataField("protected", MetadataType.STRING, True, constant="fixed"),
            MetadataField("choice", MetadataType.STRING, True, default="default"),
            MetadataField("from_default", MetadataType.INTEGER, True, default=3),
        )
    )
    request = replace(
        _request(),
        metadata_contract=contract,
        extracted_metadata={"choice": "extracted"},
        user_metadata={"choice": "user"},
    )
    first = bind_document(request)
    second = bind_document(
        replace(
            request,
            metadata_contract=MetadataContract(tuple(reversed(contract.fields))),
            user_metadata=dict(reversed(tuple(request.user_metadata.items()))),
        )
    )
    assert dict(first.document_metadata) == {
        "choice": "user",
        "from_default": 3,
        "protected": "fixed",
    }
    assert first.document_metadata_json == second.document_metadata_json
    assert first.metadata_sha256 == second.metadata_sha256
    assert (
        first.metadata_sha256
        == hashlib.sha256(first.document_metadata_json.encode("utf-8")).hexdigest()
    )


def test_metadata_rejections_are_closed_and_privacy_safe() -> None:
    contract = MetadataContract(
        (MetadataField("secret", MetadataType.INTEGER, True, constant=1),)
    )
    _assert_code(
        replace(_request(), metadata_contract=contract, user_metadata={"secret": "1"}),
        "BINDING_METADATA_CONFLICT",
    )
    _assert_code(
        replace(
            _request(), metadata_contract=contract, user_metadata={"url": "private"}
        ),
        "BINDING_METADATA_INVALID",
    )
    _assert_code(
        replace(
            _request(),
            metadata_contract=MetadataContract(
                (MetadataField("secret", MetadataType.INTEGER, True),)
            ),
        ),
        "BINDING_METADATA_REQUIRED",
    )
    with pytest.raises(DocumentBindingError) as caught:
        bind_document(
            replace(
                _request(),
                metadata_contract=contract,
                user_metadata={"secret": "top-secret"},
            )
        )
    assert "top-secret" not in str(caught.value)


def test_normalization_happens_before_canonical_serialization() -> None:
    request = replace(
        _request(),
        metadata_contract=MetadataContract(
            (
                MetadataField("decimal", MetadataType.DECIMAL, True),
                MetadataField("text", MetadataType.STRING, True),
            )
        ),
        user_metadata={"decimal": "1.2300", "text": "e\u0301"},
    )
    assert (
        bind_document(request).document_metadata_json == '{"decimal":"1.23","text":"é"}'
    )


@pytest.mark.parametrize(
    ("field_type", "value", "normalized"),
    [
        (MetadataType.DATE, "2024-02-29", "2024-02-29"),
        (
            MetadataType.DATETIME,
            "2026-01-01T01:00:00+01:00",
            "2026-01-01T00:00:00.000000Z",
        ),
        (MetadataType.DECIMAL, "-123.4500", "-123.45"),
    ],
)
def test_shared_scalar_normalization_accepts_valid_binding_inputs(
    field_type: MetadataType, value: object, normalized: object
) -> None:
    result = bind_document(
        replace(
            _request(),
            metadata_contract=MetadataContract(
                (MetadataField("value", field_type, True),)
            ),
            user_metadata={"value": value},
        )
    )
    assert dict(result.document_metadata)["value"] == normalized


@pytest.mark.parametrize(
    ("field_type", "value"),
    [
        (MetadataType.DATE, "not-a-date"),
        (MetadataType.DATE, "2023-02-29"),
        (MetadataType.DATETIME, "tomorrow"),
        (MetadataType.DATETIME, "2026-01-01T00:00:00"),
        (MetadataType.DECIMAL, "NaN"),
        (MetadataType.DECIMAL, "Infinity"),
        (MetadataType.DECIMAL, "-Infinity"),
    ],
)
def test_shared_scalar_normalization_rejects_invalid_binding_inputs(
    field_type: MetadataType, value: object
) -> None:
    _assert_code(
        replace(
            _request(),
            metadata_contract=MetadataContract(
                (MetadataField("value", field_type, True),)
            ),
            user_metadata={"value": value},
        ),
        "BINDING_METADATA_INVALID",
    )


@pytest.mark.parametrize(
    "field",
    [
        MetadataField("x", "unknown", True),  # type: ignore[arg-type]
        MetadataField("x", MetadataType.STRING, 1),  # type: ignore[arg-type]
        MetadataField("x", MetadataType.STRING, True, nullable=1),  # type: ignore[arg-type]
    ],
)
def test_malformed_public_metadata_declarations_are_rejected(
    field: MetadataField,
) -> None:
    _assert_code(
        replace(_request(), metadata_contract=MetadataContract((field,))),
        "BINDING_METADATA_INVALID",
    )


@pytest.mark.parametrize(
    ("values", "supplied"),
    [((1,), True), ((True,), 1)],
)
def test_enum_membership_requires_exact_json_scalar_type(
    values: tuple[object, ...], supplied: object
) -> None:
    request = replace(
        _request(),
        metadata_contract=MetadataContract(
            (MetadataField("choice", MetadataType.ENUM, True, values=values),)  # type: ignore[arg-type]
        ),
        user_metadata={"choice": supplied},
    )
    _assert_code(request, "BINDING_METADATA_INVALID")


def test_supersession_is_append_only_and_same_document() -> None:
    prior = SupersededBinding(4, 3, MODEL, "d" * 64)
    assert (
        bind_document(replace(_request(), supersedes=prior)).supersedes_binding_id == 4
    )
    _assert_code(
        replace(
            _request(), supersedes=replace(prior, binding_id=8), existing_binding_id=8
        ),
        "BINDING_SUPERSESSION_INVALID",
    )
    _assert_code(
        replace(_request(), supersedes=replace(prior, document_id=9)),
        "BINDING_SUPERSESSION_INVALID",
    )
    _assert_code(
        replace(_request(), supersedes=replace(prior, binding_id=True)),
        "BINDING_SUPERSESSION_INVALID",
    )
    _assert_code(
        replace(_request(), supersedes=prior, existing_binding_id=True),
        "BINDING_SUPERSESSION_INVALID",
    )


def test_public_requests_and_outcomes_are_immutable() -> None:
    request = _request()
    result = bind_document(request)
    with pytest.raises(FrozenInstanceError):
        request.document_id = 9  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.metadata_sha256 = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.user_metadata["new"] = "value"  # type: ignore[index]
