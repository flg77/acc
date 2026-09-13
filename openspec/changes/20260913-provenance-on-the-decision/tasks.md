# 20260913-provenance-on-the-decision — tasks

## Phase 1 (v0.17.6)

### 1.1 The row
- [ ] `OversightItem.requester` / `.ceiling`, defaulting empty
- [ ] `submit(..., requester=, ceiling=)`

### 1.2 Carrying it
- [ ] `dispatch_invocations(..., requester="")`
- [ ] `_dispatch_one` / `_gate_on_oversight` thread it through
- [ ] `acc/agent.py` passes `requester_of(data)`

### 1.3 Showing it
- [ ] `GateCard.requester` / `.ceiling` from the row
- [ ] `Decision` carries them; rendered above the options
- [ ] `unattributed` said plainly; nothing rendered when both are empty

### 1.4 Docs
- [ ] `docs/MANUAL.md`
- [ ] CHANGELOG entry

### Verification
- [ ] `tests/test_provenance_on_the_decision.py` green
- [ ] Existing oversight / panel / dispatch suites green
- [ ] Full sweep, classified against origin/main

## Phase 2 (deferred)
- [ ] The approver's own tier, before they decide
- [ ] UX-08 live state (the other half)
