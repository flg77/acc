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

## Rule proposals (close the gaps)

Findings never edit enforced policy directly (that would bypass the
arbiter-signed bundle and could touch immutable Cat-A). Instead they
become **RuleProposals** (`acc/rule_proposals.py`, Cat-B/C only) shown
in the **Rule Proposals** collapsible:

- Sources: `gap` (from a gap scan), `violation` (learn-from-violations,
  `acc/violation_learning.py`), `self_challenge`.
- Press **`p`** to focus; **Approve** writes the proposal to the
  pending-proposals overlay (`proposed_rules.jsonl`) the arbiter ICL
  pipeline consolidates + signs into a Cat-C bundle; **Reject** drops it.
- The **`learned_rule_promotion`** Cat-B setpoint
  (`data_rhoai.json`, default `propose`) chooses the policy:
  - `propose` — proposals wait for operator Approve (human-in-the-loop).
  - `auto` — proposals auto-approve straight into the overlay.
  Override per-run with `ACC_LEARNED_RULE_PROMOTION`.

**Cat-A is never machine-edited** — proposals are validated to be
Cat-B/C only.

## Self-challenge (red-team Cat-A)

The **Self-challenge Cat-A** button (proposals area) runs
`acc/self_challenge.py`: per Cat-A rule it generates a literal-vs-intent
adversarial scenario + likelihood + a Cat-B/C mitigation, writes an
audit doc, and emits mitigation proposals for HIGH/MEDIUM findings.

## Agent-driven (LLM) analysis

The deterministic gap scan + self-challenge are lexical first-passes.
For semantic depth, dispatch a task to the **`compliance_officer`** role
(extended with `COMPLIANCE_GAP_SCAN` / `SELF_CHALLENGE` /
`LEARNED_RULE_PROPOSE` task types) from the **Prompt** pane — the
agent's LLM produces the rich mapping + refined proposed rules
(`gap_analysis.build_gap_prompt` / `self_challenge.build_challenge_prompt`
are the prompt builders). Its `workspace_access` lets it write audit
docs to the trusted workspace.

## Scheduled scans

`acc/compliance_scan.py` runs gap analysis for every framework + a
Cat-A self-challenge and emits proposals — on demand or on a loop:

```bash
python -m acc.compliance_scan                 # one-shot (prints JSON summary)
python -m acc.compliance_scan --loop 86400    # daily
```

Schedule it like the golden-prompt runner (see
`docs/golden_prompts_scheduling.md`):

- **systemd timer** — a `compliance-scan.service` + `.timer` unit.
- **k8s CronJob** — `schedule: "0 3 * * *"` running the same module.

Scheduled findings land in the same stores, so they appear in the Rule
Proposals table for review (or auto-activate when the setpoint is
`auto`).
