# 20260825-harness-governed-prediction — proposal

## Why

ACC can answer two of the three questions an auditor asks about an autonomous
decision, and not the third.

**Who asked, and in what context** — `20260823-attributed-memory`, shipped in
v0.9.0. `requester` and `scope` are columns on `episodes`
(`acc/backends/vector_lancedb.py`), retrieval is a single equality test on the
scope key written at admission (`acc/memory_scope.py`), and promotion out of a
context is a reviewed proposal with a quorum of distinct humans.

**What the model actually saw** — the evidence-integrity work merged for v0.10.0.
The model-visible corpus is reconstructable from the durable record and hashed by
`corpus_sha256()` (`acc/prompt_record.py:153`).

**Whether the claim was any good** — nothing. `grep -ri predict acc/*.py` returns
zero hits. `acc/audit.py`, `acc/tracelog.py` and `acc/oversight.py` record
decisions and their approvals; none records a claim about the future that later
reality can contradict. An Art. 14 reviewer approves a summary of an intent and
cannot ask the more useful question — *the last nine times this role told me a
change was contained, how often was it?* — because the data was never written.

**The concrete cost is inside the memory function.** `acc/memory_reflection.py:268`
computes a durable note's confidence as:

```python
confidence=min(1.0, len(members) / (min_cluster * 2))
```

Cluster size. The long-term memory tier weights a lesson by **how often the agent
said it**, distils it from the agent's own assertions (`_summary_prompt()`,
`:164`), and caveats it with dissent the same model was asked to invent — a limit
`_split_dissent()`'s docstring states plainly: *"a model can invent
disagreement."* Nothing in an `episodes` row (`acc/cognitive_core.py:2391`) can
ever become false: the row holds the task payload and an embedding of the
*output*, never the result of the output.

`20260823-attributed-memory` governed **who** the loop learns from. It could not
govern **whether what it learns is true**, because that signal does not exist
anywhere in the runtime.

One more sign this was an oversight rather than a decision:
`acc/backends/vector_lancedb.py:57` declares an `icl_results` table carrying
`outcome` and `confidence`. `grep -rn icl_results acc/` returns two hits — the
schema and the TurboVec column map. **No writer, no reader, no CLI.** The idea
was sketched into the schema and never wired.

**The harness already knows how to insist, twice.** `_REASONING_SYSTEM_BLOCK`
(`acc/cognitive_core.py:326`) forces named reasoning headings and is kept in
lockstep with a bench scorer; the B1 marker-or-retry guard (`:494`, `:1213`)
detects a structurally deficient completion and re-prompts once with a
constrained, list-inlined directive. This change is that shape a third time.

Design context, the D0–D4 depth ladder, the failure modes and the kill criteria
live in `RP-03 — The prediction gate` (Obsidian: `ACC Roadmap/Proposals/`), with
the measured cost model and the predictability classes in its companion
`Prediction gate — compute cost, predictability classes and the scenario palette`.

## What changes

### Phase 1 (this ship) — the outcome ledger, no model contract

* **An `outcomes` record per episode**, written from signals the runtime already
  computes: capability exit status, test result, guardrail verdict, checkpoint
  delta, `TASK_COMPLETE` blocked/unblocked. Stamped with `scope` and `requester`
  at write time via `acc/memory_scope.py` — the same key episodes use, never a
  second partitioning scheme.
* **Outcome-weighted note confidence.** The term at `acc/memory_reflection.py:268`
  becomes cluster size composited with the resolution record of the source
  episodes. A note whose sources carry failed outcomes is demoted however often
  the lesson recurs.
* **`icl_results` retired** — superseded and dropped, so no future reader
  concludes ACC has two outcome tables.

This phase is deliberately separable and deliberately first. It is a strict
subset of the benefit at a fraction of the risk: it fixes the memory defect
without a prompt block, without a retry, and with no path to blocking a
dispatch. If Phases 3+ are never accepted, this one still earned its place.

### Phase 2 — the prediction ledger and the probe catalogue

* A `predictions` store: statement · observable · criterion · probe · horizon ·
  confidence · depth · kind · the policy inputs that selected them · `agent_id` ·
  `role_label` · `scope` · `requester` · `task_id` · `session_id` ·
  **`corpus_sha`** · status · outcome · resolver · evidence ref · Brier ·
  informativeness · class epoch.
* The `corpus_sha` link to the `PromptRecord` is what makes *"the agent predicted
  X given exactly this context"* reconstructable.
