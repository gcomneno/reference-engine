# SQLite data model v1

## Role of SQLite

SQLite is local, derived, disposable storage used for integrity, indexing, deterministic queries, and operational history.

It is not the canonical knowledge source. Original documents and durable derivative artifacts live outside the database. The database contains paths, hashes, normalized JSON, typed projections, and relationships; it does not contain source documents as BLOBs.

Every managed connection enables foreign-key enforcement.

Technical timestamps use ISO 8601 UTC text. Civil dates from documents remain date values interpreted using the model timezone.

## Artifact and model registry

### `artifacts`

Common registry for known files.

Fields:

- `id`
- `kind`
- `storage_scope`
- `retention_class`
- `relative_path`
- `sha256`
- `mime_type`
- `byte_size`
- `created_at`
- `registered_at`

`storage_scope` values:

- `vault`
- `workspace`

`retention_class` values:

- `canonical`
- `durable_derivative`
- `transient`

Artifact kinds remain extensible without a migration for every new derivative type.

### `document_models`

Stable identity independent of version:

- `id`
- `model_key`
- `title`
- `document_type`
- `record_type`
- `created_at`

`model_key` is unique and immutable.

### `document_model_versions`

Immutable definitions:

- `id`
- `document_model_id`
- `semantic_version`
- `schema_version`
- `status`
- `engine_compatibility`
- `artifact_id`
- `definition_json`
- `definition_sha256`
- `loaded_at`

Constraints include uniqueness of model identity plus semantic version and protection against silently redefining one version.

Statuses:

- `active`
- `deprecated`
- `disabled`

The YAML file remains an artifact. `definition_json` is the normalized deterministic representation.

### `model_query_definitions`

Declared query definitions:

- `id`
- `model_version_id`
- `query_name`
- `description`
- `definition_json`
- `definition_sha256`

A query name is unique within one model version. The JSON contains no free-form SQL.

## Documents, recognition, and binding

### `documents`

Registered canonical documents:

- `id`
- `source_artifact_id`
- `content_sha256`
- `original_filename`
- `source_url`
- `retrieved_at`
- `published_date`
- `page_count`
- `registered_at`

The same content hash identifies the same document content even when encountered under another filename.

### `recognition_runs`

One attempt to evaluate a document against available model versions:

- `id`
- `document_id`
- `engine_version`
- `started_at`
- `completed_at`
- `outcome`
- `error_code`
- `error_message`

Outcomes:

- `matched`
- `not_matched`
- `ambiguous`
- `unsupported`
- `failed`

### `recognition_results`

Evaluation of one model version within one recognition run:

- `id`
- `recognition_run_id`
- `model_version_id`
- `score`
- `eligible`
- `required_rules_passed`
- `rank_position`
- `details_json`

The details retain rule-by-rule evidence. Score is constrained to the normalized range; boolean flags are constrained to valid integer values.

### `document_bindings`

Explicit decision to process a document with an exact model version:

- `id`
- `document_id`
- `model_version_id`
- `recognition_run_id`
- `selection_method`
- `document_metadata_json`
- `metadata_sha256`
- `supersedes_binding_id`
- `bound_at`

Selection methods:

- `automatic`
- `manual`
- `explicit_cli`

A later binding may supersede an earlier binding, but does not rewrite it.

## Extraction and datasets

### `extraction_runs`

One extraction attempt:

- `id`
- `binding_id`
- `engine_version`
- `strategy`
- `handler_id`
- `handler_version`
- `options_sha256`
- `input_fingerprint`
- `started_at`
- `completed_at`
- `status`
- `output_artifact_id`
- `record_count`
- `error_code`
- `error_message`

Statuses:

- `running`
- `completed`
- `failed`
- `discarded`

`completed` means the technical run finished. It does not mean the output is validated or queryable.

The input fingerprint covers at least document hash, model definition hash, strategy, handler version, and normalized options. Identical fingerprints may be run again intentionally to verify reproducibility.

### `datasets`

Stable logical collection produced by a binding:

- `id`
- `binding_id`
- `record_type`
- `created_at`

The pair of binding and record type is unique.

### `dataset_versions`

Immutable complete snapshots:

- `id`
- `dataset_id`
- `sequence_number`
- `origin_type`
- `extraction_run_id`
- `base_dataset_version_id`
- `artifact_id`
- `record_count`
- `created_at`

Origin types:

- `extraction`
- `correction`
- `manual`
- `migration`

A correction creates a new complete snapshot. It does not store only an unmaterialized chain of patches.

## Records and typed query projection

### `records`

Immutable records belonging to one dataset version:

- `id`
- `dataset_version_id`
- `record_type`
- `ordinal`
- `natural_key_json`
- `natural_key_sha256`
- `data_json`
- `data_sha256`
- `valid_from`
- `valid_to`
- `supersedes_record_id`
- `created_at`

Constraints include:

- unique natural key within a dataset version;
- unique ordinal within a dataset version.

