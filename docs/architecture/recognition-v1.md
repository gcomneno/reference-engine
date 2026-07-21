# Recognition contract v1

## Purpose and normative language

This document is the normative contract for deterministically evaluating one
registered document against document-model versions. The key words **MUST**,
**MUST NOT**, **SHOULD**, and **MAY** are normative requirements.

Recognition only proposes a model version. It MUST NOT extract data, create a
dataset, or write `document_bindings`.

## Public orchestration boundary

`reference_engine.recognition.recognize_document()` is the domain-neutral public
application operation for recognizing one registered document. The caller supplies a
managed SQLite connection, the durable document ID, engine version, an explicit closed
capability snapshot, an optional bounded typed text-probe acquisition, and optionally a
timezone-aware clock. Filename, URL, MIME type, size, hash, registration time, and other
technical metadata are resolved from the registered `Document` and source `Artifact`.

When it owns the transaction, the service begins an immediate transaction before the
first lookup so the document, artifact, active model versions, and exact definition
bytes form one coherent view. It evaluates only the resulting immutable in-memory
snapshot, persists the complete append-only run through the recognition repository,
and commits after persistence succeeds. An existing caller transaction remains owned
by the caller and repository persistence uses its existing savepoint behavior.

The text probe is never accepted as an unbounded string: it is supplied only through
the recognition-v1 acquisition contract and must agree with the declared capability
limit and producer. Durable snapshots retain only safe scalar values and digests and
lengths for filename, URL, and probe text. Recognition performs no discovery or
extraction and never creates or changes a document binding.

## Invocation and immutable run snapshot

One explicit invocation MUST create one new append-only recognition run.
Repeating an invocation creates another run. Before rule evaluation, the engine
MUST take one immutable in-memory evaluation snapshot containing the actual
values required by the declared rules. Evaluators MUST consume that immutable
snapshot and MUST NOT reread changing database or filesystem state during the
run.

The durable run snapshot is the privacy-safe projection and fingerprint of that
evaluation snapshot. Sensitive values MAY be represented only by their digest
and length. It has this normative shape (values are illustrative):

```json
{
  "capabilities": [
    {
      "availability": "available",
      "configuration": {"maximum_code_points": 65536},
      "identifier": "recognition_text_probe.v1",
      "version": "probe-engine/1.2.0"
    }
  ],
  "candidates": [
    {
      "definition_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "model_key": "synthetic.report",
      "model_version_id": 101,
      "schema_version": 1,
      "semantic_version": "1.0.0",
      "status": "active"
    }
  ],
  "document_id": 41,
  "document_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "engine_version": "reference-engine/1.0.0",
  "safe_document_inputs": {
    "byte_size": 24831,
    "mime_type": "application/pdf",
    "original_filename": {
      "length": 10,
      "sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    },
    "page_count": 3,
    "recognition_text_probe": {
      "character_count": 4096,
      "limit": 65536,
      "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "truncated": false
    }
  },
  "snapshot_schema_version": "recognition-run-snapshot.v1",
  "source_artifact_id": 72
}
```

`safe_document_inputs` MUST contain every technical document input used by a
rule, either as a safe value or as the safe digest-and-length representation
defined below. A present null is represented by JSON `null`; a value that could
not be snapshotted is represented by exactly
`{"availability":"unavailable"}`. `capabilities` MUST contain every relevant capability's stable
identifier, version, availability (`available` or `unavailable`), and safe,
non-secret configuration. Capability configuration MUST identify the behavior that produced the
evaluation inputs; secrets, paths, source text, and credentials MUST NOT be
included.

The durable run snapshot is not necessarily a self-contained copy of every raw
evaluation value. Reproduction MUST reload the canonical document and durable
registration metadata, reconstruct the immutable evaluation snapshot using the
recorded capability versions and safe configuration, and verify every retained
safe value or digest before evaluation. A digest mismatch or unavailable
canonical input MUST fail reproduction explicitly. A digest alone MUST NOT be
passed to an evaluator that requires the original value.



