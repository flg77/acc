# 20260913-provenance-on-the-decision — proposal

Backlog: vault `20-backlog/usability/UX-00` item **UX-09** (size S). The
sequence there puts UX-08 / UX-09 after UX-03, as the two that "make the panel
stop lying".

## Why

The panel says what is being asked and, since UX-03, what will run. It does not
say **whose work this is** or **under whose authority it runs**.

ACC knows both and has for two releases. D-014 stamps a `requester_ceiling` on
the task and `capability_dispatch` checks it — refusing outright above the
ceiling. `acc.attribution.requester_of` names the person a task was admitted
for, and v0.14.2 made every shared surface honour it. The oversight row carries
neither: it has `role_id` and `agent_id` — the *agent*, never the person.

So an operator approving a HIGH-risk call cannot see from the panel whether
they are approving their own work or a request admitted for someone else, nor
which ceiling let it get this far. On a shared collective that is the
difference between "I asked for this" and "someone else did, and I am the one
signing".

## What changes

### Phase 1 (this ship)

1. **The row carries the provenance**: `OversightItem.requester` and
   `OversightItem.ceiling` — the person the task was admitted for and the
   ceiling in force, both already computed at dispatch.
2. **The dispatcher passes them.** `dispatch_invocations` gains `requester`
   alongside the `requester_ceiling` it already takes, and the agent supplies
   `requester_of(data)` beside the `ceiling_of(data)` it already passes.
3. **The panel renders one line** above the options, beside the evidence:
   `for <requester> · ceiling <LEVEL>`, and `unattributed` said plainly when a
   task never passed admission rather than left blank.
4. **What will be recorded** is named in the same block: the decision, its
   answer and the approver land on this row and in the session trace. This is
   deliberately a statement of the *fields* that will be written, not a
   mocked-up log line — a preview of a format is a thing that drifts out of
   date silently.

### Phases 2–N (deferred)

* The approver's own tier shown before they decide (it is checked at
  `approve()`, so the panel could say "you are operator tier" or warn that a
  second approver is needed from someone who is not).
* UX-08's live state — a `1/2 → 2/2` change and an expiry countdown — which is
  the other half of "the panel stops lying".

## Impact

* **Affected code:** `acc/oversight.py`, `acc/capability_dispatch.py`,
  `acc/agent.py`, `acc/tui/gate_cards.py`, `acc/tui/acc_prompt.py`,
  `docs/MANUAL.md`, `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** `tests/test_provenance_on_the_decision.py` — the row fields, the
  dispatcher passing them, unattributed said plainly, the panel line and its
  placement, and a row without provenance rendering as before.
* **Backward compatibility:** both fields default empty; a row without them
  renders exactly as today.

## What stays open after Phase 1

* The panel shows the requester as the identity string the surface stamped;
  mapping that to a person's display name is the HG-40.1 person map, which
  UX-06 is already blocked on.
* Nothing here changes *enforcement* — the ceiling was always checked at
  dispatch. This makes the check visible, not stricter.
