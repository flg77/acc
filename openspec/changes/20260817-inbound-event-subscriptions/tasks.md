# Tasks: Inbound event subscriptions that create governed work

**Change ID:** 20260817-inbound-event-subscriptions
**Branch:** `feat/inbound-event-subscriptions`

---

## Phase 1 — Ingress

- [ ] `[1]` Subscription store: match, template, secret, target role, budget
- [ ] `[2]` Signature verification; reject before any processing
- [ ] `[3]` Rate limiting with a defined behaviour at the limit

## Phase 2 — Task creation

- [ ] `[4]` Render the template; attach payload as untrusted data
- [ ] `[5]` Attribution and budget accounting per subscription
- [ ] `[6]` Gated-action policy (open question 2)

## Phase 3 — Operate

- [ ] `[7]` `list` / `test` / `remove` without restart
- [ ] `[8]` Test: embedded instruction in a payload is not obeyed