The candidate set contains exactly the model versions whose persisted status
is `active` at snapshot time. It is ordered by model key ascending, then the
full semantic-version string ascending, then model-version ID ascending. IDs
are compared as integers and strings as Unicode code-point sequences. The
array preserves this deterministic snapshot order; it does not imply version
precedence. Later registry changes MUST NOT alter the snapshot.

The run snapshot MUST be serialized as canonical UTF-8 JSON. Object keys are
sorted by Unicode code-point order; arrays preserve their specified order;
`,` and `:` are the separators, with no added whitespace. JSON strings are
emitted as their Unicode characters encoded directly as UTF-8 except that
quotation mark, reverse solidus, and U+0000 through U+001F use JSON escapes;
the shortest escape is used, solidus is not escaped, and hexadecimal escape
digits are lowercase. Integers use their shortest base-10 form with no leading
zero or negative zero. Other numeric values MUST first be represented as a
canonical decimal string rather than a JSON number. NaN and Infinity are
forbidden. The snapshot SHA-256 is the lowercase hexadecimal SHA-256 over those
exact canonical UTF-8 bytes. These same serialization and hashing rules apply
to candidate evidence. Stored snapshot and evidence JSON MUST use this
canonical serialization.

No active candidates is a successful `not_matched` run with no candidate
results. It is not `unsupported` or `failed`.

## Decimal representations and model validation

Scoring MUST use arbitrary-precision decimal arithmetic, never binary
floating-point. Weights and `recognition.minimum_score` are parsed as exact
decimals from the canonical model JSON. The model values MUST be finite JSON
numbers; the threshold MUST be in `[0, 1]`, and each weight in `[0, 1000000]`.
Booleans are not numbers.

A **canonical decimal string** for recognition v1 has syntax `0` or
`[1-9][0-9]*(\.[0-9]*[1-9])?`. It is non-negative and has no leading zero,
trailing fractional zero, sign, or exponent. Thus `0`, `12`, and `0.125` are
canonical; `-0`, `-1`, `00`, `1.0`, `1e-3`, `NaN`, and `Infinity` are not.
Exact decimal values derived from model JSON MUST be normalized to this syntax
before being retained as evidence. Evidence MUST reject exponent notation,
negative zero, NaN, and Infinity.

A **canonical six-decimal string** has syntax
`(0|[1-9][0-9]*)\.[0-9]{6}`, with round-half-even used for the single
conversion from an exact value. It is only a display representation.

`recognition.ambiguity_policy`, when present, MUST equal `reject`. `rules` MUST
be a non-empty array in preserved declaration order, and rule IDs MUST be
unique. Every rule object contains exactly `id`, `type`, `value`, `required`,
and `weight`; `id` follows document-model v1 identifier syntax and `required`
is Boolean.

A registered definition violating a common or type-specific requirement
produces `invalid_rule_definition` for the affected rule. Registration SHOULD
reject it, but recognition MUST handle an invalid active persisted definition
deterministically. Unknown properties, rule types, or regex constructs are
invalid definitions, not unavailable capabilities.

## Rule evaluation statuses

Each rule has exactly one status:

| Status | Meaning | `passed` |
|---|---|---|
| `evaluated_pass` | Available input was evaluated and matched. | `true` |
| `evaluated_fail` | Available input was evaluated and did not match. | `false` |
| `unavailable_capability` | The rule is valid but its evaluator capability is absent. | absent |
| `unavailable_input` | The rule is valid but the immutable snapshot lacks its source value. | absent |
| `invalid_rule_definition` | The persisted rule violates this contract. | absent |
| `technical_evaluation_error` | Supported evaluation was attempted and encountered a technical fault. | absent |

Only the first two are evaluated results. The two `unavailable_*` statuses
describe otherwise valid rules and are the only rule statuses that can cause
`unsupported`. Invalid model definitions and evaluator faults MUST NOT be
classified as unsupported, ordinary rule failures, or zero contributions.

## Common comparison rules

Strings are compared exactly as snapshotted: case-sensitively, without
trimming, case folding, Unicode normalization, or locale transformation, unless
a rule explicitly says otherwise.

