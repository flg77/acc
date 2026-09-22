# 20260825-harness-governed-prediction — tasks

Status legend: `[ ]` not started · `[x]` done. Nothing here is started.

Phases 1–2 are additive and carry no model contract. Phase 3 introduces the
prompt block. Phase 7 is **conditional** and must not start until its two gates
(§7.0) are both true.

## Phase 0 — settle, and build the bench

### 0.1 Settle before building

- [ ] Answer: does D3 **block** or **annotate**? (Gated on Phase 3 data — record
      the decision and its evidence, do not guess it now.)
- [ ] Answer: ledger as a LanceDB row-store beside `episodes`, or a separate
      durable store? Predictions are queried by due-time and `(role, scope)`,
      which is not a vector access pattern.
- [ ] Answer: calibration per `role` or per `(role, model)`? Pick the coarsest
      unit that is not a lie.
- [ ] Answer: default human resolver, and whether an unresolved human-class
      prediction blocks the next turn of the same objective.
- [ ] Answer: consequence of a falsification beyond memory. Do **not** route it
      into `violation_learning` without an explicit argument — being wrong is not
      a violation.
- [ ] Answer: retention class (audit's or memory's).

### 0.2 The bench — `acc-dev-harness/tools/predict_eval`

- [ ] Mirror `tools/trace_eval`: render the tier templates, run a prompt suite
      against a model, score **shape compliance** (not correctness — nothing can
      know correctness yet).
- [ ] Define `PREDICTION_BLOCK_VERSION` in the bench; the runtime block must
      match it, exactly as `_REASONING_SYSTEM_BLOCK` matches
      `trace_eval/reasoning_prompt.py`.
- [ ] Score on **labelled fields**, never on line syntax. The measured reason:
      45 of 79 otherwise-complete reasoning blocks failed only the `Option A:`
      line pattern.
- [ ] Record per-model deficiency rate `r` to `history/predict_eval.jsonl`, same
      shape as `history/reasoning_trace.jsonl`.
- [ ] Baseline at least one small model (3B-class) and one mid model (14B-class).
      `r` from this suite is what Phase 7's enforcement mode reads.

## Phase 1 — outcome ledger and the memory fix (no model contract)

### 1.1 Capture

- [ ] Add the outcome record (schema + writer). Populate from capability exit
      status, test result, guardrail verdict, checkpoint delta, and the `blocked`
      flag on `TASK_COMPLETE`.
- [ ] Write at the existing episode-write site (`acc/cognitive_core.py:2391`) so
      an outcome and its episode cannot diverge.
- [ ] Stamp `scope` and `requester` from `acc/memory_scope.py`. Do not introduce
      a second partitioning scheme.
- [ ] No signal available → `unknown`. Never success, and never an omission that
      a later aggregate reads as success.

### 1.2 Memory

- [ ] Replace the confidence term at `acc/memory_reflection.py:268` with cluster
      size composited with the source episodes' resolution record.
- [ ] Verify: a note distilled from episodes carrying failed outcomes scores
      lower than an equally-recurrent note whose sources resolved true.
- [ ] Verify: a pre-migration deployment reads as *no outcome data*, not as
      *all true*.

### 1.3 Retire the fossil

- [ ] Remove `icl_results` from `acc/backends/vector_lancedb.py` and the TurboVec
      column map. `grep -rn icl_results acc/` must return nothing.

### 1.4 Read surface

- [ ] Extend `acc/cli/memory_cmd.py` (or add `acc-cli predict`) so an operator can
      see the outcome record behind a note's confidence.

## Phase 2 — prediction ledger and probe catalogue

- [ ] Create the `predictions` store per **REQ-PRD-001**, including `corpus_sha`
      and the reference-class epoch.
- [ ] Generate the probe catalogue by enumerating the deployment's resolvers;
      declare determinism, feedback latency and reflexivity per entry.
- [ ] CI check: a resolver with no catalogue entry fails; a catalogue entry with
      no resolver fails.
