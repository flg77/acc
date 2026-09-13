# 20260913-evidence-in-the-panel — tasks

## Phase 1 (v0.17.6)

### 1.1 The evidence
- [ ] `call_evidence(kind, target, args, manifest)` -> tuple of lines
- [ ] Mask secret-shaped argument values; cap the block
- [ ] Destructive evidence folded in when present

### 1.2 Carrying it
- [ ] `OversightItem.evidence: list[str]`, in `to_dict` / `from_dict`
- [ ] `submit(..., evidence=...)`, passed by `_gate_on_oversight`

### 1.3 Showing it
- [ ] `GateCard.evidence`, read from the snapshot row
- [ ] `Decision.evidence`, rendered above the options
- [ ] Nothing rendered when there is none

### 1.4 Docs
- [ ] `docs/MANUAL.md` — what the panel shows
- [ ] CHANGELOG entry

### Verification
- [ ] `tests/test_evidence_in_the_panel.py` green
- [ ] Existing oversight / panel / dispatch suites green
- [ ] Full sweep, classified against origin/main
- [ ] lighthouse: a destructive request shows its command in the panel

## Phase 2 (deferred — needs UX-00 §5 Q3 answered)
- [ ] Dry-run in place / diff preview, for whichever kinds the operator allows
- [ ] Peek at a referenced object
