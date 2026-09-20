# FAULTLINE threat model

## Protected invariants

- A source or decision definition cannot be silently edited.
- An edge cannot point at an unknown node, duplicate an existing edge, self-reference, exceed fanout, or create a cycle through the supported graph API.
- A material source change cannot leave a descendant effectively valid.
- An invalidated decision cannot leave its descendants fresh.
- A source revision only increments for a validated `MATERIAL_CHANGE` result.
- A decision revision only increments for a completed `STILL_VALID` or `INVALIDATED` re-evaluation.
- A malformed, incomplete, contradictory, or failed model/network result never becomes success.

## Consensus assumptions

Rendered web content and model classifications are nondeterministic. Every such operation is inside strict structured consensus. The validator compares the full structured object, not only the enum outcome or a human-readable reason.

Strict equality does not prove that a website is honest, that the tracked claim is well chosen, or that the model’s materiality judgment is objectively correct. It ensures validators agree on the same bounded observation and makes disagreement fail closed.

## External evidence risks

The registered URL is immutable and must use HTTPS. The contract does not claim TLS identity, source permanence, or archival evidence beyond the content hash recorded for an observation. A source can become unavailable; that produces `SOURCE_UNRESOLVED` and makes dependent decisions unsafe.

The model is given the tracked claim, current rendered content, prior content hash, and prior semantic result. It is instructed to return no confidence score and an exact schema. The contract validates keys, types, enum values, reason bounds, and affected dependency membership.

## Resource exhaustion

All user-controlled strings, dependency fanout, dependency count, observed body size, propagation node count, propagation depth, and model reason size are bounded. Reverse adjacency arrays are sorted before storage. A propagation bound failure reverts the write rather than partially accepting a source change.

## Out of scope

FAULTLINE does not implement identity administration, token transfers, frontend authentication, source allowlists beyond HTTPS, legal certification, timestamp-based expiry, or a guarantee that a valid conclusion remains true after the next unchecked fact change. Clients should read effective state and trigger checks/rechecks as part of their own freshness policy.
