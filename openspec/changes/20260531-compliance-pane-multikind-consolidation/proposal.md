# OpenSpec — Compliance pane multi-kind consolidation

| Field | Value |
|---|---|
| Change ID | `20260531-compliance-pane-multikind-consolidation` |
| Status | Proposed (Phase 1 ready to implement) |
| Closes followup | #39 |
| Affected proposals (each registers a kind) | `20260530-assistant-agent-of-agents` · `20260530-acc-self-improvement-policy-gradient` · `20260530-acc-dreaming-agent` · `20260531-orchestrator-repurpose-skills-mcp-specialist` |
| Brainstorm | `Notes/.../ACC-Compliance-Pane-Multikind/Compliance pane multi-kind consolidation — brainstorm.md` |

## Problem statement

The Compliance pane (PR-H + Z1a-c, v0.3.1) was designed for one
oversight shape: Cat-A HIGH_CONSEQUENCE approve/reject. Since then
the same pane has accumulated three more kinds without spec updates
— **AssistantProposal** (AoA-P2b), **POLICY_UPDATE** (SIP-P2), and
**Cat-C Rule Proposal** (PR-Z3d) — and is about to gain two more:
**DREAM_REPORT** (Dreamer Phase 1) and **CapabilityRecommendation**
(Orchestrator Phase 2). Six kinds, two axes (interactive ↔
informational; mutation ↔ audit), one undifferentiated list.

Operators today can't tell which items need approval, which are
read-only audit, or which kind they even are. Approval UX is
homogeneous when it should be kind-aware.

## Bootstrap defaults

1. **Open-set `kind` Literal** — new kinds land via a one-line
   addition; no schema migration per kind.
2. **Three sub-tabs under Oversight:** Approvals (interactive +
   mutating) · Audit (read-only history) · Diagnostics (read-only
   state snapshots).
3. **Default sub-tab: highest unread count** when the operator
   opens Compliance. Per-operator state in Redis.
4. **Kind-aware approval modals** in Phase 2 — shared shell, kind-
   specific body.
5. **Cat-A double-confirm preserved** for HIGH_CONSEQUENCE.

## Three invariants (table stakes — every phase)

1. **Queue is kind-blind.** `HumanOversightQueue` stores items
   with a `kind` field; producers tag their items; consumers
   filter. No per-kind queue split.
2. **Backward compat.** Untagged historical items are treated as
   `kind="human_oversight"` for Phase 1.
3. **Each new kind ships with its own proposal.** This proposal
   defines the discriminator + UX shell; new kinds register
   themselves without modifying this pane spec.

## Phase summary

| Phase | Status | Deliverable |
|---|---|---|
| 1 | Proposed | `kind` discriminator + 3 sub-tab shell + filter chips |
| 2 | Deferred | Kind-aware approval modals |
| 3 | Deferred | Audit tab polish (filterable POLICY_UPDATE; CSV export) |
| 4 | Deferred | Diagnostics tab polish (DREAM_REPORT detail; GAP_SCAN ride) |
| 5 | Deferred | Per-kind unread badges on the main Compliance tab + Soma |

## Phase 1 design (the discriminator + the shell)

### Schema change

`acc/oversight_queue.py::OversightItem` (Pydantic) gains:

```python
kind: Literal[
    "human_oversight",
    "assistant_proposal",
    "policy_update",
    "cat_c_rule_proposal",
    "dream_report",                # Dreamer Phase 1 ride
    "capability_recommendation",   # Orchestrator Phase 2 ride
] = "human_oversight"

interactive: bool = True
cat_a_high_consequence: bool = False
summary: str = ""
detail: dict = Field(default_factory=dict)
source_role: str = ""
target_role: str | None = None
target_id: str | None = None
operator_id: str = "default"
```

All fields default to backward-compatible values. The Literal is
**open-set in practice** — new proposals add their kind here as a
one-line addition.

### Sub-tab layout (TUI)

`acc/tui/screens/compliance.py` — the existing Oversight tab
gains a sub-tab strip:

```
[ Approvals (3) ] [ Audit (12) ] [ Diagnostics (7) ]
```