* **A generated, CI-verified probe catalogue** — enumerated from the resolvers
  this deployment actually has, for the same reason the tool catalogue is
  generated: a hand-maintained list eventually advertises a probe that is not
  there, and the failure is silent.

### Phase 3 — D1, soft enforcement only

* `_PREDICTION_BLOCK` in `build_system_prompt()` (`acc/cognitive_core.py:1788`),
  versioned and synced to the bench, **with the probe catalogue inlined**.
* Parse-or-retry beside `_split_reasoning()` / `_has_actionable_marker()`, one
  retry, the B1 shape unchanged.
* **The template keys on labelled fields, never on line syntax.** Measured on the
  existing reasoning block (`acc-dev-harness/history/reasoning_trace.jsonl`,
  n=145, `RedHatAI/Llama-3.2-3B-Instruct-FP8`): when a block appeared at all, 78
  of 79 carried **all five headings**, yet 45 of those 79 (**57%**) failed the
  scorer's `Option A:` / `Option B:` *line pattern* — same completion, same
  model. Confidence parses `0.7` and `70%` alike.
* At D1 a missing or unfalsifiable prediction after one retry is a logged
  **deficiency** and the action proceeds. The deficiency rate becomes a health
  metric, and it is the input later phases need before they may block anything.
* Scoring: Brier on `(confidence, outcome)` × an informativeness term that zeroes
  predictions never at risk — unfalsifiable, tautological, or resolved by a probe
  the agent itself controlled.

### Phase 4 — the memory feedback path

Resolved predictions join `consolidate()`'s input, so `_summary_prompt()` asks
*here is what you predicted and here is what happened* rather than *what did you
keep saying*. Falsifications write `dissent` with a probe result behind them.
A note whose predictive record goes negative is retired through the existing
`acc/memory_forget.py`. The `publish` proposal gains one **displayed** line —
*survived N resolutions, M falsifications* — and still never auto-executes in any
operating mode.

### Phase 5 — the calibration profile

Per `(role, scope, epoch)`: resolution rate, Brier by depth, over/under-confidence
direction, deficiency rate, expiry rate. Read on the prompt path as one
self-knowledge line, by the depth policy (**escalation only**), and by the
oversight surface. Two properties that are requirements rather than polish:

* **Novelty gate.** `acc/cognitive_core.py` already embeds every task and keeps a
  per-role domain centroid (`_compute_drift`, `_load_centroid`,
  `_cosine_similarity`). Beyond a distance threshold the calibration line is
  suppressed and the demanded kind drops to a bound — self-knowledge calibration
  is measured to degrade on unfamiliar tasks (arXiv 2207.05221), and a confident
  number from the wrong reference class is worse than no number.
* **Class epoch.** A `role_update` or a model swap resets the reference class.
  Below a class minimum `n`, no number is displayed at all.

### Phases 6–7 (deferred, and 7 is conditional)

* **Phase 6 — D2 contrastive.** The model also predicts the outcome of the option
  it rejected. Cheap, since `Options` and `Evaluation` are already forced, and
  supported on evidence: self-evaluation improves when a model weighs several of
  its own samples first (2207.05221).
* **Phase 7 — D3/D4 and hard enforcement.** Horizon predictions with a due time,
  a mandatory second-order claim, and at D4 a pre-mortem with tripwires. Two
  gates before it opens: context compaction exists (ACC has none anywhere today),
  **and** the measured deficiency rate permits blocking. On the reasoning block a
  3B model emitted nothing at all in **46%** of 145 runs; the 14B model missed 0
  of 5. Blocking at that rate routes nearly half of every gated turn to a human
  for a formatting failure. So the mode is derived, not declared: **`r < 10%` →
  may block; otherwise annotate and escalate the record.** `r` is measured
  **offline on the fixed bench suite**, never from live traffic — which is what
  makes it ungameable, since an agent wishing to escape a block cannot reach the
  suite.

### Two axes, not one

Depth (D0–D4) answers *how far ahead and how adversarially*, and is driven by
consequence — category, reversibility, blast radius. **Kind** answers *what may
honestly be claimed*, and is driven by predictability — a property of the probe
and the domain, never of the model:

