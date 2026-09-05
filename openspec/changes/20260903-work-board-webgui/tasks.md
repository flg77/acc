# 20260903-work-board-webgui — tasks

Depends on `20260903-work-board-tui` Phase 1 (projection, `PLAN_STEP_CONTROL`, progress fix).

## Phase 1 (v0.11.x) — the WebGUI Board
### 1.1 API
- [x] `GET /api/board/{collective_id}` → `project_board` over the hub snapshot
      (`acc/webgui/routes_read.py`)
- [x] `POST /api/board/control` → `PLAN_STEP_CONTROL` / `TASK_CANCEL`, `require_operator`,
      `actor = webgui:<user>` (`acc/webgui/routes_action.py`)
- [x] tests: `tests/test_webgui_board.py` — board shape incl. Blocked join; control publishes
      with the principal; single task → TASK_CANCEL; validation 400/422; unknown collective 404
### 1.2 Screen
- [x] `client.ts`: `fetchBoard`, `boardControl` (typed `BoardItem` / `BoardColumn`)
- [x] `screens.tsx` `Board`: five columns of cards; per-state actions; Blocked shows the gate
      and points at Compliance (deep-link + PLAN DAG link → Phase 2); refetch on snapshot push
- [x] `App.tsx` nav entry after Prompt; `styles.css` columns
- [x] `npm run typecheck` green
### 1.3 Docs
- [x] CHANGELOG **Added** (WebGUI how-to: no dedicated doc exists yet — the TUI how-to's
      WebGUI section is Phase 2's place)
### Verification
- [x] targeted tests (9 Python + typecheck)
- [x] full sweep (2026-09-05 on the stacked branch: 5234 passed, 5 failed = the pre-existing WS reds (4 cosign-env + 1 model-registry, identical on main))
- [ ] lighthouse: same 3-step PLAN as the TUI smoke, watched from the WebGUI; cancel from
      the WebGUI and see the TUI board follow (one runtime, two surfaces)

## Phase 2 (deferred)
- [ ] request card on the Blocked column (`gate_cards.request_options` over the snapshot's
      `assistant_proposals`)
- [ ] drag between role lanes = reassign
- [ ] profiles via the shared registry

## Related
- `20260903-work-board-tui`; `20260830-webgui-tui-alignment`
- vault: `20-backlog/Hermes Gap/HG-39 — Work board — challenge and design`
