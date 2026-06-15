# Tasks: Golden Prompts — versioning, success criteria, connected traces

## Phase 0 — Prerequisite (done separately)
- [x] Fix selection/editor wiring (`fix/golden-prompts-selection`): row cursor, highlight→detail,
      explicit select→editor. Required before revision/diff UX is usable.

## Phase 1 — Schema (back-compatible)
- [ ] Extend `GoldenPrompt` in `acc/golden_prompts.py`: add `revision: int = 1`,
      `updated_at: str = ""`, `author: str = ""`, `definition_of_good: str = ""`,
      `success_criteria: list[SuccessCriterion] = []`. `extra="ignore"` on load for
      forward-compat; legacy files load as revision 1.
- [ ] Add `SuccessCriterion` model: `{name, kind: assert|judge, ...}` where `assert` reuses
      `ExpectsBlock` fields and `judge` carries `rubric` + optional `judge_model_id` +
      `threshold`.
- [ ] Add `source: Literal["shipped","writable","attached"]` to the in-TUI prompt view
      (derived at load time from which root it came from; not persisted in the file).

## Phase 2 — Revision-aware store
- [ ] On save, bump `revision`, stamp `updated_at` (UTC ISO-8601) + `author`
      (`$ACC_OPERATOR_ID`/`cli:operator`), append the prior version to
      `<name>.history.jsonl` before atomic-replacing `<name>.yaml`.
- [ ] Add `load_history(name)` + `diff_revisions(a, b)` helpers (text diff of the YAML body).
- [ ] Add `rollback(name, revision)` → restores a past revision as a NEW revision (history
      is append-only; never mutated).

## Phase 3 — Success criteria + definition of good
- [ ] Evaluate each `SuccessCriterion` independently in the runner; aggregate to the overall
      `passed`. Mechanical `assert` criteria run first (fast); `judge` criteria run only if
      configured.
- [ ] Implement the opt-in LLM-judge: send `{prompt, reply, rubric}` to `judge_model_id`
      (resolved via `acc.models`), parse a `{score, pass, rationale}` verdict, record it.
      Skip cleanly (criterion = N/A, not fail) when no judge model is available.
- [ ] Surface `definition_of_good` in the TUI detail pane and in the editor template.

## Phase 4 — Connected traces
- [ ] Add `prompt_revision: int` + `trace_id: str` to `GoldenResult`; generate/propagate a
      correlation id into the run and stamp it on the result.
- [ ] Extend `persist_results()` rows with `prompt_revision` + `trace_id`.
- [ ] TUI: add a per-prompt **run timeline** to the detail pane (last N runs: ts, verdict,
      latency, revision, trace_id) loaded from `golden.jsonl`, with the trace id shown so an
      operator can pivot to the agent invocation.

## Phase 5 — Persistence / export
- [ ] Add an **Export** action (TUI + `acc-cli`) to copy a writable-store prompt (with its
      current revision + provenance) into an attached/source-controlled dir or
      `examples/golden_prompts/`.
- [ ] Show `source` (shipped/writable/attached) per row so overrides are obvious.

## Phase 6 — Tests + docs
- [ ] `tests/test_golden_prompts.py`: revision bump on save, history append, diff, rollback,
      criteria aggregation (assert + mocked judge), legacy back-compat (revision 1).
- [ ] `tests/test_diagnostics_screen_pilot.py`: run-timeline renders; diff view renders;
      export action writes to the target dir.
- [ ] Update `docs/TESTING.md` + `docs/howto-tui.md` (Diagnostics pane) for versioning,
      definition-of-good, and connected traces.