Regex rules use Python 3.12 Unicode `re.search` semantics. Input and pattern are
strings. Only inline `(?a)`, `(?i)`, `(?m)`, `(?s)`, `(?x)` flags and scoped
forms are permitted; `(?L)` is forbidden. Backreferences, lookaround,
conditionals, and named or numbered groups are permitted. Compile failure, a
forbidden flag, or a pattern over 4096 Unicode code points is
`invalid_rule_definition`. Resource exhaustion or evaluator termination is
`technical_evaluation_error` with code `REGEX_RESOURCE_EXHAUSTED`.

### `mime_type`

- `value` is a non-empty `type/subtype` string of lowercase ASCII token
  characters with no parameters.
- The source is the detected MIME type with parameters removed and ASCII
  letters lowercased; comparison is exact afterward.
- Missing input is `unavailable_input`; an absent metadata reader is
  `unavailable_capability`. Both MIME strings are safe evidence.

### `filename_regex`

- `value` is a non-empty regex satisfying the common rules.
- The source is `original_filename` as a basename. Matching uses search
  semantics, with no normalization and no case folding unless `(?i)` is used.
- Missing input is `unavailable_input`; an absent filename reader is
  `unavailable_capability`. Evidence exposes the pattern and SHOULD expose only
  filename SHA-256 and length, not the filename.

### `text_contains` and `text_regex`

- `value` is a non-empty string; `text_regex` also satisfies the regex rules.
- The source is the bounded recognition text probe. Literal containment and
  regex search are case-sensitive and unnormalized unless a regex flag applies.
- Missing probe input is `unavailable_input`; absent probe capability is
  `unavailable_capability`.
- Evidence exposes expected-string or pattern SHA-256 plus probe SHA-256, never
  either text or source excerpts.

### `page_count_between`

- `value` contains exactly integer `minimum` and `maximum`; Booleans are
  invalid, both are at least 1, and minimum is no greater than maximum.
- It passes when `minimum <= page_count <= maximum`.
- Missing input is `unavailable_input`; absent page-count capability is
  `unavailable_capability`. Bounds and actual integer are safe evidence.

### `sha256`

- `value` is exactly 64 ASCII hexadecimal characters, normalized lowercase.
- It is compared exactly with the normalized registered content SHA-256.
- Missing input is `unavailable_input`; absent hash reader is
  `unavailable_capability`. Both hashes are safe evidence.

### `metadata_equals`

`value` contains exactly `field` and `expected`. The closed v1 selector and
expected-value types are:

| Field | Required `expected` JSON type |
|---|---|
| `original_filename` | string |
| `mime_type` | string |
| `byte_size` | integer |
| `source_url` | string or null |
| `retrieved_at` | UTC timestamp string or null |
| `published_date` | civil-date string or null |
| `page_count` | positive integer or null |
| `registered_at` | UTC timestamp string |

Integers are JSON integers, not Booleans. Booleans and non-integral numbers are
invalid for every v1 selector. A UTC timestamp string uses the stored ISO 8601
UTC form with `Z`; a civil-date string uses `YYYY-MM-DD`. The expected JSON type
MUST match the field type above. Strings and integers compare exactly. MIME
type alone uses its rule normalization; no other field is normalized.

Every selected field MUST have a snapshot entry. A snapshotted null is an
available value and matches `expected: null`; a missing snapshot entry is
`unavailable_input`. An absent metadata-reader capability is
`unavailable_capability`. The selector and expected type are safe. MIME types,
sizes, counts, dates, and timestamps MAY be shown. Filenames, URLs, and other
sensitive strings default to SHA-256 and length.

## Bounded recognition text probe

Text rules consume `recognition_text_probe.v1`. A probe supplies no more than
its configured Unicode-code-point limit (absolute v1 maximum 65536), plus
actual count, truncation flag, deterministic producer identifier/version, and
SHA-256 of UTF-8 probe bytes. The producer MUST return the same probe for the
same document bytes, safe configuration, and producer version.

Recognition does not define how the probe is produced and MUST NOT trigger
extraction, OCR, arbitrary plugins, network calls, datasets, or provenance
creation. No authorized producer is `unavailable_capability`; an available
producer with no probe is `unavailable_input`; producer failure after an
attempt is `technical_evaluation_error`. Truncation is available input.

## Candidate states, eligibility, and scoring

