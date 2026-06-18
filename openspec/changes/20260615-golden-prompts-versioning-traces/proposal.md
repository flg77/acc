# Proposal: Golden Prompts — versioning, success criteria, and connected traces

## Problem

The Golden Prompts feature (TUI pane #9 + `acc.golden_prompts`) lets operators define,
edit, and run a suite of canonical prompts as an end-to-end regression net. The pressing
usability bugs (selection not rendering, prompts not loadable into the editor) are fixed
separately on `fix/golden-prompts-selection`. Four capability gaps remain, all raised by
the operator:

1. **No version control.** Saving a prompt overwrites `<name>.yaml` in the writable store.
   There is no revision history, no edit timestamp, no author, and no way to see what
   changed between runs or roll back a regression in the prompt itself. A prompt that
   "used to pass" cannot be compared against the version that passed.
2. **Success criteria are thin and implicit.** The only pass/fail signal is the
   `ExpectsBlock` asserts (`reply_non_empty`, `output_contains`, `output_matches_regex`,
   `latency_max_ms`, `invocations_*`). There is no first-class **definition of good** — a
   human-readable rubric of what a correct answer looks like — and no graded or
   judge-based criterion beyond mechanical string/regex matching.
3. **Runs are not connected to the prompt.** Results persist to `test/history/golden.jsonl`
   as flat rows, but a prompt in the TUI has no **connected traces**: you cannot select a
   prompt and see its run timeline, the agent invocation traces, or which revision produced
   which verdict.
4. **Persistence is container-local and undated.** New/edited prompts land only in the
   `/app/.acc-golden` volume; they are not version-controlled, not shareable, and carry no
   provenance, so a captured "golden" can silently diverge from the source-controlled suite.

## Current behavior

- `GoldenPrompt` = `{name, description, prompt, target_role, target_agent_id,
  operating_mode, timeout_s, expects: ExpectsBlock}`. No version, date, author, or rubric.
- `save_prompt()` writes `<name>.yaml`, clobbering any prior content; `dump_prompt()` is a
  plain atomic replace with no history.
- `GoldenResult` = `{name, passed, elapsed_ms, output_excerpt, failures, error}`. It does
  not record the prompt revision it ran against, nor a trace/correlation id.
- `persist_results()` appends rows to `golden.jsonl` keyed only by prompt `name`.
- The TUI detail pane shows the *latest* in-memory result only; there is no history view.

## Desired behavior

1. **Versioned prompts (date + revisions).** Each save creates an immutable revision with
   `revision` (monotonic int), `updated_at` (ISO-8601, UTC), and `author`. Prior revisions
   are retained alongside the current one; the TUI offers diff-against-previous and
   rollback. Existing single-file prompts load as `revision: 1` (back-compat).
2. **Definition of good + richer success criteria.** Add an optional `definition_of_good`
   (prose rubric) and a structured `success_criteria` block that extends `expects` with
   named, individually-reported criteria, including an optional **LLM-judge** criterion
   (rubric scored by a reviewer model). Mechanical asserts remain the fast default;
   judge criteria are opt-in.
3. **Connected traces.** `GoldenResult` records `prompt_revision`, a `trace_id`
   (correlation id propagated to the agent run), and run metadata. The TUI detail pane
   gains a **run timeline** for the selected prompt: each past run with verdict, latency,
   revision, and a pointer to its trace (signal/correlation id) so an operator can pivot
   from a failing golden to the exact agent invocation.
4. **Durable, provenanced persistence + export.** Prompts keep provenance (`source`:
   shipped | writable | attached) and can be **exported** from the writable store to a
   version-controlled location (an attached git dir or `examples/golden_prompts/`) in one
   action, closing the loop between captured candidates and the source-controlled suite.

## Success criteria

- [ ] Editing + saving a prompt creates `revision: N+1` with a fresh `updated_at`/`author`;
      the previous revision is retrievable and rendered as a diff in the TUI.
- [ ] A prompt may declare `definition_of_good` and named `success_criteria`; each criterion
      reports pass/fail independently in `GoldenResult`, and the overall verdict aggregates
      them. Existing prompts with only `expects` keep working unchanged.
- [ ] An optional LLM-judge criterion scores the reply against the rubric and contributes a
      pass/fail with a recorded rationale; it is skipped cleanly when no judge model is set.
- [ ] `GoldenResult` carries `prompt_revision` + `trace_id`; the TUI shows a per-prompt run
      timeline linking each run to its trace.
- [ ] A writable-store prompt can be exported to an attached/source-controlled dir with its
      provenance and revision intact.
- [ ] All new behavior is back-compatible: legacy `<name>.yaml` prompts load as revision 1
      with no `definition_of_good`/`success_criteria` and run exactly as today.
- [ ] `pytest tests/test_golden_prompts.py tests/test_diagnostics_screen_pilot.py` green,
      including new versioning, criteria, and trace-link tests.

## Scope

In: schema additions (`revision`, `updated_at`, `author`, `definition_of_good`,
`success_criteria`, `source`); a revision-aware store (history + diff + rollback);
criteria evaluation incl. opt-in LLM-judge; `GoldenResult.prompt_revision`/`trace_id` +
TUI run-timeline; export-to-version-control action; tests; docs.

Out: a full prompt-experimentation/A-B platform; cross-collective prompt sync; changing the
NATS run protocol beyond adding a correlation id; UI for editing arbitrary past revisions
(rollback restores a revision as a new revision rather than mutating history).

## Assumptions

- The writable store remains `/app/.acc-golden` (volume-backed); revisions are stored
  beside the current file (e.g. `<name>.yaml` + `<name>.history.jsonl`) so the existing
  load path keeps working.
- `trace_id` reuses the existing run correlation already present in the signal envelope;
  if absent, the runner generates one and stamps both the run and the result.
- The LLM-judge criterion uses the same model-resolution path as agents
  (`models.yaml` model_id) and is entirely optional.
- This builds on the selection/persistence fix (`fix/golden-prompts-selection`); the editor
  must be usable before versioning/diff UX is meaningful.
