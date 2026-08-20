# Tasks: Prompt cache — enable, measure, and decide the default

**Change ID:** 20260817-prompt-cache-enablement
**Branch:** `feat/prompt-cache-enablement`

---

## Phase 1 — Measure

- [x] `[1]` Choose a representative workload (golden prompts, or a live task set)
      *(golden prompts -- the suite already records `cache_read_tokens` per run, so it
      is the workload with the measurement already plumbed.)*
- [ ] `[2]` Enable on a deployment; capture cache-hit / cache-write token counts
      *(NOT DONE -- needs a live Anthropic-backed deployment. Only the anthropic backend
      reports cache counts; the others cache server-side and report nothing.)*
- [ ] `[3]` Record a before/after comparison on identical prompts
      *(NOT DONE -- blocked on [2].)*

## Phase 2 — Protect

- [x] `[4]` Test asserting the stable prefix is assembled ahead of variable content
- [x] `[5]` Surface cache token counts wherever usage is already reported

## Phase 3 — Decide

- [ ] `[6]` Decide the default and record the number that justified it
      *(NOT DONE, deliberately. The change says off is defensible only while nobody has
      measured; changing the default without the number would just move which
      unmeasured position we hold.)*
- [x] `[7]` Answer the retrieved-memory placement question (open question 1)

