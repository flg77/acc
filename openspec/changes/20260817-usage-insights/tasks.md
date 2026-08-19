# Tasks: Token and activity insights

**Change ID:** 20260817-usage-insights
**Branch:** `feat/usage-insights`

---

## Phase 1 — Aggregation

- [ ] `[1]` Confirm which fields the tracelog reliably records, per backend
- [ ] `[2]` Aggregation over a window: per role, per model, per collective
- [ ] `[3]` Label backends that do not report usage rather than showing a silent zero

## Phase 2 — Command

- [ ] `[4]` `acc-cli insights` with `--days`, `--role`, `--collective`
- [ ] `[5]` Budget headroom per role
- [ ] `[6]` `--json`

## Phase 3 — Decisions

- [ ] `[7]` Decide the cost-modelling question and record it
- [ ] `[8]` Before/after comparison across a configuration change on a live node

