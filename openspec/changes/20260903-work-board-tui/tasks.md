# 20260903-work-board-tui — tasks

## Phase 1 (v0.11.x) — projection + intervention + the TUI Board
### 1.1 Fix the progress bug
- [x] `NATSObserver._route_plan` applies `step_progress` on every re-broadcast; knows
      `CANCELLED` (`acc/tui/client.py`)
- [x] test: re-broadcast `s1: RUNNING` → `PlanSnapshot.step_progress["s1"] == "RUNNING"`
### 1.2 `acc/work_board.py` (pure, shared with the WebGUI)
- [x] `WorkItem`, `COLUMNS`, `project_board(active_plans, cluster_topology,
      oversight_pending_items, oversight_recent_items, signal_flow_log)` over plain dicts
- [x] sources: plan steps (+ iteration / critique when present), cluster members, single
      tasks from the signal log, Blocked join on `task_id`, outcomes
- [x] `signal_flow_log` entries gain `task_id` + `target_role` (additive)
- [x] tests: each source; Blocked join; column order; cancelled folds into FAILED;
      missing fields degrade, never raise
### 1.3 `PLAN_STEP_CONTROL` in the executor
- [x] `SIG_PLAN_STEP_CONTROL`, `subject_plan_control(cid)`, `SIGNAL_MODES` (SYNAPTIC)
      (`acc/signals.py`)
- [x] `STATUS_CANCELLED`; `PlanExecutor.on_step_control` — cancel (TASK_CANCEL if running,
      cascade skipped, re-broadcast), retry (reset step + skipped dependents, dispatch),
      reassign (retry with a new role); idempotent; refuse retry of COMPLETE (`acc/plan.py`)
- [x] arbiter subscribes next to the PLAN submit subject (`acc/agent.py`)
- [x] tests: cancel running → TASK_CANCEL + dependents skipped; retry resets and
      re-dispatches; reassign changes the role on the new TASK_ASSIGN; second cancel no-op;
      retry COMPLETE refused; arbiter subscription
### 1.4 The Board screen
- [x] `acc/tui/screens/board.py` — swimlane DataTable (header row per column), detail
      panel, keys `c` / `r` / `a` / `g` / `Enter`; actions via `_PublishMessage`
- [x] registry: overflow pane (last, so `Ctrl+A` 0/1 stay Marketplace/Catalogs), `receives_snapshot=True`,
      on both profiles (`user` strip = Compliance · Prompt · Board)
- [x] `g` → NavigateTo prompt + focus the request region for that gate
- [x] tests: pilot — rows per column from a snapshot; `c` publishes `PLAN_STEP_CONTROL`;
      a single task's `c` publishes `TASK_CANCEL`; `g` navigates; registry guard green
### 1.5 Docs
- [x] `acc/tui/help/board.md`; `docs/howto-tui.md` screen reference; CHANGELOG **Added** +
      **Fixed**
### Verification
- [x] targeted tests (≈16 new)
- [x] full sweep (2026-09-05 on the stacked branch: 5234 passed, 5 failed = the pre-existing WS reds (4 cosign-env + 1 model-registry, identical on main))
- [ ] lighthouse: submit a 3-step PLAN (`acc-cli plan`), watch the Board move
      QUEUED → RUNNING → DONE; cancel step 2 and see 3 skipped; retry; a gated `shell_exec`
      shows BLOCKED and `g` lands on the request

## Phase 2 (deferred) — durability
- [ ] tracelog `KIND_PLAN_STEP` on every transition; Redis mirror of plan state (24 h);
      heartbeat plan summary; cold-start the board from it
- [ ] "cancel requested" sub-state while the agent finishes
- [ ] request region on the Board itself

## Related
- `20260903-work-board-webgui` — the WebGUI Board over the same projection + control signal
- `20260902-assistant-autonomy-prompt-pane-approvals` 1.4 — where a Blocked item is answered
- `20260902-tui-profiles` — the registry that makes adding the screen a one-line change
- vault: `20-backlog/Hermes Gap/HG-39 — Work board — challenge and design`; DS-09
