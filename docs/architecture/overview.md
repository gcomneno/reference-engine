# Architecture overview

## Purpose

GiadaWare Reference Engine turns configured document formats into structured, validated, queryable reference data while preserving a verifiable path back to the original source.

The engine is **model-driven**. It does not attempt to understand arbitrary documents. A versioned YAML document model declares how a specific document layout is recognized, which records it produces, how values are normalized and validated, what provenance is required, and which deterministic queries are available.

The guiding rule is:

> Intelligent, but not magical.

## System boundaries

Reference Engine is responsible for:

- original document registration and metadata;
- content identity through SHA-256;
- deterministic document recognition;
- explicit binding to a precise document-model version;
- structured extraction and normalization;
- structural, declarative, and human validation;
- immutable dataset versions and corrections;
- explicit publication;
- deterministic queries with source provenance;
- reconstruction of derived SQLite state from durable artifacts.

Reference Engine is not responsible for:

- distilling general lessons learned;
- generic retrieval-augmented generation;
- open-ended semantic interpretation;
- modifying source documents;
- answering from guesses when validated data is unavailable;
- microservices, vector databases, or heavy infrastructure in the initial architecture.

LeLe Manager remains a separate software project for generalized lessons learned. Both projects may share one personal knowledge vault, but their repositories, databases, derived artifacts, and workflows remain separate.

See [Boundaries and MVP](boundaries-and-mvp.md).

## Architectural principles

1. **Canonical originals**  
   The original document is canonical. Structured data is derived and must retain provenance.

2. **Model-driven behavior**  
   The core implements generic concepts. Domain knowledge belongs in versioned YAML models and explicitly registered handlers.

3. **Recognition is not binding**  
   Recognition proposes candidate models. Binding records the explicit decision to process a document with an exact model version.

4. **Validation is not publication**  
   A validated dataset is not automatically queryable. Publication is a separate, explicit state transition.

5. **Append-only history**  
   Models, bindings, extraction runs, dataset versions, corrections, validation decisions, and publications preserve history instead of silently rewriting it.

6. **Derived storage is disposable**  
   SQLite is a local query projection and operational index. Durable artifacts must be sufficient to rebuild it.

7. **No arbitrary execution from YAML**  
   YAML cannot directly execute Python, SQL, shell commands, imports, filesystem operations, or network calls.

8. **Local-first privacy**  
   The architecture must support sensitive documents without implicit uploads or content-rich logs.

## High-level flow

```text
inbox
  -> registration
  -> recognition
  -> binding
  -> extraction
  -> validation
  -> publication
  -> query
```

The detailed state transitions and invariants are defined in [Workflow v1](workflow-v1.md).

## Core concepts

| Concept | Meaning |
|---|---|
| Artifact | A known file with hash, storage location, retention class, and metadata. |
| Document | A registered original source document. |
| Document model | Stable model identity independent of version. |
| Model version | Immutable YAML definition used for recognition, extraction, validation, and queries. |
| Recognition run | Evaluation of one document against available model versions. |
| Binding | Explicit document-to-model-version decision. |
| Extraction run | Reproducible attempt to produce structured data. |
| Dataset version | Immutable complete snapshot of records. |
| Validation run | Append-only decision and findings for a dataset version. |
| Publication | Explicit selection of an approved dataset version for ordinary queries. |
| Provenance | Source location connecting a record or field to an original artifact. |

## Terminology

- **Canonical**: authoritative original input that the engine does not rewrite.
- **Durable derivative**: derived artifact retained because it is needed for audit, validation, or reconstruction.
- **Transient**: operational artifact that may be discarded without losing validated knowledge.
- **Extracted**: produced by an extraction run but not approved for use.
- **Validated**: approved without changing the dataset contents.
- **Corrected**: approved as a new complete snapshot containing explicit corrections.
- **Published**: explicitly selected for ordinary query use.
- **Queryable**: visible through the approved query views; this requires publication and an eligible validation decision.
- **Superseded**: retained historical version no longer selected for ordinary queries.

## Documentation map

- [Boundaries and MVP](boundaries-and-mvp.md)
- [Document model YAML v1](document-model-v1.md)
- [SQLite data model v1](data-model-v1.md)
- [Workflow v1](workflow-v1.md)
- [Knowledge vault layout](vault-layout.md)
- [Roadmap](../roadmap.md)
- [Architecture decision records](../adr/)
