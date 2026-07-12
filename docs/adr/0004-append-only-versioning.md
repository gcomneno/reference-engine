# ADR 0004: Append-only versioning

- **Status:** Accepted

## Context

Recognition, model definitions, extraction, correction, and validation can change over time. Updating rows in place would make it impossible to determine which inputs and decisions produced a published answer.

## Decision

Preserve immutable model versions, bindings, extraction runs, complete dataset snapshots, validation runs, correction artifacts, and publication manifests.

A correction creates a new complete dataset version linked to its base. A replacement binding or publication supersedes earlier state without deleting it.

## Positive consequences

- Full audit trail for published answers.
- Rollback and comparison remain possible.
- Reproducibility can use exact model, document, handler, and dataset versions.
- Failed or rejected attempts do not destroy prior valid state.

## Trade-offs and negative consequences

- More rows and durable artifacts are retained.
- Active-state views and rebuild logic are more complex.
- Complete dataset snapshots duplicate some data.

## Rejected alternatives

- Mutable current-state rows: rejected because they erase decision history.
- Store only correction patches: rejected because long patch chains complicate validation and reconstruction.
- Delete rejected runs by default: rejected because technical and validation history may remain useful, although transient details may remain non-durable.
