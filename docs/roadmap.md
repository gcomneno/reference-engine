# Roadmap

The roadmap is dependency-driven. Each milestone has a verifiable exit condition and should be delivered through small, scoped issues.

## M0 - Project foundations

**Objective:** Create a reproducible, testable Python repository without application behavior.

**Scope:**

- Python 3.12+ package with `src/` layout;
- pytest, Ruff, and mypy;
- repository guardrails;
- approved architecture documentation;
- issue and pull-request conventions.

**Verifiable outcome:** The package imports, all initial checks pass, and architecture decisions are available in the repository.

**Primary dependencies:** None.

## M1 - Document model contract

**Objective:** Validate, normalize, and hash YAML document model v1.

**Scope:**

- standard JSON Schema;
- safe YAML parsing;
- deterministic normalization;
- canonical JSON;
- stable SHA-256;
- structured errors;
- valid and invalid fixtures;
- ASCIT example model after the generic contract exists.

**Verifiable outcome:** A valid model produces the same canonical definition and hash regardless of mapping order; unsafe or invalid models fail explicitly.

**Primary dependencies:** M0.

## M2 - SQLite persistence

**Objective:** Create the generic SQLite v1 projection and persist models immutably.

**Scope:**

- connection helper with foreign keys;
- atomic migration runner;
- complete generic schema;
- queryability views;
- integrity tests;
- repositories for artifacts, model identities, model versions, and query definitions.

**Verifiable outcome:** A database can be created from zero, constraints are enforced, repeated migrations are idempotent, and model-version conflicts are detected.

**Primary dependencies:** M0 and M1 for model persistence.

## M3 - Registration, recognition, and binding

**Objective:** Move one document from inbox to an explicit model-version binding.

**Scope:**

- hash-addressed registration;
- metadata sidecar;
- duplicate detection;
- deterministic recognition;
- score and rule evidence;
- ambiguous-result handling;
- automatic, manual, and explicit binding policies.

**Verifiable outcome:** A synthetic known document is registered, recognized, and immutably bound to the intended model version; ambiguous and non-matching documents are not extracted.

**Primary dependencies:** M1 and M2.

## M4 - Extraction and datasets

**Objective:** Produce a complete structured dataset with natural keys and provenance.

**Scope:**

- extraction strategy protocol;
- registered handler registry;
- intermediate JSON envelope;
- normalization pipeline;
- structural and declarative automatic validation;
- immutable dataset snapshots;
- synthetic ASCIT PDF fixture and handler.

**Verifiable outcome:** The synthetic ASCIT source produces a complete non-queryable dataset with stable natural keys and record-level provenance.

**Primary dependencies:** M3.

## M5 - Validation and publication

**Objective:** Approve, correct, and explicitly publish dataset versions.

**Scope:**

- validation runs and findings;
- human or mixed validation workflow;
- correction artifacts;
- new complete corrected snapshots;
- publication manifests;
- active-version projection;
- rollback and supersession.

**Verifiable outcome:** Only explicitly published `validated` or `corrected` versions appear through queryable views; pending and rejected versions remain excluded.

**Primary dependencies:** M4.

## M6 - Deterministic queries

**Objective:** Execute declared `today` and `by_date` queries with verifiable sources.

**Scope:**

- parameter validation;
- restricted internal query plan;
- typed field projections;
- cardinality enforcement;
- coverage-aware errors;
- response composition with provenance.

**Verifiable outcome:** “What waste is collected today?” returns the deterministic validated result or a precise non-data condition, always with source document hash, page, locator, model version, and dataset version.

**Primary dependencies:** M5.

## M7 - Reconstruction and hardening

**Objective:** Prove that SQLite is disposable and the workflow is safe for long-term personal use.

**Scope:**

- rebuild from durable artifacts;
- end-to-end tests;
- idempotency verification;
- privacy controls;
- log redaction;
- transient cleanup;
- recovery from failed runs;
- operational documentation.

**Verifiable outcome:** Delete SQLite, rebuild it, and obtain the same published query results from the same durable artifacts.

**Primary dependencies:** M6.

## Delivery rules

- Issues must remain small, autonomous, and ordered by dependency.
- Each issue must define included and excluded scope.
- Tests are part of the issue, not follow-up work.
- Core code must remain domain-neutral.
- No milestone may bypass validation or publication invariants for convenience.
- Work stops for architectural review if implementation begins to introduce a second source of truth, arbitrary YAML execution, or domain-specific core tables.
