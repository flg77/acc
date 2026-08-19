# Tasks: Prompt cache — enable, measure, and decide the default

**Change ID:** 20260817-prompt-cache-enablement
**Branch:** `feat/prompt-cache-enablement`

---

## Phase 1 — Measure

- [ ] `[1]` Choose a representative workload (golden prompts, or a live task set)
- [ ] `[2]` Enable on a deployment; capture cache-hit / cache-write token counts
- [ ] `[3]` Record a before/after comparison on identical prompts

## Phase 2 — Protect

- [ ] `[4]` Test asserting the stable prefix is assembled ahead of variable content
- [ ] `[5]` Surface cache token counts wherever usage is already reported

## Phase 3 — Decide

- [ ] `[6]` Decide the default and record the number that justified it
- [ ] `[7]` Answer the retrieved-memory placement question (open question 1)

