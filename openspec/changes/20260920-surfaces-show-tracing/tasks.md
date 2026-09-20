# 20260920-surfaces-show-tracing — tasks

## Phase 1 — say where the turns go, read-only

### 1.1 The read seam
- [x] `acc.deployment.Tracing` + `tracing(env=None)`; checkout backend (config + environment),
      cluster backend (`AgentCorpus.spec.observability`,
      agents' `ACC_TRACE_MESSAGES`, this pod's corpus only).
- [x] `Tracing.summary()`; a refused or failed read is an `errors` entry.
- [x] `trace_messages_on(raw)` shared by the runtime and the report.

### 1.2 Surfaces
- [x] TUI: Configuration → Tracing tab, read in a worker thread; help page.
- [x] WebGUI: `GET /api/tracing`; SPA *Trace · Where the turns go*.

### Verification
- [x] `tests/test_deployment_tracing.py` — cluster backend against the fake Kubernetes API
      (bb3's block, a role declared off, log backend, no MLflow, 403), checkout backend (env,
      default), the TUI tab (pilot), the WebGUI handler.
- [x] `tsc --noEmit` on the SPA.
- [ ] a cluster: bb3 `wksp-user2` — both surfaces say *workspace wksp-user2, experiment id 2,
      with its message text*.

## Phase 2 (deferred)
- [ ] The last turns (needs MLflow read for the UI ServiceAccount — the operator's word).
- [ ] A browser link to the experiment (needs a declared address).

## Phase 3 (deferred)
- [ ] The switch — after KT-00 §6 Q1/Q2.