Each sub-tab carries a filter strip:

```
[ ALL ] [ Cat-A ] [ AssistantProposal ] [ Cat-C Rule ] [ Capability ]
```

(Filter chips are kind-bucketed; an "ALL" chip clears the filter.)

### Default-sub-tab logic

`acc/tui/state.py` — per-operator state:

```python
def default_compliance_subtab(items: list[OversightItem],
                              op_id: str, redis) -> str:
    pending_interactive = sum(1 for i in items if i.interactive
                              and not i.resolved
                              and i.operator_id == op_id)
    if pending_interactive >= 1:
        return "approvals"
    unread_audit = unread_count("audit", op_id, redis)
    if unread_audit >= 1:
        return "audit"
    return "diagnostics"
```

Redis keys: `acc:{cid}:compliance:tab:{operator_id}:last_visit`.

### Producer side (Phase 1 only tags; doesn't change UX yet)

Each existing producer publishes its kind:

| Producer | Kind |
|---|---|
| `acc/oversight_queue.py::publish_cat_a` | `human_oversight` |
| `acc/agent.py::_handle_assistant_proposals` | `assistant_proposal` |
| `acc/policy_layer.py::publish_policy_update` | `policy_update` |
| `acc/compliance/rule_proposals.py::publish_proposal` | `cat_c_rule_proposal` |

Future producers (Dreamer, Orchestrator) tag from day one.

### Tests

- `tests/test_oversight_kind_discriminator.py` — schema migration;
  untagged items treated as `human_oversight`.
- `tests/test_compliance_subtab_default.py` — default-sub-tab logic
  against fixture queues.
- `tests/test_compliance_filter_chips.py` — chip filtering shows the
  expected subset.
- `tests/test_compliance_kind_count_badges.py` — per-sub-tab counts.

### Phase 1 does NOT include

- Kind-aware approval modals (Phase 2).
- POLICY_UPDATE / DREAM_REPORT detail panes (Phase 3 / 4).
- Per-kind unread badges on the Soma agent cards or the main
  Compliance tab badge (Phase 5).
- Webgui parity (separate PR-W track).

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Schema migration breaks in-flight queue items | Defaults are backward-compatible; untagged → `human_oversight` |
| Tab thrash | Default-sub-tab decides on open; never auto-switches mid-session |
| Kind explosion | Open-set Literal; new kinds = 1-line addition |
| Modal UX inconsistency (Phase 2 risk) | `acc/tui/widgets/oversight_confirm_modal.py` shared shell |
| Audit tab unbounded growth | 64-entry cap on display; older rows archive to LanceDB |
| Per-operator leakage (post-AoA-P5b) | `operator_id` already on item; filter applies once TUI auth lands |

## Follow-on proposals (spawn after Phase 1)

- `20260601-compliance-kind-aware-approval-modals` (Phase 2)
- `20260601-compliance-audit-tab-polish` (Phase 3)
- `20260601-compliance-diagnostics-tab-polish` (Phase 4)
- `20260601-compliance-kind-aware-unread-badges` (Phase 5)
- `20260601-compliance-webgui-kind-discriminator-parity` (PR-W track)

## Linked

- Brainstorm: `Notes/.../ACC-Compliance-Pane-Multikind/Compliance pane multi-kind consolidation — brainstorm.md`
- Closes followup: `Notes/.../ACC Openspec/39 no orig Spec - Compliance pane new surfaces (AoA proposal queue + DREAM_REPORT + POLICY_UPDATE) - followup - 20260531.md`
- Historic origin: note 14 — `OpenSpec — Compliance- governance layers + oversight (PR-H, Z1a–c) (v0.3.1)`
- Affected proposals (each registers its kind):
  - `20260530-assistant-agent-of-agents` (AssistantProposal)
  - `20260530-acc-self-improvement-policy-gradient` (POLICY_UPDATE)
  - `20260530-acc-dreaming-agent` (DREAM_REPORT)
  - `20260531-orchestrator-repurpose-skills-mcp-specialist` (CapabilityRecommendation)
