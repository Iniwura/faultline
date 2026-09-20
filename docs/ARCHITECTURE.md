# FAULTLINE architecture

## Core model

FAULTLINE stores two node types in one namespace:

| Node | Immutable definition | Mutable freshness/result state |
| --- | --- | --- |
| `SOURCE` | ID, owner, HTTPS URL, tracked claim, definition fingerprint | revision, observed content hash, semantic result, state, revision fingerprint |
| `DECISION` | ID, owner, question, sorted dependency IDs, definition fingerprint | dependency revision snapshot, revision, result, state, revision fingerprint |

The namespace prevents a source and decision from sharing an ID. Definitions are never edited after registration/creation. The only graph mutation after creation is state/result evolution; a decision’s dependency set is never replaced.

## Graph direction

The decision record stores forward dependencies. `dependents` stores reverse edges as canonical sorted JSON arrays:

```text
source-a → decision-b → decision-c
```

This makes invalidation deterministic. The contract never asks an LLM to traverse the graph. `_propagate_stale` uses a bounded breadth-first walk over sorted reverse edges, with a 128-node and 32-depth cap.

Appending a decision can only point to nodes that already exist. Self-dependencies, duplicate edges, unknown nodes, fanout over 32, and cycle attempts are rejected. The explicit cycle guard walks existing dependency edges so future graph mutations cannot bypass the invariant.

## Source checks

`check_source` renders the registered URL using `gl.nondet.web.render`. A single `gl.eq_principle.strict_eq` call covers the rendered body hash and the exact model object. The model object must have exactly:

```json
{"change":"MATERIAL_CHANGE|NO_MATERIAL_CHANGE|UNRESOLVED","reason":"..."}
```

The validator therefore compares the complete structured observation, including the content hash and both semantic fields. No confidence score is accepted.

- `NO_MATERIAL_CHANGE`: source remains current and revision is unchanged.
- `MATERIAL_CHANGE`: revision increments, source becomes changed, and reverse dependents become stale.
- `UNRESOLVED` or an external/model failure: source becomes unresolved and dependents become stale; no revision is fabricated.

Provider errors are caught only to persist a generic fail-closed result. Their exception text is never stored.

## Decision rechecks

`recheck_decision` builds a canonical prompt from the question, current dependency state, current dependency revision, semantic/result records, and stored snapshot. Its exact model object is:

```json
{"outcome":"STILL_VALID|INVALIDATED|UNRESOLVED","reason":"...","affected_dependency_ids":["..."]}
```

Affected IDs must belong to the immutable dependency set and cannot repeat. `STILL_VALID` is rejected while any dependency is changed, stale, invalidated, or unresolved. This prevents a descendant from becoming effectively valid merely because an evaluator returned optimistic text.

`STILL_VALID` and `INVALIDATED` increment the decision revision and refresh the snapshot. `INVALIDATED` also propagates stale to its descendants. `UNRESOLVED` does not increment the decision revision and propagates stale so downstream decisions cannot be treated as fresh.

## Effective state

`get_effective_decision_state` is deterministic and bounded. A persisted valid decision is only effectively valid when every stored dependency revision matches and every dependency recursively resolves to a safe state (`SOURCE_CURRENT` or `DECISION_VALID`). A source revision mismatch, source change, invalidated dependency, or stale descendant reports an unsafe effective state without an LLM call.

## Fingerprints

SHA-256 fingerprints use canonical JSON with sorted keys and compact separators. Definition fingerprints bind all immutable definition fields. Revision fingerprints bind the node ID, revision, canonical result, observed hash or dependency snapshot, and the relevant semantic/result fields.
