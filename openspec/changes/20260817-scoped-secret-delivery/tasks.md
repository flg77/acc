# Tasks: Scoped secret delivery to agents

**Change ID:** 20260817-scoped-secret-delivery
**Branch:** `feat/scoped-secret-delivery`

---

## Phase 1 — Derive

- [x] `[1]` Compute per-role credential requirements from bindings and capabilities
- [x] `[2]` Report the derived set per role for operator review
- [~] `[3]` Identify capabilities whose needs are not derivable (open question 2)
      *(named as a limit rather than enumerated: derivation sees model bindings, so a
      skill with its own credential is invisible. ACC_SECRET_ALLOWLIST is the escape
      hatch, and `secrets scope` previews what would be removed so the gap is visible
      before enforcement is switched on.)*

## Phase 2 — Deliver

- [x] `[4]` Inject only the derived set per agent
- [ ] `[5]` Verify on a running deployment that agents hold only what they need
      *(not run on a live deployment. Verified in-process: with enforcement on, a role
      bound to one provider retains its own key and loses the other three.)*
- [ ] `[6]` Rotation without restarting unrelated agents
      *(not implemented. Scoping is applied at boot; rotation without restart needs the
      on-demand source in [8], which is the optional half.)*

## Phase 3 — External source

- [x] `[7]` Source interface with the file-based implementation as reference
- [ ] `[8]` One external source, chosen per open question 1
      *(not implemented -- the optional half. The change is explicit that the scoping
      half does not require it.)*
- [x] `[9]` Test: no credential value reaches logs, audit records or status output