The following predicates determine candidate state:

- **evaluated**: every rule is `evaluated_pass` or `evaluated_fail`;
- **definitively ineligible**: at least one required rule is `evaluated_fail`
  and no rule has `invalid_rule_definition` or `technical_evaluation_error`;
- **indeterminate**: no required rule is `evaluated_fail`, but at least one
  valid rule is `unavailable_capability` or `unavailable_input`.

The predicates can overlap: for example, a candidate with only evaluated
rules and a required failure satisfies both the first and second predicates.
The singular `candidate_state` field in persisted candidate evidence MUST use
this classification order: `definitively_ineligible`, then `indeterminate`,
then `evaluated`.

A candidate whose required rule failed remains definitively ineligible even
when another required or optional rule is unavailable. That unavailability
remains visible in evidence but does not poison the catalogue. Invalid
definitions and technical evaluation errors fail the run and are not candidate
states.

An evaluated candidate is eligible exactly when every required rule passes.
Only evaluated eligible candidates receive exact and display scores.
Definitively ineligible and indeterminate candidates have null exact numerator,
exact denominator, and display score. Because `recognition_results.score` is
non-null, such candidates use `0.0` as a compatibility sentinel. It is not an
actual score and MUST be ignored whenever exact-score evidence is null.

Ranks are run-relative rather than candidate-local. They are assigned only
after the run is known to contain neither a failed condition nor an
indeterminate candidate. Every result in a `failed` or `unsupported` run MUST
therefore have null `rank_position`, even when an evaluated eligible candidate
has a valid candidate-local score. A definitively ineligible candidate cannot
win.

For an evaluated eligible candidate, let `w_i` be each exact decimal rule
weight and `p_i` be 1 for pass or 0 for fail:

```text
numerator   = sum(w_i * p_i for every rule i)
denominator = sum(w_i for every rule i)
exact_score = numerator / denominator
```

If denominator is zero, exact score is zero by explicit rule. Candidate
evidence retains numerator and denominator as canonical decimal strings; that
pair is the exact score representation used for threshold comparison, ordering,
and ties. For nonzero denominators, threshold comparison MUST compare the
numerator with the exact product of denominator and threshold, and two scores
MUST be compared by cross-multiplying their numerator-denominator pairs. The
zero-denominator score compares as exact zero. No comparison performs division
or rounding. Threshold and every rule weight retained for reproducibility are
canonical decimal strings.

`recognition_results.score` remains only a SQLite `REAL` convenience
projection rounded to six decimals using round-half-even. It is approximate
and MUST NOT be used for thresholding, ordering, ties, reproducibility, or
binding. `details_json.display_score` is the corresponding canonical
six-decimal string, never a JSON number. Rule score contributions, when
retained, are canonical six-decimal strings or null and are display evidence
only.

## Ranking, winner, and run outcomes

When the run contains neither a failed condition nor an indeterminate
candidate, evaluated eligible candidates are ordered by exact score descending
and then candidate snapshot order. `rank_position` is the one-based ordinal.
Secondary keys make ranks deterministic but do not break an exact-score tie for
winner selection. A qualifier is an evaluated eligible candidate whose exact
score meets its own exact threshold.

In `failed` and `unsupported` runs every `rank_position` is null.

Outcome conditions are necessary and sufficient, in this order:

1. `failed` if an active candidate contains `invalid_rule_definition`, any
   rule produces `technical_evaluation_error`, or snapshotting, orchestration,
   or persistence fails. A failed run MUST NOT expose a winner.
2. `unsupported` if no failed condition exists and at least one candidate
   remains indeterminate. A definitively ineligible candidate's unavailable
   rules do not cause this outcome.
3. `ambiguous` if at least two qualifiers share the highest exact score.
4. `matched` if exactly one qualifier has the highest exact score.
5. `not_matched` if there is no qualifier, including an empty candidate set.

An indeterminate candidate conservatively prevents `matched`, `ambiguous`, and
`not_matched` because it might qualify. Lower-scoring qualifiers do not create
ambiguity. A tied rank-1 candidate is not a winner. Recognition never binds.

## Candidate evidence JSON

