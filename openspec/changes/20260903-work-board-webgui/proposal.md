# 20260903-work-board-webgui — proposal

## Why

The WebGUI mirrors the TUI's screen set (`webgui/src/screens.tsx`, the
`20260830-webgui-tui-alignment` parity work) and already has the two halves a
board needs: the live snapshot over a WebSocket (`acc/webgui/ws.py`, the same
`CollectiveSnapshot` the TUI renders) and an authenticated action path whose
publishes are "exactly the TUI's — no new authority over the collective"
(`acc/webgui/routes_action.py`, `require_operator`, the principal stamped on
every payload). What it lacks is the same thing the TUI lacks — a work board —
and it is the surface where the board's two differentiators actually matter:

* **Attribution.** The WebGUI knows who acted (`webgui:<user>`); the TUI is
  `tui:anonymous`. A cancel or a reassign is an intervention in governed
  work; the WebGUI is where it can be signed by a person.
* **Multiple operators.** Two people can look at the same board. That is why
  the board must hold no state: every intervention is a bus signal the
  arbiter applies (`PLAN_STEP_CONTROL`, `TASK_CANCEL`), idempotent by
  construction, and both surfaces re-render from the runtime's answer.

This change builds on `20260903-work-board-tui` Phase 1, which lands the
shared projection (`acc/work_board.py`), the executor's control path and the
progress-bug fix. Nothing here adds a second decomposer, a second oversight
path, or a board that can be edited by hand (see the vault note
`HG-39 — Work board — challenge and design` for what was declined and why).

## What changes

### Phase 1 (this ship — v0.11.x)

* **`GET /api/board/{collective_id}`** (`routes_read.py`) — `project_board`
  over the hub's latest serialised snapshot; returns `{collective_id, columns:
  [{status, items: [WorkItem…]}], generated_ts}`. Read-only; any
  authenticated role.
* **`POST /api/board/control`** (`routes_action.py`) — body `{collective_id,
  kind: plan_step|task, plan_id?, step_id?, task_id?, action:
  cancel|retry|reassign, role?, reason?}`; `require_operator`; publishes
  `PLAN_STEP_CONTROL` (plan steps) or `TASK_CANCEL` (single tasks) with
  `actor = webgui:<principal.user>`. Returns `{status: "published"}` — the
  board updates when the arbiter re-broadcasts, not from the response.
* **The `Board` screen** (`screens.tsx`, nav entry after Prompt). A **real
  kanban**: five columns — Queued · Running · Blocked · Done · Failed — of
  cards (title, role/agent, iteration `n/max`, critique head, outcome line,
  age). Card actions by state: *Cancel* (Queued/Running), *Retry* (Failed),
  *Reassign* (Failed/Queued — role from the roster), *Answer the gate*
  (Blocked → deep-link to the Compliance screen's row; the WebGUI has no
  request region yet). Re-fetches on every WebSocket snapshot push; a
  *Trace · PLAN DAG* link opens the existing graph for the item's plan.
* **`client.ts`**: `fetchBoard(cid)`, `boardControl(...)`.
* **Docs.** `docs/howto-webgui.md` (or the WebGUI section of `howto-tui.md`)
  gains the Board; CHANGELOG **Added**.

### Phase 2 (deferred)

* **Answer the gate on the board.** A request card on the Blocked column
  mirroring the TUI's `PermissionRequest` option sets; needs the WebGUI to
  receive the `ASSISTANT_PROPOSAL` payload (already on the snapshot since
  0.11.0) and the per-kind options from `gate_cards.request_options`.
* **Drag to reassign.** Dragging a card between *roles* (lanes by role,
  not by state) as the reassign gesture — the only drag that is honest,
  since state is the runtime's.
* **Profiles** follow `20260902-tui-profiles` Phase 3 (the WebGUI reading
  the same registry).

## Impact

* **Affected code:** `acc/webgui/routes_read.py`, `acc/webgui/routes_action.py`,
  `webgui/src/api/client.ts`, `webgui/src/screens.tsx`, `webgui/src/App.tsx`,
  `webgui/src/styles.css` (columns), docs, CHANGELOG.
* **New env knobs:** none.
* **Tests:** ~7 Python (`tests/test_webgui.py` style, `TestClient` +
  `_FakeObserver`): `/api/board` shape from a seeded snapshot incl. a Blocked
  join; `/control` publishes `PLAN_STEP_CONTROL` with the principal as actor;
  `TASK_CANCEL` for a single task; 401/403 without operator; unknown action
  400. Front end: `npm run typecheck` in the tree (the build runs in the
  Containerfile).
* **Backward compatibility:** additive routes and one new screen; the
  existing `Trace · PLAN DAG` view is unchanged (and, after the TUI change's
  progress fix, finally shows motion).

## What stays open after Phase 1

* **Who may intervene.** `require_operator` is the WebGUI's only role
  boundary; whether a `viewer` may *retry* (harmless) but not *cancel* is a
  policy question for the auth work, not decided here.
* **Snapshot size.** The board re-fetches on every push; if the snapshot
  grows (Phase 2 durability), the board endpoint should accept `since`.
