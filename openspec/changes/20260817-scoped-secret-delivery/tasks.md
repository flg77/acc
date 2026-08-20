# Tasks: Scoped secret delivery to agents

**Change ID:** 20260817-scoped-secret-delivery
**Branch:** `feat/scoped-secret-delivery`

---

## Phase 1 — Derive

- [ ] `[1]` Compute per-role credential requirements from bindings and capabilities
- [ ] `[2]` Report the derived set per role for operator review
- [ ] `[3]` Identify capabilities whose needs are not derivable (open question 2)

## Phase 2 — Deliver

- [ ] `[4]` Inject only the derived set per agent
- [ ] `[5]` Verify on a running deployment that agents hold only what they need
- [ ] `[6]` Rotation without restarting unrelated agents

## Phase 3 — External source

- [ ] `[7]` Source interface with the file-based implementation as reference
- [ ] `[8]` One external source, chosen per open question 1
- [ ] `[9]` Test: no credential value reaches logs, audit records or status output

