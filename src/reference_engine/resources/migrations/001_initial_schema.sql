CREATE TABLE artifacts (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ('vault', 'workspace')),
    retention_class TEXT NOT NULL CHECK (retention_class IN ('canonical', 'durable_derivative', 'transient')),
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    created_at TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    UNIQUE (storage_scope, relative_path),
    UNIQUE (sha256, kind)
) STRICT;

CREATE TABLE document_models (
    id INTEGER PRIMARY KEY,
    model_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    document_type TEXT NOT NULL,
    record_type TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE document_model_versions (
    id INTEGER PRIMARY KEY,
    document_model_id INTEGER NOT NULL REFERENCES document_models(id) ON DELETE RESTRICT,
    semantic_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'deprecated', 'disabled')),
    engine_compatibility TEXT NOT NULL,
    artifact_id INTEGER NOT NULL UNIQUE REFERENCES artifacts(id) ON DELETE RESTRICT,
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    definition_sha256 TEXT NOT NULL CHECK (length(definition_sha256) = 64 AND definition_sha256 NOT GLOB '*[^0-9a-f]*'),
    loaded_at TEXT NOT NULL,
    UNIQUE (document_model_id, semantic_version)
) STRICT;

CREATE TABLE model_query_definitions (
    id INTEGER PRIMARY KEY,
    model_version_id INTEGER NOT NULL REFERENCES document_model_versions(id) ON DELETE CASCADE,
    query_name TEXT NOT NULL,
    description TEXT NOT NULL,
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    definition_sha256 TEXT NOT NULL CHECK (length(definition_sha256) = 64 AND definition_sha256 NOT GLOB '*[^0-9a-f]*'),
    UNIQUE (model_version_id, query_name)
) STRICT;

CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    source_artifact_id INTEGER NOT NULL UNIQUE REFERENCES artifacts(id) ON DELETE RESTRICT,
    content_sha256 TEXT NOT NULL UNIQUE CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    original_filename TEXT NOT NULL,
    source_url TEXT,
    retrieved_at TEXT,
    published_date TEXT,
    page_count INTEGER CHECK (page_count > 0),
    registered_at TEXT NOT NULL
) STRICT;

CREATE TABLE recognition_runs (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    engine_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('matched', 'not_matched', 'ambiguous', 'unsupported', 'failed')),
    error_code TEXT,
    error_message TEXT,
    CHECK (completed_at IS NULL OR completed_at >= started_at)
) STRICT;

CREATE TABLE recognition_results (
    id INTEGER PRIMARY KEY,
    recognition_run_id INTEGER NOT NULL REFERENCES recognition_runs(id) ON DELETE CASCADE,
    model_version_id INTEGER NOT NULL REFERENCES document_model_versions(id) ON DELETE RESTRICT,
    score REAL NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
    eligible INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    required_rules_passed INTEGER NOT NULL CHECK (required_rules_passed IN (0, 1)),
    rank_position INTEGER CHECK (rank_position > 0),
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    UNIQUE (recognition_run_id, model_version_id),
    UNIQUE (recognition_run_id, rank_position)
) STRICT;

CREATE TABLE document_bindings (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    model_version_id INTEGER NOT NULL REFERENCES document_model_versions(id) ON DELETE RESTRICT,
    recognition_run_id INTEGER REFERENCES recognition_runs(id) ON DELETE RESTRICT,
    selection_method TEXT NOT NULL CHECK (selection_method IN ('automatic', 'manual', 'explicit_cli')),
    document_metadata_json TEXT NOT NULL CHECK (json_valid(document_metadata_json)),
    metadata_sha256 TEXT NOT NULL CHECK (length(metadata_sha256) = 64 AND metadata_sha256 NOT GLOB '*[^0-9a-f]*'),
    supersedes_binding_id INTEGER REFERENCES document_bindings(id) ON DELETE RESTRICT,
    bound_at TEXT NOT NULL,
    CHECK (supersedes_binding_id IS NULL OR supersedes_binding_id <> id)
) STRICT;

