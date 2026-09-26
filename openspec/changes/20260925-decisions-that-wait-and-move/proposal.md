# 20260925-decisions-that-wait-and-move — proposal

Backlog: vault `20-backlog/usability/UX-00`, items **UX-05** (deferral), **UX-06**
(delegation), **UX-10** (screen-reader order, copy) — lane G3 of the 2026-09-25
priority list. **UX-07** (secret input) is deliberately *not* here: a secret typed
into the panel has to reach a tool without crossing the bus, the transcript or the
model, and that needs the secret source and the live broker of F3. It lands with
F3.

## Why

The decision panel (UX-01) made one decision answerable where the operator already
is. Three things still send them elsewhere, or lose the decision:

1. **"Not now" is the same key as "never mind".** `Esc` leaves a decision pending
   with no promise it comes back, and a gate the dispatcher waits on is rejected
   when its deadline passes. An operator who needs twenty minutes, or an answer to
   the question they just asked about the decision, has no way to say so.
2. **The right person is not always the one looking.** A decision can only be
   answered by whoever sees it; there is no way to hand it to a colleague or a tier.
3. **The panel reads badly to a screen reader and cannot copy.** Side by side, the
   options and the detail box interleave line by line; the id and the command have
   to be retyped.

## Two defects found on the way, fixed first

Mapping the panel's inputs for this change found that two shipped features do not
work on the live path:

* **The heartbeat drops four fields the panel reads.** `oversight_pending_items`
  (`acc/agent.py`) carries no `evidence`, `requester`, `ceiling` or `timeout_ms`,
  so over the real arbiter heartbeat UX-03 (what the call runs), UX-09 (for whom)
  and UX-08 (the deadline) are always empty. Their tests fed rows in directly.
* **The deadline is read as a duration.** The queue stores `timeout_ms` as an
  **absolute** epoch time (`now_ms + timeout_s * 1000`); the panel added it to
  `submitted_at_ms`, which would put the deadline decades out once the field
  arrives.

A third finding is recorded, not changed: **`expire_timed_out()` has no caller.**
Only rows the dispatcher waits on actually expire (it stops waiting and expires
them); proposal and submitted rows are never expired and drop out of Redis at twice
the timeout. The panel therefore shows a countdown **only where the deadline is
enforced** — a capability gate or a question row — rather than a deadline that
would lie.

## What changes

### The fixes

* The heartbeat carries `evidence` (capped: 8 lines of 240 characters),
  `requester`, `ceiling`, `timeout_ms` and the row's `delegations`.
* `Decision.expires_at_ms` is the row's absolute `timeout_ms`, and only for a card
  whose deadline the dispatcher enforces (`GateCard.deadline_enforced`).
* `OversightQueue._load` ignores keys it does not know, so a row written by a newer
  agent never makes an older one treat it as missing.

### UX-05 — a deferral that keeps its promise

* **`d`** in the panel: *ask again in 5 min · 15 min · 1 hour*, or *when the agent
  answers my question* (offered only while one is in flight).
* **The promise:** a deferred decision comes back **before it can be lost** — at the
  requested time or one minute before an enforced deadline, whichever is first, and
  the panel says which. Within a minute of the deadline it refuses to defer.
* A deferral is withheld from the panel and the region until due, then comes back
  with a transcript line and focus. `/deferred` lists them; `/deferred now` brings
  them all back. A deferred row decided elsewhere is dropped.
* **"Stop asking me for this class"** is an **option**, not a deferral, because it
  approves: *allow this class for 30 min*, offered on a category gate that is
  LOW or MEDIUM, single-approver and not destructive. The class is
  `(category, risk, requester)`, so a snooze given on one person's task never
  answers another's. It expires after 30 minutes and never outlives the TUI
  session; each approval it makes is a real OVERSIGHT_DECISION carrying the reason
  `snoozed: …`. `/snooze` lists them, `/snooze off` ends them. Never a permanent
  widening: nothing is written to a role, a policy or the session file.

### UX-06 — hand a decision to someone else

* **`h`** in the panel: type a person (`webgui:alice`, `slack:U1`) or a tier
  (`operator`), optionally a note. Published as `OVERSIGHT_DECISION` with
  `decision: "DELEGATE"` and `delegate_to` — **no new subject, no new NKey grant**.
* `OversightQueue.delegate()` records `{by, by_tier, to, ts_ms, note}` on the row.
  The row **stays PENDING**; only an operator-tier person may delegate; every agent
  applies the same decision, so the record is idempotent on `(by, to, ts_ms)`.
* **Visibility, never authority.** The delegator's panel stops raising it
  (`/delegated` lists it); the delegate's panel shows *delegated to you by …*. Anyone
  who could decide it before still can — delegation does not narrow who may approve.
* Person matching uses `acc.attribution.person_of`, so the person map (HG-40.1 Q9,
  lane B2) extends it across surfaces without a change here.
* `acc-cli oversight delegate <id> --to <person|tier> [--note]`.

### UX-10 — screen-reader order and copy

* **`ACC_PROMPT_LINEAR=1`** renders the panel in reading order: title, question,
  state, provenance, evidence, options, then the highlighted option's detail under a
  plain `Details:` label instead of a box beside the options.
* **`y`** copies the decision id, **`Y`** the command it runs (the `runs:` evidence
  line), through the terminal clipboard (OSC 52) and an in-app buffer.

## Impact

* **Affected code:** `acc/oversight.py`, `acc/agent.py`, `acc/signals.py`,
  `acc/tui/gate_cards.py`, `acc/tui/acc_prompt.py`, `acc/tui/decision_timing.py`
  (new), `acc/tui/clipboard.py` (new), `acc/tui/widgets/acc_prompt_panel.py`,
  `acc/tui/widgets/permission_request.py`, `acc/tui/screens/prompt.py`,
  `acc/tui/screens/compliance.py` (`_OversightAction.delegate_to`), `acc/tui/app.py`,
  `acc/slash_commands.py`, `acc/cli/oversight_cmd.py`.
* **New env knob:** `ACC_PROMPT_LINEAR` (default off).
* **Wire:** one new `decision` value on the existing subject. An older agent logs
  `unknown decision 'DELEGATE'` and ignores it; nothing it would have done changes.
* **Backward compatibility:** the heartbeat gains optional fields; rows gain an
  optional `delegations` list.

## What stays open

* UX-07 secret input — with F3.
* Delegation and deferral on the web GUI (KW-10 parity).
* Wiring `expire_timed_out()`: a behaviour change for every proposal row, worth its
  own decision.
* `approver_tier` is still taken from the payload unverified; delegation inherits
  that, as approval already does.
