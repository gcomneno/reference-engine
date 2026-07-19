# Workflow v1

## Overview

The formal workflow is:

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

Each stage has explicit inputs, outputs, invariants, and failure modes. No stage may silently imply the next one.

## 1. Inbox

The inbox is a non-authoritative arrival area.

Files are:

- unregistered;
- unclassified;
- untrusted;
- potentially duplicated;
- potentially sensitive;
- not queryable.

Allowed operations:

- list;
- inspect MIME type, size, and hash;
- detect known content;
- register;
- explicitly ignore.

Possible outcomes:

- `new`
- `duplicate`
- `already_registered`
- `unsupported_file`
- `unreadable`
- `ignored`

Duplicate detection uses SHA-256 rather than filenames.

## 2. Registration

Registration transforms an unknown file into a known canonical document.

Inputs:

- inbox file;
- optional acquisition metadata.

Outputs:

- source artifact;
- document record;
- durable metadata sidecar;
- canonical vault location.

Operations include:

1. calculate SHA-256;
2. detect MIME type;
3. record size and original filename;
4. record source URL and acquisition time when available;
5. place the unchanged document in hash-addressed storage;
6. write a metadata sidecar;
7. register the relational projection.

Invariants:

- the original bytes are not modified;
- the same content hash is idempotent;
- the database contains no source BLOB;
- registration does not make extracted data queryable.

## 3. Recognition

Recognition MUST evaluate one immutable snapshot of a registered document
against exactly the active model versions. It MUST record deterministic
rule-level statuses, eligibility, normalized scores, ranks, safe evidence, and
one of `matched`, `not_matched`, `ambiguous`, `unsupported`, or `failed`.
Unavailable evaluation MUST NOT become an ordinary failed rule. Invalid rule
definitions and technical evaluation errors fail the run as defined by the
normative recognition contract.

Recognition is append-only and is only a proposal. It MUST NOT authorize
extraction or create a binding. Candidate selection, rule semantics, scoring,
ranking, outcomes, evidence, and persistence are defined normatively in the
[Recognition contract v1](recognition-v1.md).

## 4. Binding

Binding records the explicit decision:

```text
document <-> exact document-model version
```

Selection methods:

- `automatic`
- `manual`
- `explicit_cli`

Automatic binding is permitted only when:

- the recognition outcome is an unambiguous match;
- required rules pass;
- the threshold is met;
- model and system policy permit automatic binding.

An explicit model selection still evaluates required safety rules. A force override, if later supported, must be separate, conspicuous, and recorded.

Binding metadata combines declared constants, extracted values, and user-supplied values under deterministic precedence rules.

Bindings are immutable. A replacement binding points to the previous binding as superseded history.

## 5. Extraction

Inputs:

- binding;
- source document;
- exact normalized model definition;
- selected strategy;
- registered handler and version when applicable.

Internal phases:

```text
read
  -> raw extraction
  -> normalization
  -> natural-key construction
  -> structural validation
  -> declarative automatic validation
  -> provenance construction
  -> durable serialization
```

Outputs:

- extraction run;
- intermediate JSON artifact;
- complete dataset version in a non-queryable state;
- automatic validation findings.

Extraction states:

- `running`
- `completed`
- `failed`
- `discarded`

A completed extraction is not necessarily correct.

## 6. Validation

Validation compares one complete dataset version with the source and model contract.

Automatic validation covers:

- types;
- required fields;
- enums;
- natural-key uniqueness;
- temporal coverage;
- declarative rules;
- provenance requirements.

Human or mixed validation covers source fidelity, including layout-dependent details and exceptions.

For the ASCIT model this includes:

- every covered date;
- collection materials;
- explicit empty days;
- holidays and exceptions;
- source page and locator;
- complete annual coverage;
- duplicate-date prevention.

Decisions:

- `pending`
- `validated`
- `corrected`
- `rejected`

Blocking errors prevent approval.

### Corrections

Corrections are append-only:

```text
dataset vN
  -> correction artifact
  -> dataset vN+1 complete snapshot
  -> validation decision
```

The extracted snapshot remains unchanged. The corrected snapshot retains links to replaced records and source provenance.

## 7. Publication

Publication explicitly makes one approved dataset version available to ordinary queries.

Required conditions:

- valid binding;
- complete durable snapshot;
- compliant provenance;
- latest decision `validated` or `corrected`;
- no unresolved blocking findings;
- unique natural keys;
- model-version compatibility.

Publication does not modify record contents.

Each successful publication appends a `dataset_publications` event linked to
the durable publication manifest. A rollback appends another event selecting
an earlier eligible dataset version; it never deletes or rewrites the later
publication history.

A newly published version supersedes the previous active version for ordinary queries while preserving history.

Rollback is explicit and recorded. Re-publishing the already active version is an idempotent no-op.

## 8. Query

Inputs:

- model identity;
- query name;
- typed parameters;
- temporal context.

Execution:

1. resolve the model and query definition;
2. validate parameters;
3. build a restricted internal query plan;
4. query only approved views;
5. enforce declared cardinality;
6. return data and provenance.

A result includes:

- model ID and version;
- dataset and version;
- validation state;
- source document identity and SHA-256;
- page and locator;
- returned record fields.

Distinct error conditions include:

- unknown model;
- unknown query;
- invalid parameter;
- no published dataset;
- date outside coverage;
- explicit `none`;
- unresolved record;
- no matching record;
- multiple results for `cardinality: one`.

The engine never chooses a plausible record silently.

## Derived state machine

Logical document states include:

- inbox;
- registered;
- recognized;
- bound;
- extracted;
- pending validation;
- validated;
- published;
- superseded;
- rejected.

The state is derived from relationships and latest eligible decisions rather than maintained as one manually synchronized document-status column.

## Idempotency

- registering an existing document hash returns the existing document;
- registering the same model ID, version, and definition hash returns the existing version;
- registering the same version with a different hash fails with `MODEL_VERSION_CONFLICT`;
- recognition and extraction may create new runs;
- input fingerprints allow reproducibility comparison;
- publishing the already active version creates no duplicate active state.

## Failure handling

A failed run records technical failure without destroying prior successful state.

Examples:

- recognition failure leaves the registered document intact;
- extraction failure leaves the binding intact;
- rejected validation leaves earlier published data queryable;
- a pending newer dataset does not hide the active published dataset.

## Privacy controls

The workflow assumes local processing:

- no implicit upload;
- no implicit network handler;
- technical logs prefer IDs and hashes over document values;
- raw text retention can be disabled;
- transient files can be removed;
- filesystem permissions can reflect sensitivity;
- exports are explicit.
