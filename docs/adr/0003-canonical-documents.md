# ADR 0003: Canonical original documents

- **Status:** Accepted

## Context

The engine produces structured records from documents, but extraction may be corrected or replaced. A verifiable system needs an immutable source against which all derived data can be checked.

## Decision

Treat the unchanged original document as canonical. Identify content with SHA-256 and store it in hash-addressed vault storage. Treat normalized records, datasets, database rows, reports, and query projections as derived.

Every queryable record must retain provenance sufficient to reach the source artifact and location.

## Positive consequences

- Corrections never obscure the original evidence.
- Duplicate documents can be identified reliably.
- Query answers remain auditable.
- Rebuild and re-extraction can compare against stable bytes.

## Trade-offs and negative consequences

- The system must retain source files and provenance metadata.
- Corrected data may intentionally differ from a flawed extraction, requiring clear version and validation history.

## Rejected alternatives

- Make SQLite rows canonical: rejected because the database is derived and disposable.
- Replace source files with cleaned or normalized copies: rejected because transformations would erase original evidence.
- Trust filenames as identity: rejected because names are mutable and collide.