Each result's `details_json` contains only candidate-specific evidence and has
one unambiguous schema identifier:

```json
{
  "candidate_state": "evaluated",
  "display_score": "0.875000",
  "eligible": true,
  "exact_score": {"denominator": "8", "numerator": "7"},
  "model": {
    "definition_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "key": "synthetic.report",
    "semantic_version": "1.0.0",
    "version_id": 101
  },
  "recognition_evidence_schema": "recognition-candidate-evidence.v1",
  "required_rules_passed": true,
  "rules": [
    {
      "actual": {"kind": "mime_type", "value": "application/pdf"},
      "code": null,
      "expected": {"kind": "mime_type", "value": "application/pdf"},
      "id": "pdf",
      "passed": true,
      "required": true,
      "score_contribution": "0.125000",
      "status": "evaluated_pass",
      "type": "mime_type",
      "weight": "1"
    }
  ],
  "run_snapshot_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "threshold": "0.85"
}
```

The complete run snapshot and candidate set MUST NOT be duplicated here.
`run_snapshot_sha256` references the persisted run snapshot. The candidate
evidence SHA-256 is the lowercase SHA-256 of the exact persisted `details_json`
bytes, which MUST be this entire object serialized by the canonical rules
above. It is derived from the persisted candidate evidence and is not a second
field inside the object being hashed. `required_rules_passed` is true exactly
when every required rule passes. `code` is null for evaluated rules and
otherwise is a stable capability, reason, or error code, never a free-form
exception message.

For a definitively ineligible candidate, `candidate_state` is
`definitively_ineligible`, `eligible` and `required_rules_passed` are false,
and `exact_score` and `display_score` are null.

For an indeterminate candidate, `candidate_state` is `indeterminate` and
`eligible` is null. `required_rules_passed` is true when every required rule is
`evaluated_pass`; it is null when at least one required rule is unavailable and
none has failed. This distinguishes an unavailable optional rule from an
unavailable required rule.

Every unavailable rule remains in `rules` with null `passed` and
`score_contribution`. The relational `rank_position` is null. The current
non-null relational `eligible` column uses `0` for both false and unknown. The
non-null relational `required_rules_passed` column uses `0` for both false and
unknown. Candidate evidence is authoritative for the distinction.

`expected` and `actual` use `{"kind":...,"value":...}` for safe values or
`{"kind":...,"sha256":...,"length":...}` for sensitive strings. Missing or
unavailable actual values are null. Evidence MUST NOT contain source text,
matched passages, filenames, URLs, arbitrary metadata, secrets, paths, stack
traces, or sensitive exception data by default.

## Persistence lifecycle and schema decision

The run records the exact engine version and `started_at`/`completed_at`
UTC timestamps with `Z` and microsecond precision. A completed run, its
canonical snapshot and hash, and its complete candidate-result set MUST be
inserted atomically. A caller-owned transaction MAY contain that unit; the
repository MUST use a savepoint and MUST NOT commit or roll back unrelated
caller work.

Partial candidate-result sets MUST NOT be persisted. When
`invalid_rule_definition` or `technical_evaluation_error` is recorded but
deterministic orchestration still completes every candidate evaluation, the
`failed` run MUST persist exactly one result for every active candidate so that
the complete evidence remains auditable. A candidate containing either failed
status has null `candidate_state`, `eligible`, `required_rules_passed`,
`exact_score`, and `display_score`.

When a run-level fault prevents completion of the full candidate-result set,
the engine SHOULD persist a `failed` run with no candidate results. It MUST NOT
persist only the successfully evaluated prefix.

A failed run MUST expose no winner. It SHOULD retain a stable `error_code`, a
redacted `error_message`, its completion time, and the snapshot and hash when
snapshotting completed. If the database cannot persist that row, the caller
receives the error. Redacted messages contain no document values, paths, URLs,
YAML, SQL parameters, stack traces, or source text.

Migration 002 adds `input_snapshot_json` and `input_snapshot_sha256` to
`recognition_runs`. The JSON column accepts only null or valid JSON. The hash
column accepts only null or a 64-character lowercase hexadecimal SHA-256.

