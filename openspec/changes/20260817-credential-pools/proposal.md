# Proposal: Credential pools — several keys per provider with cooldown

**Change ID:** 20260817-credential-pools
**Date:** 2026-08-17
**Status:** Implemented
**Author:** flg

---

## Problem Statement

A model binding names exactly one environment variable holding exactly one
credential, resolved once at agent boot. If that key is rate-limited, revoked or
rotated, every role bound to it stops working until an operator edits the
environment file and restarts the affected agents.

This is not hypothetical. A shared provider gateway returned 429 to a live
deployment under load; the task survived only because a retry happened to land
inside the backoff window. With more than one credential available the throttled
key could simply have stepped aside.

Rotation is the second, quieter cost. Replacing a key today means editing the
environment file and restarting every agent that uses it — an outage to perform
routine hygiene, which is a good way to ensure the hygiene does not happen.

## Current Behavior

`api_key_env` in the model registry names a single variable. The whole
environment file is injected into every agent container, and the value is read at
boot. There is no pool, no cooldown, no per-key health, and no way to add or
retire a credential without a restart.

## Desired Behavior

A provider (or model) may have several credentials available, used in
rotation, with a key that fails moved to cooldown rather than taken as a hard
failure.

    acc-cli auth list [<provider>]
    acc-cli auth add <provider> --env <VAR>
    acc-cli auth remove <provider> <index>
    acc-cli auth status <provider>
    acc-cli auth reset <provider>        # clear cooldowns

Three properties matter:

* **Names only.** The pool holds variable *names* and health state. Values stay
  where they are; nothing in this change reads, prints, stores or copies a
  credential value.
* **Cooldown, not exclusion.** A key that 429s is rested and retried later, not
  removed. A key that 401s is a configuration fault and should be reported, not
  silently rotated past.
* **Observable.** `status` must show which credentials are healthy, which are
  cooling down and why, because a pool that silently masks a dead key is how an
  operator discovers at renewal time that only one of four ever worked.

## Success Criteria

- With two credentials configured and one rate-limited, work continues on the
  other without operator action.
- A rested credential is retried after its cooldown and returns to service.
- A `401` is surfaced as a fault rather than absorbed by rotation.
- `status` distinguishes healthy / cooling / faulted per credential.
- A single-credential configuration behaves exactly as today.

## Scope

**In scope**

- Pools of credential **names** per provider or model, with health and cooldown.
- Selection at call time rather than only at boot.
- Distinguishing retryable throttling from fatal authentication faults.
- Operator visibility into pool state.

**Out of scope**

- Reading credentials from an external secret manager — related and separate.
- Reducing which agents see which credentials — a scoping concern, separate.
- Cross-provider selection; that is the failover change.

## Implementation options

**A. Pool inside the model registry entry.** `api_key_env` becomes a list. Small
change, obvious semantics, but ties pool identity to a model rather than to the
provider that actually enforces the limit.

**B. A provider-level credential store** keyed by provider, with model entries
referencing it. Matches how quota is usually enforced and lets several models
share a pool.

**C. Both** — provider-level pools with a per-model override for the case where
one model has its own credential.

Deployments observed in practice use distinct keys per *model* on the same
gateway, which argues against pure B. C is the recommendation, with A as the
degenerate case.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Is a pool per provider, per model, or both? Real deployments have been seen
   doing per-model, which the obvious design would not predict.
2. Where does cooldown state live — in-process per agent, or shared? Shared state
   means one agent's discovery helps the others; in-process is simpler and
   duplicates the learning N times.
3. Should exhausting a pool trigger the failover chain, or fail? They compose,
   and the order matters.

## Assumptions

- Credential values continue to arrive through the environment; this change does
  not alter how they get there.
- Backends can distinguish throttling from authentication failure.
