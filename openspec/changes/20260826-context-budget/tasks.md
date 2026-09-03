# 20260826-context-budget — tasks

## Phase 1 (v0.11.0) — declare the window, budget the assembly, record the drop

### 1.1 Declared capacity -- PARTIAL (the window landed; the reserve did not)
- [x] `ModelEntry.context_window: int = 0` (`acc/models.py`). `0` means
      **undeclared**, not zero capacity, and every consumer treats the two
      differently.
- [ ] `ModelEntry.max_output_tokens` -- deferred with the budgeter, which is
      its only consumer.
- [x] `_entry_to_dict` emits it only when non-zero, so a registry that never
      declared one does not grow a `context_window: 0` on the next save.
- [x] **Fixed the pre-existing `zone` omission** in the same serialiser. It was
      not merely un-persisted: the whole registry is rewritten on any edit, so
      every `upsert_model` / `delete_model` / TUI save stripped every declared
      residency zone at once, and with none left `ZonePolicyGate` returns
      `Decision(True, "no zones declared")` and permits any cross-boundary
      failover hop -- a governance control switched off by an unrelated save.
- [x] Added `test_every_optional_field_is_serialised`, which fails when a new
      `ModelEntry` field is not in the serialiser. That is the guard that would
      have caught `zone`, and it is what stops the next field going the same
      way.
- [x] `models.yaml.example` annotated: what `context_window` means, that it is
      the **served** window rather than the model card's, that Ollama gets it
      as `num_ctx`, that `doctor --probe` reconciles it, and that raising it
      raises KV allocation so it should come from a measurement.

### 1.6 Ollama silent-truncation fix -- DONE
- [x] `acc/backends/llm_ollama.py` sends `options: {"num_ctx": N}` on
      `/api/chat`.
- [x] Plumbed the whole way: `ModelEntry.context_window` -> `model_env`
      (`ACC_LLM_CONTEXT_WINDOW`) **and** `llm_failover._llm_overlay`
      (`context_window`) -> `LLMConfig.context_window` -> both backend
      construction sites (`acc/config.py`, `acc/cli/llm_cmd.py`).
- [x] `_llm_overlay` updated alongside `model_env`. Their docstrings already
      claimed to be "deliberately the same mapping" and nothing enforced it; a
      field added to only one works until the first failover hop and then
      silently stops. Now covered by a parametrised test over every backend.
- [x] **Undeclared sends no `options` key at all** -- not `{}`, not a default.
      The request stays byte-identical to the pre-change one, so upgrading ACC
      cannot change a deployed host's memory profile. This is open question 5
      answered conservatively: a larger window allocates a larger KV cache per
      request, and a box sized for the accidental ~4k is where an OOM lands.
      The figure is rolled out per deployment, measured, via
      `acc-cli doctor --probe`.
- [x] `tests/test_ollama_num_ctx.py` -- 18 tests over the wire format, the
      three plumbing hops and the registry round-trip.

### 1.2 The packer -- DONE
- [x] `acc/context_budget.py`: `Block`, `Drop`, `BudgetResult`,
      `ContextOverflow`, `Posture`, `pack()`, `resolve_ceiling()`,
      `block_caps()`, `posture_for()`, `standard_blocks()`.
- [x] The algorithm from `design.md` 5 -- task first, priority-ordered
      eviction, greedy within divisible blocks, assembly by `display_rank`.
- [x] Heading emitted iff >=1 item survives -- **and the footer with it**. The
      design missed the footer: the episode block ends with "(Use these to
      ground your answer...)", which pointing at nothing is worse than a bare
      dangling heading. `Block` grew a `footer` field.
- [x] Overhead (heading + footer) is charged before any item is admitted, so a
      block whose structure alone exceeds its budget is dropped whole rather
      than rendering a heading over nothing.
- [x] Pure: no I/O, no await, no clock, no mutation of the inputs (tested).
- [x] `standard_blocks()` owns the priority/display constants, so a call site
      cannot get the eviction order subtly wrong while looking right.
- [x] Posture table (`edge` / `standalone` / `rhoai`) with the `design.md` 6
      numbers; an unrecognised mode gets the **conservative** posture, because
      too tight costs recall and too loose costs integrity.
- [x] `block_caps()` = `min(share x ceiling, absolute_cap)` -- shares bind on a
      small window, absolute caps on a large one. Tested both ways.
- [x] `estimate_tokens()` -- class-weighted, no tokenizer, no network, with a
      `calibration` hook so the EMA (1.3) does not change the shape.
- [x] Module docstring in house style: why two orders, why the task is never
      truncated, why nothing is summarised, why posture never sets the window.

> **The invariant that makes wiring safe.** `pack()` reproduces the existing
> `"

".join` **byte-for-byte** when nothing is dropped. Without that, this
> is a rewrite of every prompt ACC sends rather than a ceiling on the ones that
> were overflowing, and it could not be rolled out to a healthy deployment
> without re-benchmarking it.