Database triggers require both columns to be null or both non-null and require
the pair for every new non-failed run. A new failed run may retain both null
only when its fault prevented snapshot completion. When snapshotting completed,
the failed run persists both values.

The invariant is forward-only because SQLite validates an `ALTER TABLE` column
constraint against existing rows. Pre-migration recognition runs are therefore
preserved with both columns null rather than receiving fabricated evidence.
Such legacy rows are not recognition-v1-conformant. Updates to their outcome or
snapshot fields must first establish a valid final snapshot state.

Persistence MUST NOT claim full recognition-v1 conformance until the
recognition repository and orchestration enforce the lifecycle policy defined
above.

## Binding boundary

For `matched`, recognition exposes the run ID, document identity and SHA-256,
winner model identity/version and definition SHA-256, the persisted run
snapshot SHA-256, the candidate evidence SHA-256 computed from the persisted
canonical `details_json`, exact numerator and denominator, exact threshold,
eligibility, and required-rules-passed flag. Binding receives or recomputes the
exact score from the persisted exact numerator and denominator and MUST never
use SQLite `REAL` or `display_score`. It independently verifies the run is
`matched`, the stored snapshot hash matches the persisted canonical snapshot,
the supplied candidate evidence hash matches the persisted canonical
`details_json`, the winner is unchanged, and binding is allowed. Other
outcomes expose no winner. Recognition MUST NOT write `document_bindings`.

## Synthetic outcome examples

Hashes and omitted evidence are synthetic. `—` means not applicable or null.

### Matched

| Candidate | State | Required-rule result | Unavailable/error | Exact score | Threshold | Rank | Run outcome |
|---|---|---|---|---|---|---|---|
| `alpha` | evaluated | pass | none | `9/10` | `0.85` | 1 | `matched` (winner) |
| `beta` | evaluated | pass | none | `7/10` | `0.8` | 2 | `matched` |

### Not matched

An unavailable optional rule remains visible on `gamma`, but its required
failure makes it definitively ineligible and does not make the run unsupported.

| Candidate | State | Required-rule result | Unavailable/error | Exact score | Threshold | Rank | Run outcome |
|---|---|---|---|---|---|---|---|
| `gamma` | definitively_ineligible | fail | optional `unavailable_input` | — | `0.8` | — | `not_matched` |

### Ambiguous

| Candidate | State | Required-rule result | Unavailable/error | Exact score | Threshold | Rank | Run outcome |
|---|---|---|---|---|---|---|---|
| `delta` | evaluated | pass | none | `3/4` | `0.7` | 1 | `ambiguous` |
| `epsilon` | evaluated | pass | none | `3/4` | `0.7` | 2 | `ambiguous` |

### Unsupported

| Candidate | State | Required-rule result | Unavailable/error | Exact score | Threshold | Rank | Run outcome |
|---|---|---|---|---|---|---|---|
| `zeta` | indeterminate | no required failure | required `unavailable_capability` | — | `0.8` | — | `unsupported` |

### Failed

| Candidate | State | Required-rule result | Unavailable/error | Exact score | Threshold | Rank | Run outcome |
|---|---|---|---|---|---|---|---|
| `eta` | — (run failed) | no required failure | `technical_evaluation_error` | — | `0.8` | — | `failed` |

An active candidate with `invalid_rule_definition` produces the same `failed`
outcome and no winner; it is never `unsupported`.

## Bounded follow-up implementation issues

Migration 002 implements the schema prerequisite by adding canonical run
snapshot JSON and its SHA-256 to `recognition_runs`, including forward-only
database enforcement of the snapshot-pair and non-failed-run invariants.

The recognition implementation remains dependent on that migration and MUST
not claim full v1 persistence conformance until its repository and
orchestration enforce the pre-snapshot failure policy.

After that dependency, implementation scope is limited to recognition domain
types, evaluators for already available capabilities, SQLite recognition
repositories, deterministic orchestration, and synthetic tests for rules,
exact scores, projections, ranks, outcomes, evidence hashes, transactions, and
redaction. It excludes binding, extraction, OCR, CLI behavior, ASCIT-specific
logic, arbitrary plugins, network access, further schema changes, datasets,
validation, publication, and queries.
