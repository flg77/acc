# Tasks: Batch evaluation runs over a prompt corpus

**Change ID:** 20260817-evaluation-batch-runs
**Branch:** `feat/evaluation-batch-runs`

---

## Phase 1 — Corpus and execution

- [ ] `[1]` Corpus format; reuse golden-prompt structures where possible
- [ ] `[2]` Bounded-concurrency runner respecting budgets and gating
- [ ] `[3]` Restricted capability set for batch execution (open question 1)
- [ ] `[4]` Interrupt and resume without losing completed results

## Phase 2 — Results

- [ ] `[5]` Labelled result storage
- [ ] `[6]` `eval compare` between two labels
- [ ] `[7]` Decide scoring approach (open question 2)

## Phase 3 — Use it

- [ ] `[8]` Run the same corpus under two role→model configurations and compare
- [ ] `[9]` Document the comparison as the way configuration changes get justified