| Kind | Claim | When |
|---|---|---|
| `P-DET` | a point outcome | deterministic probe, not reflexive, feedback inside the horizon |
| `P-STAT` | a frequency | stochastic but stationary, enough prior resolutions |
| `P-DIST` | a percentile or range | noisy but instrumented; scored in aggregate only |
| `P-BOUND` | an invariant that holds whatever happens | reflexive / adversarial / non-stationary / novel, or a catastrophic tail |
| `P-COND` | if X then Y | thin base rates, observable trigger |
| `none` | no prediction | no probe, or feedback lands after irreversibility |

As either outcome-noise or tail-cost rises, the honest claim migrates from the
outcome to the boundary. A robot arm and a portfolio rebalance both land on
`P-BOUND` — the first because its tail is catastrophic and instantaneous, the
second because its outcome is reflexive, adversarial and non-stationary. Asking
either *"will this succeed?"* yields a number that is respectively uninformative
and untrue. **`none` is a legitimate result**: where no probe exists the harness
demands nothing rather than accumulating `unverifiable` rows.

## Impact

* **Affected code (Phase 1):** `acc/backends/vector_lancedb.py` (new table, drop
  `icl_results`), `acc/backends/vector_turbovec.py` (column map),
  `acc/memory_reflection.py` (confidence term, `consolidate()` inputs),
  `acc/cognitive_core.py` (outcome capture at the existing episode-write site,
  `:2391`), `acc/cli/memory_cmd.py` (read surface).
* **Affected code (Phases 2–3):** `acc/cognitive_core.py` (`build_system_prompt`,
  parse-or-retry), `acc/capability_dispatch.py` (depth + kind selection),
  `acc/config.py` (`RoleDefinitionConfig`), a new `acc/prediction/` package, a new
  `acc/cli/predict_cmd.py`.
* **New in acc-dev-harness:** `tools/predict_eval` mirroring `tools/trace_eval`;
  a `PREDICTION_BLOCK_VERSION` constant the runtime block must match.
* **New env knobs:** `ACC_PREDICTION` (`0` disables globally — a kill switch, not
  a feature gate), `ACC_PREDICTION_MIN_N` (class minimum before a calibration
  number is displayed), `ACC_PREDICTION_BLOCK_MAX_R` (default `0.10`).
* **Tests:** ~26 across the phases. Phase 1: outcome write, scope stamping,
  demotion on falsified sources, pre-migration reads as *no data* rather than
  *all true*, `icl_results` gone. Phase 3: retry shape, deficiency logging,
  tautology scores zero, catalogue-bound probes. Phase 5: erasure rebuild,
  epoch reset, novelty suppression, min-`n` suppression. Invariants: a perfect
  record still hits the same category gate in every mode, including AUTO.
* **Backward compatibility:** every phase is additive and default-off at the
  deployment level. A deployment that never enables it is byte-identical to
  today. Phase 1 changes one computed value (note confidence) and no schema a
  reader depends on; existing rows without outcome data read as **unknown**,
  never as success.
* **Cost:** modelled from the measured bench numbers — D1 ≈ **1.25×** a baseline
  turn on a 14B-class model, ≈ **1.74×** on a 3B; D4 ≈ 1.55× / 2.16×. Model size
  dominates depth tier: D4 on a capable model is cheaper than D1 on a small one.
  Resolution adds no inference; storage is ~1.5 KB per prediction.

## What stays open

* **Does D3 block or annotate?** Phase 7 says block, gated on `r`. The counter is
  reviewer burden, and the honest answer needs Phase 3's live deficiency data.
  This is the question that most changes the mechanism's shape.
* **Where the ledger lives.** A LanceDB row-store beside `episodes`, or a separate
  durable store? Predictions are queried by due-time and by `(role, scope)` —
  not a vector access pattern, so an embedding column may be dead weight.
  `icl_results` is the cautionary tale about adding a table with no real reader.
* **Calibration per `role`, or per `(role, model)`?** Per-model is more truthful
  and fragments the data badly where `acc/llm_failover.py` rotates backends.
  Pick the coarsest unit that is not a lie.
* **Who is the default human resolver**, and does an unresolved human-class
  prediction block the next turn of the same objective? Blocking is principled
  and is also how an objective deadlocks.
* **Whether a falsification has a consequence beyond memory.** Raising a depth
  tier is proposed. Feeding `violation_learning`'s clustering is tempting and
  probably wrong: being wrong is not a violation, and conflating them teaches
  agents to avoid claims rather than errors.
* **Retention class** — audit's or memory's? Predictions are evidence and
  evidence is append-only, but a D4 horizon can outlive a session's retention
  window.
