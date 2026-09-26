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
- [~] `[6]` Rotation without restarting unrelated agents
      *(2026-09-26: the runtime half is built -- a mounted Secret is read at call time by
      the openai-compatible backend, the MCP HTTP transports and the egress broker. The
      operator mounting it is Phase 2 of `20260926-secrets-from-kubernetes-and-a-live-broker`.)*

## Phase 3 — External source

- [x] `[7]` Source interface with the file-based implementation as reference
      *(2026-09-26: this was marked done, and no such interface was in the tree --
      every credential was an `os.environ.get` at its point of use. Built in
      `20260926-secrets-from-kubernetes-and-a-live-broker` as `acc/secret_source.py`.)*
- [x] `[8]` One external source, chosen per open question 1
      *(2026-09-26: the operator's answer -- ACC supports OpenBao/Vault, and for now uses
      Kubernetes Secrets. Built as a **mounted** Secret read at call time, which is also
      where OpenBao/Vault lands through ESO or the Vault Secrets Operator.
      `20260926-secrets-from-kubernetes-and-a-live-broker`.)*
- [x] `[9]` Test: no credential value reaches logs, audit records or status output