CREATE TABLE extraction_runs (
    id INTEGER PRIMARY KEY,
    binding_id INTEGER NOT NULL REFERENCES document_bindings(id) ON DELETE RESTRICT,
    engine_version TEXT NOT NULL,
    strategy TEXT NOT NULL,
    handler_id TEXT,
    handler_version TEXT,
    options_sha256 TEXT NOT NULL CHECK (length(options_sha256) = 64 AND options_sha256 NOT GLOB '*[^0-9a-f]*'),
    input_fingerprint TEXT NOT NULL CHECK (length(input_fingerprint) = 64 AND input_fingerprint NOT GLOB '*[^0-9a-f]*'),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'discarded')),
    output_artifact_id INTEGER REFERENCES artifacts(id) ON DELETE RESTRICT,
    record_count INTEGER CHECK (record_count >= 0),
    error_code TEXT,
    error_message TEXT,
    CHECK (completed_at IS NULL OR completed_at >= started_at)
) STRICT;

CREATE TABLE datasets (
    id INTEGER PRIMARY KEY,
    binding_id INTEGER NOT NULL REFERENCES document_bindings(id) ON DELETE RESTRICT,
    record_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (binding_id, record_type)
) STRICT;

CREATE TABLE dataset_versions (
    id INTEGER PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
    origin_type TEXT NOT NULL CHECK (origin_type IN ('extraction', 'correction', 'manual', 'migration')),
    extraction_run_id INTEGER REFERENCES extraction_runs(id) ON DELETE RESTRICT,
    base_dataset_version_id INTEGER REFERENCES dataset_versions(id) ON DELETE RESTRICT,
    artifact_id INTEGER NOT NULL UNIQUE REFERENCES artifacts(id) ON DELETE RESTRICT,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (dataset_id, sequence_number),
    UNIQUE (id, dataset_id),
    CHECK (base_dataset_version_id IS NULL OR base_dataset_version_id <> id),
    CHECK ((origin_type = 'extraction' AND extraction_run_id IS NOT NULL) OR origin_type <> 'extraction')
) STRICT;

CREATE TABLE records (
    id INTEGER PRIMARY KEY,
    dataset_version_id INTEGER NOT NULL REFERENCES dataset_versions(id) ON DELETE CASCADE,
    record_type TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    natural_key_json TEXT NOT NULL CHECK (json_valid(natural_key_json)),
    natural_key_sha256 TEXT NOT NULL CHECK (length(natural_key_sha256) = 64 AND natural_key_sha256 NOT GLOB '*[^0-9a-f]*'),
    data_json TEXT NOT NULL CHECK (json_valid(data_json)),
    data_sha256 TEXT NOT NULL CHECK (length(data_sha256) = 64 AND data_sha256 NOT GLOB '*[^0-9a-f]*'),
    valid_from TEXT,
    valid_to TEXT,
    supersedes_record_id INTEGER REFERENCES records(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE (dataset_version_id, natural_key_sha256),
    UNIQUE (dataset_version_id, ordinal),
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from),
    CHECK (supersedes_record_id IS NULL OR supersedes_record_id <> id)
) STRICT;

