# ADR 0001: Model-driven core

- **Status:** Accepted

## Context

Reference Engine must support more than the first ASCIT calendar without becoming a universal document-understanding system. Hard-coding each domain into the core would make every new format a schema and application redesign. Treating arbitrary documents as understandable without configuration would make results non-deterministic and unauditable.

## Decision

Build a generic core around artifacts, documents, model versions, recognition, bindings, extraction runs, datasets, records, provenance, validation, publication, and declared queries.

Each supported layout is described by an immutable YAML document model. Complex layout logic may use explicitly registered and authorized handlers. ASCIT is the first model and extension, not the core domain.

## Positive consequences

- New document formats can reuse stable workflow and storage concepts.
- Recognition, extraction, validation, and queries remain inspectable.
- Domain vocabulary stays outside the central schema.
- The same engine can later support a specific laboratory-report layout without claiming universal clinical understanding.

## Trade-offs and negative consequences

- Model design becomes a required user or developer task.
- Some layouts need custom registered handlers.
- The generic contract must be maintained carefully to avoid accidental domain leakage.

## Rejected alternatives

- Hard-code waste-calendar tables and services: rejected because the engine must grow beyond the MVP.
- Generic AI or RAG interpretation: rejected because deterministic extraction and provenance are required.
- One arbitrary plugin API with unrestricted imports: rejected for security and reproducibility.
