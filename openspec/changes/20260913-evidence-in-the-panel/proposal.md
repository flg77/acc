# 20260913-evidence-in-the-panel — proposal

Backlog: vault `20-backlog/usability/UX-00` item **UX-03**. The sequence there
is UX-02 → UX-04 → **UX-03**; both predecessors shipped (v0.17.0, v0.17.3).

## Why

The panel tells the operator that something is gated, why the category applies,
and what happens if they allow it. It does not tell them **what will actually
run**. Deciding therefore means trusting the summary rather than checking the
call — which is the gap UX-00 §2 records as "no evidence in the panel … so
deciding still means trusting rather than checking".

Worse, ACC already computes the evidence and then drops it. UX-02 added
`Question.evidence` — "what made the dispatcher ask (the matched command, the
declared flag)" — the dispatcher fills it, the oversight row carries it, and
the string `evidence` does not appear anywhere in `acc/tui/`. An operator is
asked to approve `rm -rf …` without being shown which path.

## What changes

### Phase 1 (this ship) — the evidence ACC already has, shown without running anything

1. **`call_evidence()`** beside the dispatcher renders the concrete call as
   operator-facing lines: what runs (`shell_exec {"cmd": "rm -rf build/"}`),
   what makes it destructive when it is (the matched command or the declared
   flag), and for an MCP call **where it goes** (the server's transport and
   URL — a gated call that reaches a remote host should say so).
2. **Secret-shaped values are masked** before they are rendered: a value whose
   key looks like a key / token / secret / password / credential is shown as
   `***`, and the whole block is length-capped. The panel is a screen an
   operator may be sharing; the journal already avoids this and the panel must
   not reintroduce it.
3. **The row carries it** — `OversightItem.evidence` — and it reaches the panel
   through the existing snapshot → `GateCard` → `Decision` path.
4. **The panel renders it** under the question, as `what this runs:` lines,
   before the options. The decision stays pending throughout: nothing here
   executes, so there is no question of doing work before approval.

### Phases 2–N (deferred, and one of them needs an operator decision)

* **Dry-run in place** and **diff preview** — UX-00 §5 question 3 is open:
  running an option to preview it means doing part of the work before approval.
  Which kinds is that acceptable for — read-only ones only, or anything with a
  real `--dry-run`? Phase 1 deliberately executes nothing, so it does not
  prejudge that answer.
* **Peek at a referenced object** (reading a file, fetching a row) — a read is
  still an action, so it belongs with the same decision.
* The journal line that *will* be written, and the gap-report row: UX-09 covers
  provenance, and this should land with it rather than duplicate it.

## Impact

* **Affected code:** `acc/capability_dispatch.py`, `acc/oversight.py`,
  `acc/tui/gate_cards.py`, `acc/tui/acc_prompt.py`, `docs/MANUAL.md`,
  `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** `tests/test_evidence_in_the_panel.py` — the rendered lines per
  kind, secret masking, truncation, the row round-trip, and the panel showing
  them above the options (and nothing when there is no evidence).
* **Backward compatibility:** `evidence` defaults empty; a row without it
  renders exactly as today.

## What stays open after Phase 1

* The operator sees the call, not its effect: `rm -rf build/` is shown, the
  list of files it would remove is not. That is the dry-run question above.
* Arguments are rendered from what the dispatcher parsed, so a marker whose
  arguments failed to parse shows the refusal, not a call.
* Evidence is per capability call; a proposal (INFUSE / SPAWN) keeps the
  rationale it already renders.
