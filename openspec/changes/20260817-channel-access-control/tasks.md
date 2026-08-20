# Tasks: Access control on inbound channels

**Change ID:** 20260817-channel-access-control
**Branch:** `feat/channel-access-control`

---

## Phase 1 — Model

- [x] `[1]` Define requester identity, scope and permission levels
- [x] `[2]` Shared enforcement point where a channel message becomes a task
- [x] `[3]` Default-deny with a clear refusal message

## Phase 2 — Admission

- [~] `[4]` Pairing or allowlist flow, approval routed through oversight
      *(allowlist done: `acc-cli access admit` is an explicit operator action, recorded
      with who admitted whom. NOT yet routed through the oversight queue -- admitting
      already requires substrate-vouched operator authority, so the queue would add an
      approval step for someone who already has it. Worth revisiting if admission ever
      becomes delegable.)*
- [x] `[5]` Revocation effective without restart
- [x] `[6]` Attribution onto the task and the audit record

## Phase 3 — Conformance

- [x] `[7]` Apply to both existing channels
      *(Slack routes through the shared gate -- it carries EXTERNAL requesters nothing
      vouches for. The voice daemon is a LOCAL console process, so under the operator's
      answer its requester is the system principal the host already authenticated; it
      needs attribution, not an allowlist. Applying the same model to both, with the
      right answer for each.)*
- [x] `[8]` Conformance test any future channel adapter must pass
- [x] `[9]` Answer the identity question (open question 1) and record it