> **Mutation-verified, and it found a hole.** Six deliberate breaks were
> applied and the suite re-run: render in eviction order, abandon a block at
> the first miss, truncate the task instead of raising, emit a heading with no
> items, ignore per-block caps, let posture set the window. Five were caught
> immediately. The sixth -- emitting a heading over nothing -- **was missed**,
> because `pack()` short-circuits before ever calling `Block.render()` with an
> empty list, so that guard was defensive and untested. Pinned directly now,
> and the sweep is clean. 100% line coverage, 41 tests.

### 1.3 Estimation and calibration -- PARTIAL (heuristic in; EMA not)
- [x] `estimate_tokens()` -- class-weighted (words + punctuation + non-ASCII
      surcharges), no tokenizer, no network, no model download. `chars / 4` is
      the usual shortcut and is wrong for what ACC actually sends, which is
      largely code, YAML and command output.
- [x] A `calibration` parameter already threads through `pack()` and is
      reported on the result and the stage event, so landing the EMA does not
      change any shape.
- [ ] Per-`(backend, model)` EMA correction, `alpha=0.25`, clamped
      `[0.6, 2.0]`, reset on model change.
- [ ] Read `usage.prompt_tokens` in `_call_llm` alongside the existing
      `total_tokens` and feed the correction. The signal is already crossing
      the wire and being discarded.
- [ ] Safety margin narrows as the sample count rises.

### 1.4 Posture defaults -- DONE
- [x] Posture table lives in `acc/context_budget.py` keyed off `deploy_mode`,
      with the `design.md` 6 numbers, resolved via `ACC_DEPLOY_MODE`.
- [x] `ACC_CONTEXT_BUDGET` (kill switch / override) and
      `ACC_CONTEXT_WINDOW_DEFAULT` are read.
- [x] `ContextBudgetConfig` in `acc/config.py` so the posture is settable from
      `acc-config.yaml` rather than only from the environment. Two fields --
      `reserve_output` and `safety_margin_pct` -- because those are the halves
      of the posture that belong to a *site*; shares and caps stay in the
      table, keyed off the deployment class.
- [x] `ACC_CONTEXT_RESERVE_OUTPUT` / `ACC_CONTEXT_SAFETY_MARGIN` overrides,
      in `_ENV_MAP` like every other setting, so env beats yaml the way an
      operator already expects.
- [x] Applied in `posture_for()`, **not** in `resolve_ceiling()`, so the
      posture a stage event reports is the one the ceiling was computed from.
      An override renames it `<mode>+custom` -- a record that said `edge` while
      custom numbers were in force would be the same class of lie `source`
      exists to prevent.
- [x] `0` is the "undeclared" sentinel at the config layer and maps to `None`
      before `posture_for()`, so an unset field and a deliberate zero never
      collapse into each other.
- [x] Malformed / out-of-range env values warn and fall back to the posture,
      never to zero. `ACC_CONTEXT_SAFETY_MARGIN=20` (a percentage where a
      fraction was wanted) is the specific trap that is tested.
- [x] Register the settable keys in `acc/profiles.py` `SETTABLE` so a named
      deployment profile can carry the posture.
- [x] `acc-config.yaml.example` annotated: what each number means, that the
      margin is a fraction, and why the *window* is not settable here.
- [x] `tests/test_context_budget_posture_config.py` -- 29 tests over the config
      model, yaml/env precedence, `posture_for` overrides, and the defensive
      parse.

### 1.5 Wiring -- DONE
- [x] `_compose_user_content` takes an optional `budget=`; with it absent the
      pre-existing branch runs unchanged, so every legacy caller keeps its
      behaviour.
- [x] `_process_task_body` resolves the budget and passes it, subtracting the
      measured system prompt so what is budgeted is what is actually left.
- [x] `_system_prompt_tokens()` -- measured once per role and memoised, keyed
      on the rendered length so a role edit invalidates it. A **constant**
      rather than an estimate only because PR-CA1 made the system prompt stable
      per role; the prefix-cache discipline pays twice.
- [x] `acc.pipeline.context_budget` emitted on **every** turn, degraded or not.
      Silence would be indistinguishable from the check not running.
- [x] `ContextOverflow` propagates as a task failure, with an `overflow: true`
      event recorded first. Trimming the operator's request is the failure this
      change exists to prevent, so it is not caught here.
- [x] `_resolve_context_budget()` reads `ACC_CONTEXT_BUDGET` (kill switch /
      override), `ACC_LLM_CONTEXT_WINDOW` (what `model_env` sets from
      `ModelEntry.context_window`), `ACC_CONTEXT_WINDOW_DEFAULT`, and
      `ACC_DEPLOY_MODE` for posture. Env rather than `ACCConfig` because
      `CognitiveCore` is not given one -- the same reason
      `ACC_ROLE_TOKEN_BUDGET` is read there.
