# Tasks: LLM failover — a second choice when a provider fails

**Change ID:** 20260817-llm-failover-chain
**Branch:** `feat/llm-failover-chain`

---

## Phase 1 — Resolution and classification

- [ ] `[1]` Error classification: retryable vs fatal, per backend, with tests
- [ ] `[2]` Model resolver returning a client for a role at **call** time
- [ ] `[3]` Chain schema in configuration; single-value form stays valid

## Phase 2 — Failover

- [ ] `[4]` Advance to the next entry when the primary is exhausted
- [ ] `[5]` Health tracking with cooldown; automatic return to the primary
- [ ] `[6]` Policy gate interface, defaulting to the most restrictive behaviour
- [ ] `[7]` Fail closed with a named reason when a hop is refused

## Phase 3 — Visibility

- [ ] `[8]` Surface the active model (and that it is not the primary) in status output
- [ ] `[9]` Record failover events durably
- [ ] `[10]` Consider raising a proposal on sustained failover (open question 3)

## Phase 4 — Verification

- [ ] `[11]` Test: primary unreachable → work continues on secondary
- [ ] `[12]` Test: primary recovers → work returns
- [ ] `[13]` Test: fatal error (401) does **not** trigger failover
- [ ] `[14]` Test: no chain configured → behaviour identical to today
- [ ] `[15]` Live verification against a genuinely unavailable endpoint

