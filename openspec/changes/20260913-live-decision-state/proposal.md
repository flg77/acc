# 20260913-live-decision-state — proposal

Backlog: vault `20-backlog/usability/UX-00` item **UX-08** (size S). The other
half of what the backlog calls "the two that make the panel stop lying";
**UX-09** shipped alongside it (#411).

## Why

Three things the item asked for. Two were real defects; the third turned out to
be already correct, and saying so is part of the work.

1. **The `1/2 → 2/2` change.** `OversightQueue.approve()` writes each approval
   to the **row**. The panel read `required_approvals` and `approvals` from the
   **proposal snapshot**, which does not move when a second approver signs — so
   a two-approver decision could sit at `PENDING 1/2` after the second approval
   had landed.
2. **The expiry countdown.** The row carries `timeout_ms`; the card dropped it
   and nothing rendered it. A gate that times out is *rejected* — the
   dispatcher stops waiting — so a deadline the panel does not show is a
   decision the operator can lose by reading slowly.
3. **The "N more waiting" count.** Already fed correctly: the destructive path
   passes `len(cards) - 1`, and the single-group path passes `0` because the
   panel takes that path only when there is exactly one group of one card.
   Nothing to fix; tests now pin it so it stays that way.

## What changes

* `GateCard` carries the row's own `required_approvals`, `approvals` and
  `timeout_ms`.
* `build_decision` prefers the **row** for approval state, keeping the proposal
  as the fallback for a decision that has no row state.
* `Decision.expires_at_ms`, and a pure `countdown()` rendering
  `expires in 4m00s` / `expires in 45s` / `expires in 2h46m`, and **`expired`**
  past the deadline rather than counting backwards.
* `render_panel(..., now_ms=None)` so the clock is injectable and the countdown
  is testable without freezing time.

## Impact

* **Affected code:** `acc/tui/gate_cards.py`, `acc/tui/acc_prompt.py`,
  `docs/MANUAL.md`, `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** `tests/test_live_decision_state.py` — the row reaching the card,
  the row beating a stale snapshot, the proposal still working as fallback,
  every countdown band, expired, no deadline, and the two "N more waiting"
  paths pinned.
* **Backward compatibility:** all three card fields default to today's values
  (1 approval, none given, no timeout), so a row without them renders exactly
  as before.

## What stays open after this

* The countdown is rendered when the panel repaints, not on a timer: it is
  correct whenever the pane redraws rather than ticking second by second.
* UX-09's deferred half — the approver's own tier shown before they decide.
