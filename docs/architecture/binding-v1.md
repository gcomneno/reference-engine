# Document binding contract v1

## Boundary and immutable inputs

Binding is a domain decision separate from recognition and persistence. Recognition
produces durable authorization evidence; binding verifies that evidence and returns
an immutable projection. It does not mutate a recognition run, write SQLite, extract
metadata, invoke a handler, or load executable behavior.

A request identifies one registered document by ID and SHA-256, one exact model
version and definition hash, one selection method, both policy inputs, durable
recognition evidence, metadata layers, and an optional predecessor projection. The
selection methods are `automatic`, `manual`, and `explicit_cli`.

## Policy and recognition authorization

The model policy and caller/system policy independently state whether each method is
allowed. Missing model policy and omitted typed permissions deny access. Both
policies MUST permit the requested method; there is no force override.

Every method requires a complete projection of a completed durable recognition run
for the same document, including canonical start/completion timestamps, the entire
ordered candidate-result set, and each candidate's exact durable model-definition
JSON and relational score/rank projections.
The run snapshot JSON and candidate evidence JSON MUST be canonical, match their
SHA-256 digests, and agree on the selected model identity and definition hash. The
snapshot must contain that candidate exactly once. Every required rule in the
candidate evidence must have status `evaluated_pass`. Binding applies the shared
recognition-v1 canonical, definition, decimal, scoring, ranking, and outcome rules;
abbreviated or freshly reconstructed rule evidence is not accepted.
Ordinary rule inputs, including MIME type and every other non-sensitive value, are
read directly from the canonical snapshot; callers MUST NOT duplicate them.
Evaluated `filename_regex`, `text_contains`, and `text_regex` rules are the only
rules that need caller-held preimages because their semantics cannot be recomputed
from a digest and length. The candidate's optional `rule_input_values` tuple contains
one `SensitiveRuleInput(rule_id, value)` per such rule. Rule IDs associate preimages
without positional coupling to unrelated rules. Missing, extra, duplicate, or
unknown associations are rejected. Binding verifies each supplied preimage's digest
and length against the canonical snapshot before the recognition-owned evaluator
recomputes the result. The value is excluded from object representations and stable
binding errors.

Automatic binding additionally requires outcome `matched`, an eligible rank-one
candidate, and that candidate as the persisted winner. Consequently `failed`,
`unsupported`, `ambiguous`, and `not_matched` never authorize automatic binding.
Missing, stale, incomplete, or inconsistent evidence never authorizes it either.

`manual` and `explicit_cli` apply identical evidence, required-rule, and policy
checks. They differ only as audit origins. Their candidate need not meet the score
threshold, rank first, or be the automatic winner. Either policy may deny either
method independently.

## Document metadata

Every key must be declared by the selected model. A field may declare a protected
`constant` or a fallback `default`, but not both. A constant cannot be overridden by
the extracted or user layer. Any supplied value that is not exactly equal with the
same runtime type is a conflict, even if normalization would later make it equal.

For non-constant fields precedence is:

```text
user-supplied > extracted > model default
```

`extracted` is only a caller-supplied mapping produced elsewhere; binding performs
no extraction. Undeclared keys are rejected, as are required fields still missing
after merging.

Chosen values are normalized by declared type. Strings use Unicode NFC; integers
and booleans retain exact JSON types; finite decimals become canonical decimal
strings; civil dates use ISO `YYYY-MM-DD`; timezone-aware datetimes become UTC with
six fractional digits; enums equal a declared JSON scalar. The normalized mapping
is serialized as locale-independent UTF-8 JSON with sorted keys and no insignificant
whitespace. Its lowercase SHA-256 covers those exact bytes, so input order has no
effect.

## Supersession and errors

A new binding may reference one positive prior binding ID for the same document and
carries the prior model and metadata identities as an immutable projection. The pure
contract validates exact positive integer identities, valid model/hash shapes,
same-document compatibility, and non-self-reference; it never rewrites prior data.

The projection is not proof that the predecessor exists. A trusted persistence
adapter MUST load the predecessor, verify that these projected facts match durable
state, and supply the identity of any binding being reconstructed before invoking
the pure contract. Binding performs no database access and does not authenticate a
caller-created `SupersededBinding` by itself.

Binding errors use closed `BINDING_*` codes and fixed messages. They expose no paths,
metadata values, matched text, URLs, secrets, arbitrary details, or stack data.
Later persistence and application services may retain results but cannot weaken
this contract.
