# 20260903-work-board-tui — proposal

## Why

HG-39 (vault, `20-backlog/Hermes Gap/`) asked for a "kanban / multi-agent
task board" and rated it ADAPT / P3 against the PLAN DAG executor, with the
open question *is this a new feature or a view over PLAN state that already
exists?* The 2026-09-03 evaluation (vault: `HG-39 — Work board — challenge and
design`) answers: mostly a view — with two defects underneath and one join
nobody made.

* **The TUI never advances plan progress.** `PlanExecutor._broadcast`
  re-publishes the PLAN payload with `step_progress` on every transition
  (`acc/plan.py:953-966`). `NATSObserver._route_plan` initialises every step
  to PENDING and, on a re-broadcast, "updates steps but preserves progress"
  (`acc/tui/client.py:857-877`) — it never reads `step_progress`. The Comms
  ACTIVE PLAN DAG has been permanently PENDING. Same class as #321 and the
  Diagnostics fan-out (`20260902-tui-profiles` 1a): state produced, never
  consumed.
* **The executor has no intervention.** `acc/plan.py` drives PENDING →
  RUNNING → COMPLETE / FAILED and cascades failure; there is no cancel, retry
  or reassign, and it does not react to `TASK_CANCEL` — the Prompt pane's
  `/cancel` (`prompt.py:2005`) publishes one that only the *agent* honours.
  "Block, reassign or cancel" — HG-39's own statement of the value — does not
  exist at the executor level.
* **Blocked is never joined.** A plan step whose task is waiting on an
  oversight row is Blocked in every practical sense; nothing relates
  `active_plans[*].steps[*].task_id` to `oversight_pending_items[*].task_id`.
  Since 0.11.0 the gate is answered in the Prompt pane's request region — the
  board should point there, not re-implement it.

**The frame.** Hermes' kanban lets humans *and agents* create and claim work.
ACC has the creation side (PLAN, clusters, assistant proposals) and refuses
self-claiming agents on purpose (`agency_limiter`, the AgentBOM). So a work
board here is **a projection of state the runtime owns, plus the interventions
a human is entitled to** — and **nobody drags a card to Done**: the runtime
moves cards; a human cancels, retries, reassigns, or answers the gate. A
human-movable Done would let a person fake completion of work that Cat-A/B/C
and the reviewer loop attest to.

One projection, two renderers: the TUI (this change) and the WebGUI
(`20260903-work-board-webgui`, which depends on the core landed here).

## What changes

### Phase 1 (this ship — v0.11.x)

**A. Fix the progress bug.** `_route_plan` applies `step_progress` from
every re-broadcast (and learns `CANCELLED`). Test: a re-broadcast with
`s1: RUNNING` is reflected; the Comms DAG renders it.

**B. The unit of work — `acc/work_board.py` (pure, shared).**
`WorkItem(id, kind, title, role, agent_id, status, parent, task_id,
blocked_on, iteration, critique, updated_ts, output_head)`; five columns
`QUEUED · RUNNING · BLOCKED · DONE · FAILED` (cancelled folds into FAILED with
`status_detail="cancelled"`). `project_board(active_plans, cluster_topology,
oversight_pending_items, oversight_recent_items, signal_flow_log)` over
plain dicts (the WebGUI hands it the serialised snapshot; the TUI adapts its
dataclasses), so both surfaces compute the same cards:

| source | item | note |
|---|---|---|
| `active_plans[*].steps` + `step_progress` | plan step | role, description, `depends_on`, `iteration_n/max`, `last_critique` where the payload carries them |
| `cluster_topology[*].members` | fan-out member | parent = the cluster; progress from TASK_PROGRESS |
| `signal_flow_log` TASK_ASSIGN / TASK_COMPLETE | single prompt task | the common lighthouse case — no PLAN at all; the log entry gains `task_id` + `target_role` (additive) |
| `oversight_pending_items` joined on `task_id` | → `BLOCKED`, `blocked_on = oversight_id` | the join the runtime never made |
| `oversight_recent_items`, `assistant_outcomes` | outcome line on the item | AUTO_APPROVED, installed, unmet |

