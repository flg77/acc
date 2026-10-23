# 20261015-ask-once-p2-answer-anywhere — proposal

**058 Phase 2: answer from anywhere, and say where the work is heading.**
The operator's statement of 2026-10-15: "The operator should respond from
within to cmdline / prompt to permission requests and always have the right
guidance of the assistant on where the system progress is moving to."

## What changes

- **WebGUI → Work → Decisions.** It shows the arbiter's pending rows. A row
  that asks a question shows its options as buttons, so the mission card is
  answered as in the TUI. `POST /api/oversight` accepts `answer`, and the
  queue still refuses an option that does not fit the decision. The page
  also lists the missions; close, revoke, purge and restore go through a new
  `POST /api/mission`, which publishes `MISSION_CONTROL` and requires the
  operator role. A viewer sees everything and no buttons.
- **`acc-cli ask "<task>"`** submits through the TUI's prompt channel,
  streams progress, and prints each question of *this* task with its
  options. It reads the answer from the terminal and publishes the same
  OVERSIGHT_DECISION under the operator's identity (`cli:` prefix). It ends
  with the answer, the heading and the ask count. `--no-answer` leaves the
  questions waiting.
- **The heading** (`acc/heading.py`) on every `TASK_COMPLETE`: `done`,
  `refused`, `waiting_on_you`, `mission`, and `next`, the agent's own
  "Next: …" line. The TUI prints it under the answer (`↳`), the WebGUI under
  the turn, and `acc-cli ask` at the end.
- `InvocationOutcome.mission_id`: the mission that answered or was created
  by a call.
- The WebGUI Prompt page defaults to ASK_ONCE when it sends.

## Not in this change

- A heading *during* the task, beyond the existing per-tool `TASK_PROGRESS`.
  The durable plan stream (PA-06) is still needed for that.
