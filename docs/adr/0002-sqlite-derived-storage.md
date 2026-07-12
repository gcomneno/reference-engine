# ADR 0002: SQLite as derived storage

- **Status:** Accepted

## Context

The initial system is local and personal. It needs relational integrity, indexes, reproducible migrations, and deterministic queries, but not distributed infrastructure. The database must not become the only copy of validated knowledge.

## Decision

Use SQLite as the initial operational and query projection. Enable foreign keys on every managed connection. Store hashes, paths, normalized JSON, typed field projections, and workflow relationships. Do not store original documents as BLOBs.

Keep canonical originals and durable derivative artifacts in the vault. Require enough durable artifacts to rebuild SQLite.

## Positive consequences

- Minimal infrastructure and simple backup behavior.
- Strong integrity and transactional migrations.
- Efficient local deterministic queries.
- The database can be deleted without losing validated knowledge.

## Trade-offs and negative consequences

- Rebuild tooling is mandatory rather than optional.
- Some information exists both as durable artifacts and relational projections.
- SQLite concurrency and scale limits may require later review if the usage model changes.

## Rejected alternatives

- PostgreSQL or distributed services: rejected as unnecessary initial infrastructure.
- Files only with no database: rejected because integrity, query projection, and workflow relationships would become fragile.
- ORM-first design: rejected for the initial schema because explicit SQLite behavior and constraints are part of the contract.
