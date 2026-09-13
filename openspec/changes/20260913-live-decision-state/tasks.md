# 20260913-live-decision-state — tasks

## Phase 1 (v0.17.6)
- [x] `GateCard.required_approvals` / `.approvals` / `.timeout_ms` from the row
- [x] `build_decision` prefers the row, proposal as fallback
- [x] `Decision.expires_at_ms` + pure `countdown()`
- [x] `render_panel(now_ms=)` for a testable clock
- [x] Pin the two "N more waiting" paths
- [x] MANUAL + CHANGELOG

### Verification
- [x] `tests/test_live_decision_state.py` green (19)
- [x] Panel / oversight / question suites green (819)
- [ ] Full sweep, classified against origin/main
