# FAULTLINE state machine

## Sources

```text
register_source
      │
      ▼
SOURCE_CURRENT ── NO_MATERIAL_CHANGE ──▶ SOURCE_CURRENT
      │
      ├── MATERIAL_CHANGE ──▶ SOURCE_CHANGED ──┐
      │                                         │ reverse-edge cascade
      └── UNRESOLVED/failure ─▶ SOURCE_UNRESOLVED ─┘
```

Only `MATERIAL_CHANGE` increments the source revision. `UNRESOLVED` never pretends that a semantic change occurred.

## Decisions

```text
create_decision ─▶ DECISION_STALE
                         │
                         │ recheck: STILL_VALID
                         ▼
                   DECISION_VALID
                         │
                         ├── dependency changes/mismatch ─▶ effective STALE
                         │
                         ├── recheck: INVALIDATED
                         ▼
                 DECISION_INVALIDATED

recheck failure or UNRESOLVED ─▶ DECISION_UNRESOLVED
```

The persisted state and effective state are intentionally separate. A valid persisted record is not enough: its dependency revision snapshot must still match, and all upstream states must remain safe.

## Demo transition

```text
A: SOURCE_CURRENT
B: DECISION_VALID, depends on A
C: DECISION_VALID, depends on B

A check = MATERIAL_CHANGE
→ A: SOURCE_CHANGED, revision + 1
→ B: DECISION_STALE
→ C: DECISION_STALE

B recheck = INVALIDATED
→ B: DECISION_INVALIDATED, revision + 1
→ C: DECISION_STALE
```

An invalidated decision is a terminal conclusion for that decision revision, but it is still an unsafe upstream dependency for descendants.
