# 20260913-preview-in-the-panel — proposal

Backlog: vault `20-backlog/usability/UX-00` item **UX-03 Phase 2**. Phase 1
shipped 2026-09-13 (#409): the panel shows the parsed call. This adds the
preview — what the call would actually do.

Gated on **UX-00 §5 question 3, answered by the operator 2026-09-13**: a
preview may run *anything with a real `--dry-run`* — the widest of the four
options offered.

## Why

Phase 1 shows `runs: shell_exec {"cmd": "rm -rf build/"}`. That is the call,
not its effect: which files, how many, whether the path even exists. For a
destructive decision that is the difference between reading a command and
knowing what it does.

## What changes

### The rule: declared, never inferred

A capability declares its own preview; ACC never derives one.

* `SkillManifest.preview_args` / `MCPManifest.preview_args` — arguments merged
  into the call to make it a dry run (`{"dry_run": true}`).
* Empty (the default) means **no preview**, and nothing runs.

This is the operator's caution made structural. Their answer permits executing
a real `--dry-run`; the risk they accepted is that *a dry-run flag is the
capability's claim, not ACC's guarantee*. Putting the claim in the manifest
means it is written down, reviewed, and — for a packaged capability — signed,
instead of ACC pattern-matching a command string and hoping.

**Shell commands are deliberately out of scope.** Rewriting an operator's
`git apply …` into `git apply --check` means guessing a third-party CLI's
flags, which is exactly the inference the caution warns against. An exec skill
gets a preview only if the skill itself accepts a dry-run argument.

### The mechanics

1. **At gate time**, before the row is submitted, a call whose manifest
   declares `preview_args` is dispatched **once** in its preview form, with its
   own timeout, and the result is added to the row's evidence as
   `preview: …` lines.
2. **It is journalled as its own act** — a `preview` tracelog entry naming the
   capability, the merged arguments and the outcome. A preview is an execution;
   the record says so.
3. **A failed preview is shown as a failure** (`preview failed: …`) and changes
   nothing else: it never resolves the decision, never pre-approves, never
   blocks the gate. The request is still asked exactly as before.
4. The preview reuses the existing adapter path but **does not re-enter the
   gate** — a preview that asked for approval would be a loop.

### Why at gate time rather than on an operator keypress

An operator-triggered preview (`p` in the panel) needs a request/response over
the bus, which D-022 deliberately avoided for the question envelope. Running it
at gate time costs one round trip that the operator is already waiting through,
needs no new signal, and puts the evidence in the first render of the panel.
The cost is honest and worth stating: **a declared preview runs whether or not
anyone reads it.** If that proves wrong, an operator-triggered form is a later
phase, and the declaration and journalling built here are what it would reuse.

## Impact

* **Affected code:** `acc/skills/manifest.py`, `acc/mcp/manifest.py`,
  `acc/capability_dispatch.py`, `docs/MANUAL.md`, `docs/howto-mcp-sources.md`,
  `CHANGELOG.md`.
* **New env knobs:** `ACC_PREVIEW_TIMEOUT_S` (default 10) — a preview must not
  wedge the gate.
* **Tests:** `tests/test_preview_in_the_panel.py` — the merge, declared-only,
  the timeout, failure rendered as failure, no gate re-entry, the journal
  entry, and that a capability without a declaration behaves exactly as today.
* **Backward compatibility:** `preview_args` defaults empty, so no existing
  capability previews anything and every gate behaves as it does today.

## What stays open after this

* No preview for a shell command, per the scope above.
* The preview runs unconditionally for a declared capability; an
  operator-triggered form is a later phase.
* The output is rendered as text; a real diff view (side-by-side, syntax) is
  the panel's next question, not this one.
