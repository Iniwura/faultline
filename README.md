# FAULTLINE

FAULTLINE is a GenLayer Intelligent Contract for tracking decisions whose validity depends on changing real-world facts.

```
SOURCE → CHANGE → CASCADING STALENESS → RECHECK
```

## Why it matters

A decision can remain stored while the facts behind it change. FAULTLINE records those dependencies, checks sources semantically, and makes affected decisions stale instead of silently treating old conclusions as current.

## Model

- `SOURCE` nodes hold an HTTPS URL, tracked claim, observed content hash, semantic result, revision, and freshness state.
- `DECISION` nodes hold a question, immutable dependency IDs, a revision snapshot, result, and state.
- The graph is `SOURCE → DECISION → DECISION`; reverse edges make stale propagation deterministic.
- New decisions start `DECISION_STALE` and require a recheck before they can become valid.

Source checks render the registered page and ask for a bounded semantic result:

```json
{"change":"MATERIAL_CHANGE|NO_MATERIAL_CHANGE|UNRESOLVED","reason":"..."}
```

Decision rechecks evaluate the question against current dependency state, revisions, semantic/result records, and the stored snapshot:

```json
{"outcome":"STILL_VALID|INVALIDATED|UNRESOLVED","reason":"...","affected_dependency_ids":["..."]}
```

GenLayer validator functions rerun the bounded nondeterministic evaluation and require agreement on the source change enum, or on the decision outcome and affected dependency IDs. The contract validates result shape, enums, reason bounds, content hashes, dependency membership, and duplicate IDs before state changes.

Only `MATERIAL_CHANGE` increments a source revision. `STILL_VALID` and `INVALIDATED` increment a decision revision. `UNRESOLVED` fails closed and propagates safety impact without fabricating a revision.

The graph rejects unknown, duplicate, self-referential, over-fanout, and cyclic dependencies. Propagation and effective-state checks are bounded by explicit node/depth limits. Revision snapshots and SHA-256 fingerprints bind definitions and state transitions. `get_effective_decision_state` deterministically detects stale snapshots and unsafe upstream state without an LLM call.

States:

```
SOURCE_CURRENT  SOURCE_CHANGED  SOURCE_UNRESOLVED
DECISION_VALID  DECISION_STALE  DECISION_INVALIDATED  DECISION_UNRESOLVED
```

## Final Studio Dev deployment

```
Network:       GenLayer Studio Dev
RPC:           https://studio-dev.genlayer.com/api
Contract:      0xcdDBe68Ca04a43a50359668e654730CF328e8f03
Deploy tx:     0xfc37e89130a81ba5aacb1c43a27b87c541e755b134b17a96ac02104d9c1b4c84
Source SHA256: 2e58b15b9ae475f837f54eb1d4c82ea7f139a7911794489a80ab8cdfa24ff4c7
```

The authoritative contract source is [`contracts/faultline.py`](contracts/faultline.py). The public schema exposes 9 methods: 4 writes and 5 reads.

## Live proof

The final live proof is a three-node `SOURCE → B → C` chain:

```
Source:      faultline-fixed-source-1789912411
URL:         https://www.iana.org/help/example-domains
Claim:       IANA maintains example domains such as example.com and example.org for documentation purposes.
Check tx:    0x587b9e97ec6c2339707340e85ff0009cbcda49511861defb655d40c79ab0d03
Result:      NO_MATERIAL_CHANGE
State:       SOURCE_CURRENT

Decision B:  faultline-fixed-decision-b-1789912411
Recheck tx:  0x136da70e5b007ac168631412def3ee447d99bf5184117a7168e0b1f768f21c11
State:       DECISION_VALID / revision 1

Decision C:  faultline-fixed-decision-c-1789912411
Recheck tx:  0xf259da9a5f42a6963851f8763827d0e5e42a8426b2a00d8ef746ef025a753553
State:       DECISION_VALID / revision 1
```

This proof did **not** produce a live `MATERIAL_CHANGE`. The material-change cascade is covered deterministically by the test suite.

## Verification

The project has 28 passing tests covering validation, custom validator disagreement, cycle protection, revision snapshots, effective states, unresolved results, propagation, and the three-node demo flow.

```bash
pytest
genvm-lint check contracts/faultline.py
genvm-lint schema contracts/faultline.py --json
genvm-lint typecheck contracts/faultline.py --json
```

## Frontend

The frontend keeps the contract address and Studio Dev RPC in `frontend/src/main.js`.

```bash
cd frontend
npm install
npm run dev
npm run build
```

The frontend reads the live fixture, connects an EIP-1193 wallet for writes, preserves transaction handling, and includes the reviewer/live simulation route. Simulation is local-only and does not write on-chain.
