# Tasks: Inbound event subscriptions that create governed work

**Change ID:** 20260817-inbound-event-subscriptions
**Branch:** `feat/inbound-event-subscriptions`

---

## Phase 1 — Ingress

- [x] `[1]` Subscription store: match, template, secret, target role, budget
- [x] `[2]` Signature verification; reject before any processing
- [x] `[3]` Rate limiting with a defined behaviour at the limit

## Phase 2 — Task creation

- [x] `[4]` Render the template; attach payload as untrusted data
- [x] `[5]` Attribution and budget accounting per subscription
- [x] `[6]` Gated-action policy (open question 2)
      *(a subscription-created task is gated EXACTLY as any other task is. It carries
      requester attribution and no elevated tier, so a gated action inside it still
      requires approval. Anything else would make a webhook the way to obtain
      approval-free execution -- the same reasoning as objectives.)*

## Phase 3 — Operate

- [~] `[7]` `list` / `test` / `remove` without restart
      *(create/remove/load are implemented and take effect per event -- no restart. A
      `acc-cli subscriptions` surface is NOT built; the module is the API a receiver
      endpoint calls.)*
- [x] `[8]` Test: embedded instruction in a payload is not obeyed

