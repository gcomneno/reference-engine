# Document model YAML v1

## Purpose

A document model defines the deterministic contract between one known document layout and the generic Reference Engine core.

The top-level schema version is:

```yaml
schema_version: 1
```

`schema_version` identifies the YAML contract understood by the engine. It is separate from `model.version`, which identifies an immutable semantic version of one document model.

## Top-level sections

Required sections:

- `model`
- `recognition`
- `extraction`
- `records`
- `provenance`
- `validation`
- `queries`

Optional sections:

- `document_metadata`
- `temporal`
- `indexes`
- `security`

Unknown properties are rejected where the contract is closed.

## Model identity and versioning

Conceptual structure:

```yaml
model:
  id: ascit.capannori-zone-4.waste-calendar
  version: 1.0.0
  title: ASCIT waste calendar - Capannori Zone 4
  document_type: waste_collection_calendar
  record_type: waste_collection_day
  status: active
  engine_compatibility: ">=0.1,<1.0"
  description: Annual door-to-door waste collection calendar
```

Rules:

- `model.id` is stable and immutable;
- `model.version` follows semantic versioning;
- one semantic version cannot be redefined with different content;
- every binding and extraction retains the exact model version and definition hash.

Version meaning:

- **patch**: correction that does not change produced data semantics;
- **minor**: compatible addition, such as an optional field;
- **major**: change to meaning, structure, natural key, or strategy.

## Recognition

Recognition uses explicit rules, required checks, weights, and a threshold.
Every rule contains exactly `id`, `type`, `value`, `required`, and `weight`;
rule IDs are unique and array order is significant. `weight` is a finite
non-negative number. Each rule type has a closed `value` shape: a string for
`mime_type`, `filename_regex`, `text_contains`, `text_regex`, and `sha256`; an
inclusive `{minimum, maximum}` integer object for `page_count_between`; or a
closed `{field, expected}` object for `metadata_equals`. The latter selects only
the supported technical document fields and is not JSONPath.

Supported rule types in v1:

- `mime_type`
- `filename_regex`
- `text_contains`
- `text_regex`
- `page_count_between`
- `sha256`
- `metadata_equals`

Recognition run outcomes:

- `matched`
- `not_matched`
- `ambiguous`
- `unsupported`
- `failed`

`unsupported` is reserved for an otherwise valid candidate that remains
indeterminate because immutable input or evaluator capability is unavailable.
Invalid definitions and technical evaluator faults produce `failed`, not
`unsupported`. The normative candidate-state and run-outcome rules are in the
[Recognition contract v1](recognition-v1.md).

Example:

```yaml
recognition:
  minimum_score: 0.85
  ambiguity_policy: reject
  rules:
    - id: pdf
      type: mime_type
      value: application/pdf
      required: true
      weight: 1
    - id: authority
      type: text_contains
      value: ASCIT
      required: true
      weight: 3
    - id: zone
      type: text_regex
      value: "(?i)capannori.*zona\\s*4"
      required: true
      weight: 4
```

The exact validation, input, normalization, capability, scoring, ranking,
evidence, and outcome semantics are defined by the normative
[Recognition contract v1](recognition-v1.md). Recognition proposes a model; it
does not create a binding.

## Document metadata

Metadata may be constant, extracted, or supplied explicitly:

```yaml
document_metadata:
  fields:
    authority:
      type: string
      required: true
      default: ASCIT
    year:
      type: integer
      required: true
      extraction:
        source: text_regex
        pattern: "\\b(20\\d{2})\\b"
        group: 1
```

The engine also records technical metadata independent of the model, including original filename, MIME type, SHA-256, relative vault path, source URL, acquisition time, page count, and registration time.

## Extraction

Declared strategies in the v1 contract:

- `pdf_text_regex`
- `pdf_table`
- `csv_rows`
- `json_records`
- `manual`
- `python_handler`

A model may declare a registered handler identifier:

```yaml
extraction:
  strategy: python_handler
  handler: ascit.calendar_2026.extract
  options:
    zone: 4
```

Handlers:

- are resolved through an explicit registry;
- must be installed and authorized;
- implement a core protocol;
- are not imported from arbitrary paths;
- cannot be executable code embedded in YAML.

The contract may define a strategy before the engine implements it. Unsupported strategies fail explicitly.

## Records

Example:

```yaml
records:
  type: waste_collection_day
  cardinality: many
  natural_key:
    - service_date
  fields:
    service_date:
      type: date
      required: true
    status:
      type: enum
      required: true
      values: [scheduled, none, unresolved]
    materials:
      type: list
      required: true
      items:
        type: enum
        values: [organic, paper, glass, multimaterial, residual, green_waste]
    notes:
      type: string
      required: false
      nullable: true
```

Primitive types:

- `string`
- `integer`
- `decimal`
- `boolean`
- `date`
- `datetime`
- `enum`
- `list`
- `object`

Rules:

- every field has one declared main type;
- `required: false` means the field may be absent;
- `nullable: true` means a present field may contain null;
- natural-key fields must exist;
- a natural key is non-empty, deterministic, canonical, and unique within a dataset version;
- ambiguous values remain explicit strings or modeled alternatives rather than guessed conversions.

