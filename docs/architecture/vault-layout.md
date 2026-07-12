# Knowledge vault layout

## Purpose

The knowledge vault stores canonical originals and durable derivative artifacts. It is separate from the software repository and from the disposable SQLite database.

The root is configured with:

```text
REFERENCE_ENGINE_VAULT_DIR
```

Additional local paths:

```text
REFERENCE_ENGINE_WORK_DIR
REFERENCE_ENGINE_DATABASE_PATH
```

- the vault contains durable artifacts;
- the work directory contains transient operational files;
- the database path points to derived SQLite state.

No personal absolute path is embedded in a versioned model.

## Conceptual layout

```text
$REFERENCE_ENGINE_VAULT_DIR/
├── inbox/
├── documents/
│   └── sha256/
│       └── ab/
│           └── abcdef.../
│               ├── source.pdf
│               └── metadata.yaml
├── models/
│   └── <model-id>/
│       └── <version>/
│           ├── model.yaml
│           ├── normalized.json
│           └── manifest.yaml
├── bindings/
│   └── <binding-id>/
│       └── binding.yaml
├── datasets/
│   └── <dataset-id>/
│       ├── v0001/
│       │   ├── dataset.json
│       │   └── manifest.yaml
│       └── v0002/
│           ├── dataset.json
│           └── manifest.yaml
├── corrections/
│   └── <dataset-id>/
│       └── v0001-to-v0002/
│           └── correction.json
├── validations/
│   └── <dataset-id>/
│       └── v0002/
│           ├── report.yaml
│           └── findings.json
└── publications/
    └── <dataset-id>/
        └── <timestamp>.yaml
```

## Hash-addressed documents

Documents are stored by content hash, not by semantic classification.

Benefits:

- filename collisions are irrelevant;
- duplicate content is detected;
- a document does not move when classification changes;
- bindings can change without changing the canonical source path;
- provenance can use stable content identity.

A readable by-model index may be generated as a derivative convenience, but it is not canonical.

## Artifact authority classes

### Canonical

Examples:

- unchanged original document;
- authoritative user-supplied source metadata when required.

Canonical artifacts are never silently rewritten.

### Durable derivative

Examples:

- normalized model definition;
- binding manifest;
- complete extracted dataset retained for audit;
- validated or corrected dataset snapshot;
- correction artifact;
- validation report;
- publication manifest.

Durable derivatives are derived but retained because they are required for reproducibility, audit, or database reconstruction.

### Transient

Examples:

- temporary page images;
- intermediate parser output not selected for retention;
- failed-run scratch files;
- caches;
- generated SQLite database.

Transient artifacts may be deleted without losing validated knowledge.

## Document sidecar

A registration sidecar contains enough information to rebuild document identity and acquisition metadata, including:

- schema version;
- document SHA-256;
- original filename;
- MIME type;
- byte size;
- source URL when available;
- retrieval time;
- registration time.

The sidecar does not change the original document.

## Model artifacts

Each immutable model version retains:

- original YAML;
- normalized deterministic JSON;
- manifest with model identity, semantic version, schema version, hashes, and compatibility.

The normalized representation is derived. The original YAML remains the human-authored model artifact.

## Binding manifest

A binding manifest records:

- document hash;
- exact model ID and version;
- model-definition hash;
- selection method;
- recognition-run reference when applicable;
- normalized document metadata;
- predecessor binding when superseding;
- binding timestamp.

## Dataset artifacts

Every dataset version is a complete snapshot.

A dataset manifest records:

- dataset identity;
- version sequence;
- record type;
- origin type;
- binding;
- source extraction run or base dataset version;
- record count;
- artifact hashes;
- creation time.

A correction artifact explains the transition between complete snapshots, but reconstruction does not depend on applying an unbounded patch chain.

## Validation artifacts

A validation report records:

- dataset version;
- validator kind and identity when appropriate;
- decision;
- findings summary;
- unresolved warnings;
- report timestamp;
- relevant hashes.

Sensitive field values should not be copied into logs unless required and permitted by policy.

## Publication manifests

Publication is represented durably and explicitly.

A manifest identifies:

- dataset;
- published dataset version;
- validation decision;
- publication time;
- previous active version when applicable;
- rollback or supersession reason when applicable.

Rebuilding SQLite must not infer publication merely from validation.

## Rebuild contract

The following durable artifacts reconstruct authoritative state:

- models;
- documents and sidecars;
- bindings;
- dataset snapshots;
- provenance;
- correction records;
- validation decisions and findings;
- publication manifests.

Operational telemetry that was never retained as a durable artifact may be absent after rebuild.

## Privacy

For sensitive domains:

- vault permissions should limit access;
- raw text retention may be disabled;
- derived artifacts should be separated by sensitivity policy;
- temporary files should be cleaned;
- exports require explicit action;
- public repository fixtures must be synthetic.