**C. Intervention in the executor — `PLAN_STEP_CONTROL`.** A new signal
(`SIG_PLAN_STEP_CONTROL`, subject `acc.<cid>.plan.control`, SYNAPTIC — it is
addressed to the arbiter), payload `{plan_id, step_id, action:
cancel|retry|reassign, role?, actor, reason?}`. `PlanExecutor.on_step_control`:
* `cancel` — step → `CANCELLED`; if RUNNING, publish `TASK_CANCEL` for its
  task id (the agent's cancel is best-effort today); dependents cascade as
  skipped, exactly like failure; re-broadcast.
* `retry` — a FAILED / CANCELLED step (and the dependents that were only
  skipped — `task_id == ""`) back to PENDING, then `_dispatch_ready_steps`.
* `reassign` — `retry` with `step.role` replaced by `role`.
Idempotent: a second cancel of a cancelled step is a no-op; retry of a
COMPLETE step is refused with a warning. The arbiter subscribes next to the
PLAN submit subject. Every control is journalled through the existing plan
re-broadcast, and an `actor` is on the payload so the WebGUI can attribute
it (D-011's open item — the TUI stays `tui:anonymous`).

**D. The `Board` screen (TUI).** Registered in `acc/tui/registry.py` as an
overflow pane (`Ctrl+A` digit; the 1–9 keys are full) on **both** profiles —
"what is my task doing" is a user question, so the `user` strip becomes
Prompt · Board · Compliance. A **swimlane list**, not five columns: one
DataTable with a header row per column and one row per item — terminal width
does not afford a real kanban, and density beats layout. Keys on the
highlighted row: `c` cancel · `r` retry · `a` reassign (role picked from the
roster in the which-key modal) · `g` go to the gate (Prompt pane, request
region focused) · `Enter` detail (description, critique, output head,
outcome). Actions publish `PLAN_STEP_CONTROL` / `TASK_CANCEL` through the
app's existing `_PublishMessage`; the board holds no state of its own.

**E. Docs.** `acc/tui/help/board.md`; `docs/howto-tui.md` screen reference;
CHANGELOG **Added** + **Fixed** (the progress bug).

### Phase 2 (deferred)

* **Durability.** The arbiter journals step transitions to the tracelog
  (`KIND_PLAN_STEP`) and mirrors plan state in Redis the way clusters do
  (`acc/cluster.py:configure_redis_mirror`), 24 h TTL; the heartbeat carries a
  plan summary so a restarted TUI / WebGUI cold-starts the board instead of
  waiting for the next PLAN broadcast. This is DS-09's "durable, inspectable
  work-tracking layer".
* **Blocked → answered, in place.** `g` today jumps to the Prompt pane; a
  compact request region on the Board itself is Phase 2 once
  `PermissionRequest` is reusable outside Prompt.

### Declined

Boards as a thing you create; `specify` / `decompose`; agent self-claim; a
human-movable Done; a review state separate from the critic loop; comments
separate from the task's Prompt thread.

## Impact

* **Affected code:** `acc/tui/client.py` (`_route_plan`, log entry fields),
  `acc/work_board.py` (new), `acc/signals.py`, `acc/plan.py`
  (`on_step_control`, `STATUS_CANCELLED`), `acc/agent.py` (subscription),
  `acc/tui/screens/board.py` (new), `acc/tui/registry.py`,
  `acc/tui/help/board.md`, `docs/howto-tui.md`, CHANGELOG.
* **New env knobs:** none.
* **Tests:** ~16. `_route_plan` applies progress; projection (each source,
  the Blocked join, column order, cancelled folding, missing fields degrade);
  executor control (cancel running → TASK_CANCEL + cascade; retry resets
  skipped dependents; reassign changes the role; idempotency; refuse retry of
  COMPLETE); arbiter subscribes; Board pilot (rows per column, `c` publishes
  the control, `g` navigates to Prompt); registry guard passes (the screen
  declares `snapshot`, so it must be fanned out — it is).
* **Backward compatibility:** the executor's state machine gains one status;
  `_route_plan` becomes correct (Comms DAG starts moving — that is a visible
  change); no agent change beyond the arbiter's new subscription; the
  `user` strip gains one screen.

## What stays open after Phase 1

* **Attribution in the TUI** (`tui:anonymous`) — same open item as D-011.
* **Cancelling a running step** — Phase 1 marks the step and publishes
  `TASK_CANCEL`; the agent's cancel is best-effort, so the card may show
  cancelled while the agent finishes its LLM call. Honest, and worth a
  "cancel requested" sub-state if it confuses in practice.
* **Single tasks without a PLAN** are derived from the signal log
  (FIFO 30). Enough for a live pane; not a history. Phase 2's journal is.

## Decisions (operator, 2026-09-05)

The three open questions, answered in the vault note `HG-39 — Work board — challenge and design`:

1. **Board on the `user` strip** — yes. `acc/tui/registry.py` `_USER_SCREENS = {prompt, board, compliance}`; the user profile is Prompt · Board · Compliance.
2. **Cancelling a running step** — both: `PlanExecutor.on_step_control` publishes `TASK_CANCEL` to the agent (best-effort on its side) *and* marks the step `CANCELLED`, skipping dependents. A "cancel requested" sub-state while the agent finishes stays Phase 2.
3. **Reassign** — the same step under a new role with a fresh task id; history stays on the card. No new step is created.

All three were implemented as proposed in Phase 1 (`tests/test_work_board.py`: `test_cancel_running_step_sends_task_cancel_and_skips_dependents`, `test_reassign_changes_the_role_on_the_new_assignment`; `tests/test_tui_profiles.py` for the strip).

