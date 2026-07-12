# ADR 0005: No arbitrary code from YAML

- **Status:** Accepted

## Context

Document models are user-authored configuration and may eventually describe sensitive documents. Allowing YAML to import code, execute expressions, run SQL, invoke shell commands, or access the network would turn configuration into an untrusted execution channel.

## Decision

Parse YAML with a safe loader and reject executable custom tags. Do not evaluate Python or SQL expressions from model content.

Custom extraction and normalization use identifiers resolved through an explicit registry of installed, authorized handlers. Network access is never implicit and cannot be enabled by embedding executable content in a model.

## Positive consequences

- Models remain inspectable configuration.
- Loading a model does not execute code.
- Handler versions and permissions can be audited.
- Sensitive local workflows are not silently exported.

## Trade-offs and negative consequences

- Some complex models require separately installed extensions.
- The declarative operator set must evolve deliberately.
- Users cannot paste arbitrary scripts directly into models.

## Rejected alternatives

- `eval`-based expressions: rejected as unsafe and non-deterministic.
- Import handlers from arbitrary dotted paths: rejected because installation does not imply authorization.
- Allow raw SQL queries in YAML: rejected because it breaks storage abstraction and query safety.
- Permit handler-controlled network access by default: rejected for privacy.
