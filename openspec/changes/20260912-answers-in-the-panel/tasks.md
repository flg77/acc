# 20260912-answers-in-the-panel — tasks

## Phase 1 — the answer under the question (UX-04)

### 1.1 The pure core
- [x] `Decision.exchanges: tuple[tuple[str, str], ...]`
- [x] `_exchange_lines`: `asked: …` + the answer, or *waiting for the agent…*
- [x] `MAX_EXCHANGES` = 2 shown, older ones counted; `MAX_ANSWER_LINES` = 6 then
      *… the rest is in the thread*

### 1.2 The widget
- [x] `ask(question)` / `answer(text)` / `exchanges`
- [x] kept across a refresh of the same decision, dropped on a different one
- [x] `_paint` carries them into the rendered decision

### 1.3 The wiring
- [x] `c` sets `_panel_chat_pending`
- [x] the dispatch records the question and the `task_id` the panel awaits
- [x] `_panel_answer(task_id, text)` routes only that task's reply, before the
      thread append; a continuation reply on a held thread counts too

### 1.4 Tests + docs
- [x] `tests/test_panel_answers.py` (10)
- [x] MANUAL: `c` answers in place
- [x] CHANGELOG

### Verification
- [x] the panel, gate and prompt suites green (842 passed across 43 files)
- [ ] lighthouse: `c` on a real gate, the answer renders under the question, the
      request stays PENDING

## Phase 2 (deferred)
- [ ] ask again from inside the panel (an input there, not the prompt box)
- [ ] the exchange kept with the oversight row, so a second approver sees it
- [ ] UX-03: evidence in the panel (diff preview, dry-run in place)
- [ ] stream the answer as it arrives
