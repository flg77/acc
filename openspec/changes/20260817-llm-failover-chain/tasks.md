# Tasks: LLM failover — a second choice when a provider fails

**Change ID:** 20260817-llm-failover-chain
**Branch:** `feat/llm-failover-chain`

---

## Phase 1 — Resolution and classification

- [x] `[1]` Error classification: retryable vs fatal, per backend, with tests
- [x] `[2]` Model resolver returning a client for a role at **call** time
- [x] `[3]` Chain schema in configuration; single-value form stays valid

## Phase 2 — Failover

- [x] `[4]` Advance to the next entry when the primary is exhausted
- [x] `[5]` Health tracking with cooldown; automatic return to the primary
- [x] `[6]` Policy gate interface, defaulting to the most restrictive behaviour
- [x] `[7]` Fail closed with a named reason when a hop is refused

## Phase 3 — Visibility

- [~] `[8]` Surface the active model (and that it is not the primary) in status output
      *(partial: `FailoverBackend.status()` reports primary / active / on_primary /
      chain / cooldown, but no headless status command exists to print it yet —
      that is 20260817-collective-status-command. Data side done, surface pending.)*
- [~] `[9]` Record failover events durably
      *(partial: every hop, refusal and recovery is emitted as a `FailoverEvent`
      through an `on_event` sink and logged at WARNING. The sink is NOT yet wired
      to the episode/audit record — deliberately left, so the durable-record shape
      is chosen once rather than guessed here.)*
- [ ] `[10]` Consider raising a proposal on sustained failover (open question 3)
      *(not implemented. It needs a definition of 'sustained' and an owner for the
      proposal, neither settled. The event stream already carries what such a rule
      would consume.)*

## Phase 4 — Verification

- [x] `[11]` Test: primary unreachable → work continues on secondary
- [x] `[12]` Test: primary recovers → work returns
- [x] `[13]` Test: fatal error (401) does **not** trigger failover
- [x] `[14]` Test: no chain configured → behaviour identical to today
- [~] `[15]` Live verification against a genuinely unavailable endpoint
      *(partial: verified against a genuinely closed port through the real
      `OpenAICompatBackend` — it raises `LLMCallError(retryable=True)` and the chain
      advances over real HTTP. Not yet exercised against a live provider outage.)*