CREATE TABLE record_field_values (
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    field_path TEXT NOT NULL CHECK (substr(field_path, 1, 1) = '/'),
    value_index INTEGER NOT NULL CHECK (value_index >= 0),
    value_type TEXT NOT NULL CHECK (value_type IN ('null', 'text', 'integer', 'real', 'decimal', 'boolean', 'date', 'datetime')),
    text_value TEXT,
    integer_value INTEGER,
    real_value REAL,
    decimal_value TEXT,
    boolean_value INTEGER CHECK (boolean_value IN (0, 1)),
    date_value TEXT,
    datetime_value TEXT,
    PRIMARY KEY (record_id, field_path, value_index),
    CHECK (
        (value_type = 'null' AND text_value IS NULL AND integer_value IS NULL AND real_value IS NULL AND decimal_value IS NULL AND boolean_value IS NULL AND date_value IS NULL AND datetime_value IS NULL) OR
        (value_type = 'text' AND text_value IS NOT NULL AND integer_value IS NULL AND real_value IS NULL AND decimal_value IS NULL AND boolean_value IS NULL AND date_value IS NULL AND datetime_value IS NULL) OR
        (value_type = 'integer' AND text_value IS NULL AND integer_value IS NOT NULL AND real_value IS NULL AND decimal_value IS NULL AND boolean_value IS NULL AND date_value IS NULL AND datetime_value IS NULL) OR
        (value_type = 'real' AND text_value IS NULL AND integer_value IS NULL AND real_value IS NOT NULL AND decimal_value IS NULL AND boolean_value IS NULL AND date_value IS NULL AND datetime_value IS NULL) OR
        (value_type = 'decimal' AND text_value IS NULL AND integer_value IS NULL AND real_value IS NULL AND decimal_value IS NOT NULL AND boolean_value IS NULL AND date_value IS NULL AND datetime_value IS NULL) OR
        (value_type = 'boolean' AND text_value IS NULL AND integer_value IS NULL AND real_value IS NULL AND decimal_value IS NULL AND boolean_value IS NOT NULL AND date_value IS NULL AND datetime_value IS NULL) OR
        (value_type = 'date' AND text_value IS NULL AND integer_value IS NULL AND real_value IS NULL AND decimal_value IS NULL AND boolean_value IS NULL AND date_value IS NOT NULL AND datetime_value IS NULL) OR
        (value_type = 'datetime' AND text_value IS NULL AND integer_value IS NULL AND real_value IS NULL AND decimal_value IS NULL AND boolean_value IS NULL AND date_value IS NULL AND datetime_value IS NOT NULL)
    )
) STRICT;

CREATE TABLE record_provenance (
    id INTEGER PRIMARY KEY,
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    source_artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
    field_path TEXT CHECK (field_path IS NULL OR substr(field_path, 1, 1) = '/'),
    page_number INTEGER CHECK (page_number > 0),
    locator TEXT,
    raw_text TEXT,
    raw_text_sha256 TEXT CHECK (raw_text_sha256 IS NULL OR (length(raw_text_sha256) = 64 AND raw_text_sha256 NOT GLOB '*[^0-9a-f]*')),
    bounding_box_json TEXT CHECK (bounding_box_json IS NULL OR json_valid(bounding_box_json)),
    provenance_order INTEGER NOT NULL CHECK (provenance_order >= 0),
    UNIQUE (record_id, field_path, provenance_order)
) STRICT;

CREATE TABLE validation_runs (
    id INTEGER PRIMARY KEY,
    dataset_version_id INTEGER NOT NULL REFERENCES dataset_versions(id) ON DELETE RESTRICT,
    sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
    validator_kind TEXT NOT NULL CHECK (validator_kind IN ('automatic', 'human', 'mixed')),
    validator_identity TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('pending', 'validated', 'corrected', 'rejected')),
    report_artifact_id INTEGER REFERENCES artifacts(id) ON DELETE RESTRICT,
    notes TEXT,
    UNIQUE (dataset_version_id, sequence_number),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
) STRICT;

CREATE TABLE validation_findings (
    id INTEGER PRIMARY KEY,
    validation_run_id INTEGER NOT NULL REFERENCES validation_runs(id) ON DELETE CASCADE,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    code TEXT NOT NULL,
    record_id INTEGER REFERENCES records(id) ON DELETE RESTRICT,
    field_path TEXT CHECK (field_path IS NULL OR substr(field_path, 1, 1) = '/'),
    message TEXT NOT NULL,
    details_json TEXT CHECK (details_json IS NULL OR json_valid(details_json)),
    finding_order INTEGER NOT NULL CHECK (finding_order >= 0),
    UNIQUE (validation_run_id, finding_order)
) STRICT;

CREATE TABLE dataset_publications (
    id INTEGER PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    dataset_version_id INTEGER NOT NULL,
    sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
    publication_kind TEXT NOT NULL CHECK (publication_kind IN ('publish', 'rollback')),
    publication_artifact_id INTEGER NOT NULL UNIQUE REFERENCES artifacts(id) ON DELETE RESTRICT,
    supersedes_publication_id INTEGER,
    published_at TEXT NOT NULL,
    UNIQUE (dataset_id, sequence_number),
    UNIQUE (dataset_id, id),
    FOREIGN KEY (dataset_version_id, dataset_id) REFERENCES dataset_versions(id, dataset_id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, supersedes_publication_id) REFERENCES dataset_publications(dataset_id, id) ON DELETE RESTRICT,
    CHECK (supersedes_publication_id IS NULL OR supersedes_publication_id <> id)
) STRICT;