- [ ] Verify: a deployment with no test runner does not advertise a test probe.

## Phase 3 — D1 and soft enforcement

- [ ] `_PREDICTION_BLOCK` in `build_system_prompt()`, versioned, probe catalogue
      inlined, tier- and kind-parameterised.
- [ ] `_split_prediction()` / `_has_prediction()` beside the existing splitters;
      one constrained retry on the B1 shape.
- [ ] Parser keys on labelled fields; confidence accepts `0.7` and `70%`.
- [ ] Depth + kind selection in `acc/capability_dispatch.py`; unknown capability
      maps to the strictest reachable tier.
- [ ] `none` is a valid selection — where no probe exists, demand nothing.
- [ ] Deficiency logging + health metric on `acc-cli status` / `doctor`.
- [ ] Brier + informativeness scoring; a tautological prediction scores zero even
      when it resolves true.
- [ ] Verify: a turn whose command fails resolves its prediction false with no
      human involved.
- [ ] Verify: `ACC_PREDICTION=0` is byte-identical to today.

## Phase 4 — memory feedback

- [ ] Resolved predictions join `consolidate()`'s input; `_summary_prompt()` asks
      what was predicted and what happened.
- [ ] Falsifications write `dissent` with an evidence reference, distinguishable
      from model-authored dissent.
- [ ] Net-negative notes retire through `acc/memory_forget.py` and leave the hot
      cache.
- [ ] `publish` proposals display resolution/falsification counts — advisory
      only, still never auto-executing in any mode including `AUTO`.

## Phase 5 — calibration profile

- [ ] Aggregate per `(role, scope, depth, epoch)`.
- [ ] Prompt line, depth-policy input (**escalation only**), oversight surface.
- [ ] Novelty gate from the existing centroid distance (`_compute_drift`,
      `_load_centroid`); beyond threshold suppress the line and drop the kind to
      `P-BOUND`.
- [ ] Epoch reset on `role_update` and on a model change.
- [ ] Suppress any figure below `ACC_PREDICTION_MIN_N`.
- [ ] Verify: erasing a requester rebuilds the profile.
- [ ] Verify: a role with a perfect record still hits the same category gate in
      every operating mode.

## Phase 6 — D2 contrastive

- [ ] Demand the predicted outcome of the **rejected** option alongside the taken
      one.
- [ ] The counterfactual scores only if the fallback is actually taken; otherwise
      it is recorded, unresolved, and read by humans.

## Phase 7 — D3/D4 (CONDITIONAL)

### 7.0 Gates — both must be true before any task below starts

- [ ] Context compaction exists. ACC has none anywhere today, and D3/D4 add the
      most generation to the deployments nearest the context ceiling.
- [ ] Measured `r` from Phase 0.2 is below `ACC_PREDICTION_BLOCK_MAX_R` for the
      target model, **or** the mode is annotate-only for that deployment.

### 7.1 Build

- [ ] Horizon predictions with an explicit due time; mandatory second-order claim.
- [ ] D4: inverted pre-mortem, named tripwires, and the rollback a tripwire
      offers (offers — never performs).
- [ ] Due-scan resolver invoked by the existing scheduler; no new daemon.
- [ ] Deny matrix per **REQ-ELI-006/007**.
- [ ] Expiry as a status; pending-past-due as a health metric.
- [ ] Verify: a prediction due in seven days resolves or expires visibly; no
      pending row ages silently.

## Cross-cutting verification

- [ ] No prediction is resolved by its author (write-time rejection).
- [ ] No prediction, confidence or calibration record can satisfy or weaken a
      Cat-A rule.
- [ ] Every human-facing rendering says *the agent expects*, with confidence and
      caveat — never a commitment.
- [ ] Kill criteria instrumented and reviewed after one window: `unverifiable`
      > 50% of resolutions · no note ever demoted by an outcome ·
      pending-past-due > 20%. Any one of these means retire the mechanism, not
      tune it.
