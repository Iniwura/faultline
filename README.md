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
SOURCE_UNOBSERVED  SOURCE_CURRENT  SOURCE_CHANGED  SOURCE_UNRESOLVED
DECISION_VALID  DECISION_STALE  DECISION_INVALIDATED  DECISION_UNRESOLVED
```

## Final Studio Dev deployment

```
Network:       GenLayer Studio Dev
RPC:           https://studio-dev.genlayer.com/api
Contract:      0x5516Cd4ed18bAE5ADCA01908366c49bFC8612F00
Deploy tx:     0xd487058e82eae40b1567c8222dc0e06cc42920f47634dc1355632f11910f6c03
Source SHA256: 3f7d3969dc539f266635d18a1f4bdf716a372482bab32fce530730c2f05f655b
```

The authoritative contract source is [`contracts/faultline.py`](contracts/faultline.py). The public schema exposes 11 methods: 4 writes and 7 reads, including creator indexes for sources and decisions.

## Live proof

The final live proof is a three-node `SOURCE → B → C` chain:

```
Source:      faultline-fixed-source-1789912411
URL:         https://www.iana.org/help/example-domains
Claim:       IANA maintains example domains such as example.com and example.org for documentation purposes.
Baseline:    NO_MATERIAL_CHANGE
State:       SOURCE_CURRENT / revision 0

Decision B:  faultline-fixed-decision-b-1789912411
Depth:       1
Proof tx:    0x347b6fd885b0e4fe69a63a3bbfcaf91747ee247db8228b62c557b5bf754efe35
State:       DECISION_VALID / revision 1

Decision C:  faultline-fixed-decision-c-1789912411
Depth:       2
State:       DECISION_VALID / revision 1
```

The source was first registered as `SOURCE_UNOBSERVED`. Its first successful observation established the baseline deterministically as `NO_MATERIAL_CHANGE` without incrementing the revision.

The frontend owner-index path was exercised live against this deployment: it discovered the source and both decisions from the connected wallet, then read their canonical records back from the contract.

This proof did **not** produce a live `MATERIAL_CHANGE`. Deep-graph, broad-graph, admission-bound, unobserved-source, and material-change propagation behavior are covered deterministically by the executable test suite.

## Verification

The contract suite has 32 passing tests covering validation, validator disagreement, initial unobserved source state, cycle protection, revision snapshots, effective states, unresolved results, deep and broad dependency graphs, admission limits, propagation, creator indexes, and the three-node demo flow. The frontend record-refresh suite has 3 passing tests.

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
npm test
npm run dev
npm run build
```

The frontend reads the live fixture, connects an EIP-1193 wallet for writes, and refreshes user-created sources and decisions from the contract owner indexes instead of relying on synthetic local records. It preserves transaction handling and includes the reviewer/live simulation route. Simulation is local-only and does not write on-chain.
