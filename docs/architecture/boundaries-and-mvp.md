# Boundaries and MVP

## Product boundary

Reference Engine manages source-specific reference knowledge:

- original documents and acquisition metadata;
- source authority and temporal validity;
- model-driven recognition;
- structured extraction;
- normalized records and natural keys;
- provenance at document, record, or field level;
- validation, correction, and publication;
- deterministic queries with verifiable sources.

It does not manage generalized lessons learned. That responsibility belongs to LeLe Manager.

A future laboratory-report model illustrates the intended extensibility: the user may configure the layout produced by a specific laboratory, including recognition anchors, result fields, units, reference ranges, and provenance. The engine can then process that declared format. It does not claim to recognize every clinical report or produce medical diagnoses.

## Separation from LeLe Manager

| Reference Engine | LeLe Manager |
|---|---|
| Source-specific documents and facts | Generalized lessons learned |
| Temporal validity and provenance | Reusable distilled knowledge |
| Extraction and deterministic queries | Deduplication and lesson management |
| Canonical originals plus derived datasets | Canonical lesson documents |
| Source layout models | Lesson schema and editorial workflow |

The projects may use the same personal knowledge vault root, but they retain independent software repositories, derived storage, release cycles, and responsibilities.

## Explicit exclusions

The initial architecture excludes:

- microservices;
- event buses and distributed workers;
- vector databases;
- generic RAG;
- automatic interpretation of unknown formats;
- web UI;
- implicit network access;
- arbitrary user SQL;
- arbitrary executable plugins loaded from YAML;
- a table or code path hard-wired to ASCIT;
- a universal ontology covering unrelated domains.

These exclusions are architectural controls, not claims that such capabilities can never exist.

## MVP vertical

The first document model is the **ASCIT 2026 waste-collection calendar for Capannori, Zone 4**.

The MVP must support:

1. registering the original PDF without altering it;
2. calculating and retaining its SHA-256;
3. registering the precise YAML model version used;
4. deterministic recognition and explicit binding;
5. extracting a complete date-oriented dataset;
6. retaining source page and locator for each record;
7. validating ordinary dates, empty days, holidays, and exceptions;
8. publishing an approved dataset version;
9. executing `today` and `by_date`;
10. returning the answer with model version, document hash, page, locator, dataset version, and validation state.

Example query intent:

```text
today: What waste is collected today?
by_date: What waste is collected on 2026-12-25?
```

The core remains domain-neutral. ASCIT-specific vocabulary and parsing belong to the model and its registered extension.

## Calendar semantics

A covered date must distinguish:

- `scheduled`: one or more materials are explicitly collected;
- `none`: the source explicitly indicates no collection;
- `unresolved`: the data cannot be determined reliably.

A missing record is not automatically equivalent to `none`.

Holiday and exception rules must override ordinary recurring schedules. Query results must never guess a likely material when validated data is missing or unresolved.

## MVP completion criteria

The MVP is complete when:

- the source PDF remains canonical and unmodified;
- every covered date has explicit validated semantics;
- ordinary days and documented exceptions are handled correctly;
- every query result includes verifiable provenance;
- non-queryable states produce explicit errors instead of plausible answers;
- SQLite can be deleted and rebuilt from durable artifacts;
- rebuilt queries return the same results;
- tests cover ordinary days, holidays, explicit empty days, unresolved data, duplicate natural keys, missing provenance, and dates outside coverage.

## Growth boundary

After the MVP, new document types are introduced by adding:

- a new stable model identity;
- an immutable YAML model version;
- optional registered extraction or normalization handlers;
- synthetic tests and fixtures;
- declared query definitions.

Growth should not require changing core concepts or adding domain columns to the central schema. A domain-specific projection may be introduced later for performance, but it must remain derived and must not become the authoritative representation.