## Normalization

A field may declare an ordered pipeline:

```yaml
normalize:
  - operation: trim
  - operation: lowercase
  - operation: map
    values:
      organico: organic
      carta: paper
```

Supported v1 operations:

- `trim`
- `collapse_whitespace`
- `lowercase`
- `uppercase`
- `replace`
- `map`
- `split`
- `parse_integer`
- `parse_decimal`
- `parse_date`
- `parse_datetime`
- `custom`

`custom` resolves only a registered normalizer identifier. Normalization is deterministic and does not perform creative interpretation.

## Temporal semantics

Supported modes:

- `none`
- `point_in_time`
- `date_range`
- `complete_date_range`

Example:

```yaml
temporal:
  timezone: Europe/Rome
  record_date_field: service_date
  document_validity:
    from: "2026-01-01"
    to: "2026-12-31"
  coverage:
    mode: complete_date_range
    explicit_empty_days: true
```

The model distinguishes explicit absence from extraction uncertainty. For complete date coverage, every date must resolve to an explicit status.

## Provenance

Modes:

- `document`
- `record`
- `field`

The ASCIT MVP requires at least record-level provenance.

Minimum source information:

- source document SHA-256;
- page number;
- stable locator.

Optional information:

- raw text;
- raw-text SHA-256;
- bounding box;
- multiple ordered source locations.

A queryable record must be traceable to a source artifact. A model may require field-level provenance for values taken from different locations.

## Validation

Validation has three layers.

### Structural validation

Derived from field declarations:

- required fields;
- declared types;
- enum membership;
- list item types;
- date and datetime formats;
- nullability.

### Declarative validation

Supported operators:

- `equals`
- `not_equals`
- `empty`
- `not_empty`
- `in`
- `not_in`
- `greater_than`
- `less_than`
- `between`
- `matches`
- `and`
- `or`
- `not`
- `implies`

Example:

```yaml
validation:
  rules:
    - id: scheduled-requires-materials
      severity: error
      assert:
        operator: implies
        if:
          operator: equals
          field: status
          value: scheduled
        then:
          operator: not_empty
          field: materials
```

No expression is evaluated as Python or SQL.

### Human validation

A human or mixed validation run compares the complete dataset with the source document and records findings and a decision:

- `pending`
- `validated`
- `corrected`
- `rejected`

`corrected` applies to a new complete dataset snapshot containing explicit corrections. It does not mutate the extracted snapshot.

## Query definitions

Queries are declarative data structures, not user SQL.

A query may declare:

- description;
- typed parameters;
- context values such as current date and timezone;
- field filters;
- allowed validation states;
- expected cardinality;
- returned fields.

Supported operators:

- `equals`
- `not_equals`
- `in`
- `greater_than`
- `greater_or_equal`
- `less_than`
- `less_or_equal`
- `between`
- `contains`

Queries cannot perform arbitrary SQL, code execution, network access, unrestricted filesystem access, or implicit cross-model joins.

## Index declarations

A model may suggest query fields:

```yaml
indexes:
  - fields: [service_date]
    unique: true
```

The storage adapter decides how to materialize the suggestion. The YAML contract does not depend on SQLite column names.

## Security

A future sensitivity policy may declare controls such as:

```yaml
security:
  sensitivity: health
  preserve_raw_text: false
  allow_network_handlers: false
  redact_logs: true
```

The base security rules always apply:

- safe YAML parser;
- no executable YAML tags;
- no `eval`;
- no arbitrary SQL or shell;
- no arbitrary imports;
- no implicit network;
- no personal absolute paths in versioned models;
- exact model artifact and hash retained for each extraction.

## Standard error codes

The v1 contract uses stable machine-readable error codes.

Model and document errors:

- `MODEL_INVALID`
- `MODEL_UNSUPPORTED_VERSION`
- `DOCUMENT_NOT_MATCHED`
- `DOCUMENT_AMBIGUOUS`

Extraction and handler errors:

- `UNSUPPORTED_EXTRACTION_STRATEGY`
- `HANDLER_NOT_REGISTERED`
- `HANDLER_NOT_ALLOWED`
- `EXTRACTION_FAILED`

Record, provenance, and validation errors:

- `RECORD_SCHEMA_INVALID`
- `RECORD_RULE_VIOLATION`
- `PROVENANCE_MISSING`
- `VALIDATION_REJECTED`

Query failures use stable codes in the `QUERY_*` family for conditions such as
an unknown query, invalid parameters, absence of a published dataset, no
matching record, or a cardinality violation.

Errors include a human-readable message and, where applicable, a model data
path or source location. They must not expose sensitive document values
unnecessarily.

## Intermediate extraction envelope

Every extraction produces a storage-independent JSON envelope containing at least:

- model ID and version;
- document SHA-256;
- record type;
- canonical natural key;
- normalized record data;
- provenance;
- initial state `extracted`.

The complete envelope is a derived artifact and must not be confused with publication or validation.
