# Compliance & Governance pane

The TUI **Compliance** pane (`3 Compliance`) shows the live governance
posture and lets an operator browse what's loaded, measure it against
enterprise frameworks, and act on the human-oversight queue.

## Layout

```
┌─ OWASP LLM TOP 10 GRADING ─┐ ┌─ HUMAN OVERSIGHT QUEUE ──────────┐
│ LLM01..LLM10 · grade · %   │ │ selectable table (o to focus)    │
├─ COMPLIANCE HEALTH ────────┤ │ ↑/↓ move · a=approve · r=reject  │
│ score bar                  │ ├─ PENDING ITEM DETAIL ────────────┤
├─ GOVERNANCE LAYERS ────────┤ │ gate reason + approve/reject prev │
│ ▸ Cat A (immutable 🔒)     │ ├─ OWASP VIOLATION LOG ────────────┤
│ ▸ Cat B (setpoints)        │ │ last 50                          │
│ ▸ Cat C (learned)          │ └──────────────────────────────────┘
│ ▸ Frameworks               │
└────────────────────────────┘
```

The OWASP grading, health bar, and violation log are unchanged.

## Governance layers (browse what's loaded)

The left-bottom **GOVERNANCE LAYERS** section has three collapsibles —
**Cat A / Cat B / Cat C** — populated by `acc/governance_inventory.py`
from `regulatory_layer/category_{a,b,c}/*.rego` (+ `data_rhoai.json`):

- Each title shows the layer **version** + **rule count**; Cat-A is
  marked 🔒 (immutable — compiled to WASM at build time).
- Each holds a `rule_id | summary` table parsed from the rule
  annotations (`# A-001: …`, Cat-C `# Context:` lines).
- Press **`g`** to focus the governance area; **Enter** on a rule opens
  the source policy file in a **read-only viewer** (line-numbered,
  highlights the rule's line).

This is display-only — it never edits policy. Cat-A is immutable; Cat-B/C
change only through the signed `RULE_UPDATE` path.

## Frameworks + gap analysis

The **Frameworks** collapsible lists control catalogs to measure ACC
against (`acc/frameworks.py`):

- **Built-in** (shipped, read-only): NIST AI RMF, EU AI Act high-risk
  obligations, ISO/IEC 42001, SOC 2 — `regulatory_layer/frameworks/*.yaml`.
- **Imported**: enterprise / custom catalogs (e.g. German **BSI**) the
  operator adds. Drop the catalog YAML under your home (the `/host-home`
  read-only mount), type its path in the input, and press **`+ Add`** —
  it's validated and copied into the writable store
  (`acc-frameworks-data`).

Catalog schema:

```yaml
framework_id: bsi_c5
name: "BSI C5"
version: "2020"
source: "BSI C5:2020"
controls:
  - control_id: OPS-01
    title: "Capacity planning"
    description: "..."
    category: OPS
```

**Run gap scan** (highlight a framework → button) runs
`acc/gap_analysis.py` against the loaded Cat-A/B/C rules:

- maps each control to governance rules by shared domain terminology,
- marks each **covered** (with the matched rule ids + shared terms) or a
  **gap** (with a category-driven severity + a proposed-rule stub),
- writes a JSON + markdown **audit document** to the reports store
  (`acc-compliance-data`) capturing the full per-control reasoning, and
- opens the markdown report in the viewer; the framework's coverage %
  is cached in the table.

> The deterministic scan is a **conservative lexical first-pass**.
> ACC's constitutional rules use biological-metaphor language, so lexical
> coverage scores low — the agent-driven (LLM) gap analysis (Phase 3,
> via `gap_analysis.build_gap_prompt` + the `compliance_officer` role)
> does the semantic mapping and refines the proposed enforceable rules.

## Human oversight queue

A focusable, row-cursor table (like the Ecosystem role table):

- **`o`** focuses it; **↑/↓** move through pending items.
- **`a`** approves / **`r`** rejects the **highlighted** item
  individually; the detail panel tracks the cursor.
- HIGH_CONSEQUENCE approvals (risk HIGH/CRITICAL/UNACCEPTABLE or a
  dangerous gate-reason marker) pop a confirmation modal first; Reject
  is never gated.

Items arrive from the arbiter HEARTBEAT (`oversight_pending_items`);
decisions publish `OVERSIGHT_DECISION` on NATS.

## Container wiring

`acc-tui` mounts (see `container/production/podman-compose.yml`):

- `regulatory_layer:/app/regulatory_layer:ro` — the governance + built-in
  framework files (browse).
- `acc-frameworks-data:/app/.acc-frameworks:U,z` — imported catalogs.
- `acc-compliance-data:/app/.acc-compliance:U,z` — gap-analysis reports.

Env: `ACC_REGULATORY_ROOT`, `ACC_FRAMEWORKS_IMPORT_ROOT`,
`ACC_COMPLIANCE_REPORTS_ROOT` (overridable; sensible in-container
defaults).

## Tests

```
pytest tests/test_governance_inventory.py \
       tests/test_frameworks.py \
       tests/test_gap_analysis.py \
       tests/test_compliance_pane_governance.py \
       tests/test_compliance_oversight_table.py \
       tests/test_compliance_frameworks_pane.py \
       tests/test_compliance_pane_detail.py -v
```

## Not yet (Phase 3)

- Agent-driven (LLM) gap analysis + `self_challenge` red-team skill via
  an extended `compliance_officer` role.
- Generating enforceable Cat-B/C rules from gaps (operator-selectable
  *propose-pending-approval* vs *auto-activate*) through the signed
  `RULE_UPDATE` path.
- Learn-from-violations → proposed Cat-C rules; scheduled gap-scan loop.