- [x] Malformed env values never disable budgeting by accident: only an
      explicit `<= 0` is the kill switch.
- [x] `tests/test_context_budget_wiring.py` -- 28 tests.

> **Two refactors the wiring forced, both improvements.**
>
> 1. `_render_episode_block` / `_render_memory_notes_block` were split into
>    `_episode_parts` / `_notes_parts` returning `(heading, items, footer)`, with
>    the render functions rewritten in terms of them. The packer needs to evict
>    an episode at a time, and this keeps exactly one place that knows the
>    formatting -- pinned by a test asserting the two agree.
> 2. The budget arrives as an **argument**, never off `self`.
>    `tests/test_thread_continuity.py` calls `_compose_user_content` unbound
>    with a two-method `SimpleNamespace` shim, which would have broken the
>    moment the seam reached for configuration. Keeping it explicit preserves
>    that and keeps the seam pure; the environment is read in exactly one place.
>
> **The thread is split, not dropped.** `replay_block` returns one rendered
> string, but the thread is the highest-priority evictable block -- losing it
> whole because it is one turn too long throws away the context the current
> request is answering. It is split back on `REPLAY_HEADING`, and an
> unrecognised shape degrades to a single indivisible item rather than guessing
> where the turns begin.
>
> **Known gap, recorded not papered over.** An in-process failover hop overlays
> `LLMConfig` rather than the environment, so a hop to a model with a different
> window keeps the original budget until the wiring is config-aware. It errs
> conservatively on the common case, where the primary is the larger model.
>
> **A sharp edge worth knowing.** A window below the posture's own
> reserve + margin (600 on edge, where reserve is 512) clamps the ceiling to 1
> and every task overflows. That is the right outcome for a nonsense
> configuration -- loud beats an agent sent an empty prompt -- and it is pinned
> by a test so nobody rediscovers it in production.

### Verification
- [x] `tests/test_context_budget.py` -- 41 tests: byte-identity, eviction
      order, greedy divisible eviction, heading/footer suppression,
      `ContextOverflow`, determinism, purity. 100% line coverage.
      **Mutation-verified**: six deliberate breaks, all caught (one only after
      a gap it exposed was closed).
- [ ] Estimator calibration tests -- land with the EMA (1.3).
- [x] Posture covered in `test_context_budget.py` and
      `test_context_budget_wiring.py`: the three tables, and that `deploy_mode`
      selects posture without ever altering the window.
- [x] Stage-event shape and `degraded`-iff-drops covered in
      `test_context_budget_wiring.py`, including the undegraded turn.
- [x] `tests/test_ollama_num_ctx.py` -- `num_ctx` present and equal to the
      resolved window; absent entirely when undeclared.
- [x] Regression: an existing `models.yaml` loads and round-trips through the
      serialiser without gaining or losing a field (guarded by
      `test_every_optional_field_is_serialised`).
- [x] `ACC_CONTEXT_BUDGET=0` reproduces today's assembled prompt
      byte-for-byte, and so does a budgeted prompt that simply fits.
- [x] Full sweep classified against a clean worktree at `f8ece3e`: the 8
      pre-existing reds and nothing else.
- [ ] lighthouse smoke — a multi-turn thread on the 3B FP8 model, asserting
      `degraded: true` appears and the task text arrives intact.

## Phase 2 (deferred) — window discovery
- [ ] `check_context_window` in `acc/preflight.py` behind `probe_endpoints`.
- [ ] vLLM/`openai_compat` `GET /v1/models` → `max_model_len`.
- [ ] Ollama `POST /api/show` → `model_info["<arch>.context_length"]` +
      `parameters.num_ctx`; effective = `min(...)`.
- [ ] Anthropic static table + `/v1/messages/count_tokens`.
- [ ] `acc-cli doctor --probe` reconciliation lines; MISMATCH is WARN, never an
      override of the declaration.

## Phase 3 (deferred) — learned ceiling
- [ ] Parse the limit out of a 400 context-length rejection.
- [ ] Cache per `(base_url, model)` with a TTL; one bounded retry.

## Phase 4 (deferred) — retrieval quality
- [ ] Similarity floor on `_retrieve_episodes` (`acc/cognitive_core.py:2260`).
- [ ] Near-duplicate suppression across retrieved episodes.
- [ ] Chunk-level episode retrieval, matching the docstore's 1200/200 shape.

## Phase 5 (deferred) — structured compaction
- [ ] Fixed-slot extraction (goal / decisions / open questions / handles),
      schema-validated, consuming the budgeter's deficit figure.

## Phase 6 (deferred) — concurrency-aware ceiling
- [ ] `min(model_window, kv_pool / concurrent_agents)`, joined to
      `role.max_parallel_tasks` (Cat-A A-019).