`data_json` is the complete record after structural and declarative validation against the exact model version. This does not imply human approval, publication, or queryability. It remains authoritative relative to the typed projection.

### `record_field_values`

Regenerable typed projection for fields used by queries or indexes:

- `record_id`
- `field_path`
- `value_index`
- `value_type`
- `text_value`
- `integer_value`
- `real_value`
- `decimal_value`
- `boolean_value`
- `date_value`
- `datetime_value`

Primary key:

```text
(record_id, field_path, value_index)
```

`field_path` uses JSON Pointer. Scalar fields use index zero; list values use consecutive indexes.

The projection avoids creating one schema table per document domain and avoids relying on unrestricted dynamic JSON SQL. Exact decimal text remains available even when a numeric helper projection is populated.

## Provenance

### `record_provenance`

Source relationship for a record or field:

- `id`
- `record_id`
- `source_artifact_id`
- `field_path`
- `page_number`
- `locator`
- `raw_text`
- `raw_text_sha256`
- `bounding_box_json`
- `provenance_order`

A null `field_path` applies to the complete record. A non-null path applies to one field.

Every queryable record must have provenance that satisfies its model requirements.

## Validation

### `validation_runs`

Append-only decisions for one dataset version:

- `id`
- `dataset_version_id`
- `sequence_number`
- `validator_kind`
- `validator_identity`
- `started_at`
- `completed_at`
- `decision`
- `report_artifact_id`
- `notes`

Validator kinds:

- `automatic`
- `human`
- `mixed`

Decisions:

- `pending`
- `validated`
- `corrected`
- `rejected`

A later validation run does not delete prior decisions.

### `validation_findings`

Findings produced by a validation run:

- `id`
- `validation_run_id`
- `severity`
- `code`
- `record_id`
- `field_path`
- `message`
- `details_json`
- `finding_order`

Severities:

- `info`
- `warning`
- `error`

Typical blocking codes include missing required data, duplicate natural keys, missing provenance, incomplete temporal coverage, invalid enum values, and invalid temporal ranges.

## Publication

### `dataset_publications`

Append-only relational projection of durable publication manifests:

- `id`
- `dataset_id`
- `dataset_version_id`
- `sequence_number`
- `publication_kind`
- `publication_artifact_id`
- `supersedes_publication_id`
- `published_at`

`publication_kind` values:

- `publish`
- `rollback`

Rules:

- sequence numbers are unique and increasing within one dataset;
- the selected dataset version must belong to the declared dataset;
- every row references its durable publication manifest;
- a rollback appends a new event selecting an earlier eligible version;
- prior events remain immutable;
- validation never creates a publication event implicitly;
- publishing the already active version is an idempotent no-op at the workflow layer.

The latest publication event selects the candidate active version. That version
is queryable only while its latest validation decision remains `validated` or
`corrected` and all other publication invariants are satisfied.

## Queryability views

### `latest_validation_decisions`

Returns the latest validation decision for every dataset version.

### `active_dataset_versions`

Selects the dataset version targeted by the latest `dataset_publications` event for each dataset, provided that version remains eligible. A pending or rejected newer dataset version does not hide the current published eligible version.

### `queryable_records`

Exposes only records belonging to active dataset versions.

### `queryable_record_fields`

Limits typed field projections to queryable records.

Ordinary query code must use these views rather than reading arbitrary extracted or pending records.

## Publication representation

Publication is an explicit workflow action represented durably by a manifest
and relationally by `dataset_publications`.

The table is generic and append-only. Its latest event selects the candidate
active dataset version, while the queryability views also verify the latest
eligible validation decision. Validation alone never activates a dataset.

## Indexes

Initial indexes support:

- artifact and document hashes;
- model definition hashes;
- recognition and binding foreign keys;
- extraction input fingerprints;
- dataset version sequence;
- natural-key lookup;
- typed date, text, and integer field filters;
- provenance lookup;
- validation sequence and findings.

Model-declared index suggestions determine which field paths are projected and indexed. They do not generate arbitrary SQL from YAML.

## Referential integrity and deletion

Default policy:

- `ON DELETE RESTRICT` for canonical artifacts, documents, models, bindings, datasets, and approved history;
- cascade only for wholly derived subordinate details whose parent cannot be meaningfully retained alone.

The normal application API does not expose destructive deletion of validated knowledge.

## Rebuildability

Durable inputs sufficient to reconstruct authoritative database state include:

- versioned YAML models and normalized definitions;
- original documents and metadata sidecars;
- binding manifests;
- complete validated or corrected dataset snapshots;
- provenance;
- correction artifacts;
- validation reports;
- publication manifests.

Transient failed-run telemetry may be lost without losing validated knowledge.

## Domain neutrality

The central schema contains no ASCIT columns or clinical columns. Domain records remain validated JSON with typed projections. Future domain-specific projections may improve performance, but they are derived and cannot replace the complete record or its provenance.
