# 20260912-the-tool-result-turn — tasks

## Phase 1 (v0.17.4) — the model sees what its tools returned

### 1.1 The opt-in
- [ ] `RoleDefinitionConfig.tool_result_turn: bool = False`

### 1.2 The results block
- [ ] `render_tool_results(outcomes)` — one line per invocation, `ok` results as
      JSON, failures as `error: …`
- [ ] Truncate per result (2000) and cap the block (6000)
- [ ] Instruction line: answer now, do not emit markers

### 1.3 The follow-up turn
- [ ] After dispatch: role opts in + at least one result → second `process_task`
      with the block appended to the content
- [ ] Flag the follow-up payload so it can never recurse
- [ ] The second answer replaces the reply; round-1 `invocations` are kept on
      TASK_COMPLETE
- [ ] Second-turn markers are parsed for logging but **not** dispatched

### 1.4 Docs
- [ ] `docs/ARCHITECTURE.md` + role docs mention the switch
- [ ] CHANGELOG entry

### Verification
- [ ] `tests/test_tool_result_turn.py` green
- [ ] Existing agent / cognitive-core suites green (default off = no change)
- [ ] Full sweep, classified against origin/main
- [ ] **AS-04 re-run on lighthouse**: the cell answers with the *tool's* weather,
      not an invented one, and the injected `send_weather_alert` is still gated

## Phase 2 (deferred)
- [ ] A capped multi-round loop
- [ ] Gating the second turn's markers instead of ignoring them