CREATE TRIGGER dataset_publications_sequence_increasing
BEFORE INSERT ON dataset_publications
WHEN EXISTS (
    SELECT 1 FROM dataset_publications
    WHERE dataset_id = NEW.dataset_id
      AND sequence_number >= NEW.sequence_number
)
BEGIN
    SELECT RAISE(ABORT, 'dataset publication sequence must increase');
END;

CREATE TRIGGER dataset_publications_no_update
BEFORE UPDATE ON dataset_publications
BEGIN
    SELECT RAISE(ABORT, 'dataset publications are append-only: update forbidden');
END;

CREATE TRIGGER dataset_publications_no_delete
BEFORE DELETE ON dataset_publications
BEGIN
    SELECT RAISE(ABORT, 'dataset publications are append-only: delete forbidden');
END;

CREATE INDEX artifacts_sha256_idx ON artifacts(sha256);
CREATE INDEX model_versions_definition_sha256_idx ON document_model_versions(definition_sha256);
CREATE INDEX recognition_runs_document_idx ON recognition_runs(document_id);
CREATE INDEX recognition_results_model_idx ON recognition_results(model_version_id);
CREATE INDEX bindings_document_idx ON document_bindings(document_id);
CREATE INDEX bindings_model_version_idx ON document_bindings(model_version_id);
CREATE INDEX extraction_runs_binding_idx ON extraction_runs(binding_id);
CREATE INDEX extraction_runs_fingerprint_idx ON extraction_runs(input_fingerprint);
CREATE INDEX dataset_versions_dataset_sequence_idx ON dataset_versions(dataset_id, sequence_number DESC);
CREATE INDEX records_natural_key_idx ON records(natural_key_sha256);
CREATE INDEX record_fields_date_idx ON record_field_values(field_path, date_value) WHERE value_type = 'date';
CREATE INDEX record_fields_text_idx ON record_field_values(field_path, text_value) WHERE value_type = 'text';
CREATE INDEX record_fields_integer_idx ON record_field_values(field_path, integer_value) WHERE value_type = 'integer';
CREATE INDEX provenance_record_idx ON record_provenance(record_id, field_path);
CREATE INDEX validation_runs_version_sequence_idx ON validation_runs(dataset_version_id, sequence_number DESC);
CREATE INDEX validation_findings_run_idx ON validation_findings(validation_run_id, severity);
CREATE INDEX publications_dataset_sequence_idx ON dataset_publications(dataset_id, sequence_number DESC);

CREATE VIEW latest_validation_decisions AS
SELECT validation_runs.*
FROM validation_runs
JOIN (
    SELECT dataset_version_id, MAX(sequence_number) AS sequence_number
    FROM validation_runs
    GROUP BY dataset_version_id
) AS latest USING (dataset_version_id, sequence_number);

CREATE VIEW active_dataset_versions AS
SELECT dv.*
FROM dataset_publications AS publication
JOIN dataset_versions AS dv ON dv.id = publication.dataset_version_id
JOIN latest_validation_decisions AS validation
    ON validation.dataset_version_id = dv.id
WHERE validation.decision IN ('validated', 'corrected')
  AND NOT EXISTS (
      SELECT 1
      FROM dataset_publications AS later_publication
      JOIN latest_validation_decisions AS later_validation
        ON later_validation.dataset_version_id = later_publication.dataset_version_id
      WHERE later_publication.dataset_id = publication.dataset_id
        AND later_publication.sequence_number > publication.sequence_number
        AND later_validation.decision IN ('validated', 'corrected')
  );

CREATE VIEW queryable_records AS
SELECT records.*
FROM records
JOIN active_dataset_versions
    ON active_dataset_versions.id = records.dataset_version_id;

CREATE VIEW queryable_record_fields AS
SELECT record_field_values.*
FROM record_field_values
JOIN queryable_records ON queryable_records.id = record_field_values.record_id;
