# Tasks — `20260531-compliance-pane-multikind-consolidation`

## Phase 1 — kind discriminator + sub-tab shell (additive)

- [ ] `acc/oversight_queue.py` — `OversightItem` Pydantic model
      gains `kind`, `interactive`, `cat_a_high_consequence`,
      `summary`, `detail`, `source_role`, `target_role`, `target_id`,
      `operator_id` fields (all backward-compatible defaults).
- [ ] Producers tag their items:
  - [ ] `oversight_queue.publish_cat_a` → `human_oversight`
  - [ ] `agent._handle_assistant_proposals` → `assistant_proposal`
  - [ ] `policy_layer.publish_policy_update` → `policy_update`
  - [ ] `compliance.rule_proposals.publish_proposal` →
        `cat_c_rule_proposal`
- [ ] `acc/tui/screens/compliance.py` — Oversight tab gains a
      three-sub-tab strip (Approvals / Audit / Diagnostics) with
      filter chip strip per sub-tab.
- [ ] `acc/tui/state.py` — `default_compliance_subtab(items, op_id,
      redis)` decision function.
- [ ] Redis state keys: `acc:{cid}:compliance:tab:{operator_id}:
      last_visit`.
- [ ] `docs/COMPLIANCE_PANE.md` — operator narrative + screenshots
      via `acc-tui-docs`.
- [ ] Tests:
  - `tests/test_oversight_kind_discriminator.py` — schema
    migration; defaults; back-compat with untagged.
  - `tests/test_compliance_subtab_default.py` — default sub-tab
    against fixture queues.
  - `tests/test_compliance_filter_chips.py` — chip filtering.
  - `tests/test_compliance_kind_count_badges.py` — per-sub-tab
    counts.

## Phase 2 — kind-aware approval modals — DEFERRED

- [ ] `acc/tui/widgets/oversight_confirm_modal.py` — shared shell.
- [ ] Cat-A HIGH_CONSEQUENCE: double-confirm (preserved).
- [ ] AssistantProposal: show `[PROPOSE_*]` marker + dispatcher
      target.
- [ ] Cat-C Rule Proposal: rule overlay diff.
- [ ] CapabilityRecommendation: skill/MCP delta + LLM rationale.

## Phase 3 — Audit tab polish — DEFERRED

- [ ] POLICY_UPDATE detail pane (old/new θ + composite reward +
      tasks_in_window).
- [ ] Filter by role + knob + date range.
- [ ] CSV export.

## Phase 4 — Diagnostics tab polish — DEFERRED

- [ ] DREAM_REPORT detail pane (centroid drift + poisoning + convergent
      workflows + preference hints).
- [ ] GAP_SCAN (Orchestrator Phase 3) ride.

## Phase 5 — per-kind unread badges — DEFERRED

- [ ] Main Compliance tab badge breaks down by kind:
      `Compliance (3 approvals · 1 diagnostic)`.
- [ ] Soma agent cards already have 💤 badge pattern (AoA-P1); add
      a small `n approvals pending` badge.

## Follow-on proposals to spawn after Phase 1

- [ ] `20260601-compliance-kind-aware-approval-modals`
- [ ] `20260601-compliance-audit-tab-polish`
- [ ] `20260601-compliance-diagnostics-tab-polish`
- [ ] `20260601-compliance-kind-aware-unread-badges`
- [ ] `20260601-compliance-webgui-kind-discriminator-parity`
