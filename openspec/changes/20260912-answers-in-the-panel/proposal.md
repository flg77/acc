# 20260912-answers-in-the-panel — proposal

Backlog: vault `20-backlog/usability/UX-00` item **UX-04** (the sequence there is
UX-02 → **UX-04** → UX-03). Follows `20260911-question-envelope` (UX-02) and the
acc-prompt panel (#381).

## Why

The panel exists so a decision can be answered where the operator already is.
`c` — *chat about this* — was the half that still sent them away:

* it prefills the input with the decision's title and **leaves the request
  PENDING** (`PromptScreen.on_acc_prompt_panel_chat_requested`), which is right;
* but the agent's reply lands in the **transcript**, under whatever else has
  arrived since, while the decision sits in the panel above it.

So the operator reads one surface to answer another, and the answer is separated
from the question that prompted it by every trace line, progress line and gate
card that arrived in between. UX-00 §2 row 20 records exactly this: *"the answer
lands in the transcript, not yet in the panel"*.

## What changes

### Phase 1 (this ship)

1. **`Decision.exchanges`** — the questions asked about this decision and the
   answers that came back, newest last; an empty answer is a question still in
   flight.
2. **The panel renders them under the question** (`_exchange_lines`): `asked: …`
   followed by the answer, or *waiting for the agent…* while it is out. The last
   **2** exchanges are shown and older ones counted (`… 3 earlier exchanges in
   the thread`); an answer longer than **6** wrapped lines is trimmed with *…
   the rest is in the thread*. The panel is a decision surface, not a chat
   window, and the transcript keeps every word.
3. **The wiring**: `c` marks the next send as the panel's
   (`_panel_chat_pending`); the dispatch records the question on the panel and
   the `task_id` it is waiting for; the reply for **that** task is routed back
   (`_panel_answer`) before it is appended to the thread — a continuation reply
   on a held thread counts too. Every other reply belongs to the thread alone.
4. **The thread follows the decision**: exchanges survive a snapshot refresh of
   the same request (like the note) and are dropped when a different decision
   takes the panel.

### Phases 2–N (deferred)

* Asking again from inside the panel (a key that opens an input there) rather
  than through the prompt box.
* The exchange kept with the oversight row, so a second operator sees what was
  asked — today it lives in the panel for this operator, this session.
* UX-03: evidence in the panel (diff preview, dry-run in place).
* Streaming the answer as it arrives instead of on completion.

## Impact

* **Affected code:** `acc/tui/acc_prompt.py`, `acc/tui/widgets/acc_prompt_panel.py`,
  `acc/tui/screens/prompt.py`, `docs/MANUAL.md`, `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** `tests/test_panel_answers.py` — 10: the default, a question in
  flight, the answer under its question, trimming, older exchanges counted, the
  widget's record, the reset rules, `c` marking the send, only the asked task
  answering, and an answer with no panel open.
* **Backward compatibility:** `exchanges` defaults to empty, so a panel that was
  never asked anything renders exactly as before; the transcript is unchanged.

## What stays open after Phase 1

* The exchange is per operator and per session — it is not on the row, so a
  second approver does not see it.
* An answer arriving after the decision was resolved is dropped (the panel is
  gone); the transcript still has it.
* The panel does not scroll: trimming is how a long answer is handled.
