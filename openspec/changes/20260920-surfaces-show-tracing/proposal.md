# 20260920-surfaces-show-tracing — proposal

## Why

Operator, 2026-09-20, on bb3 `wksp-user2`: *"What about the MLflow
integrations? I can't see the actual queries (including those from the
assistant or potential others). This is something we need to be able to
activate directly in this cluster scenario directly from the webgui or tui."*

Nothing was switched off. The corpus declares
`spec.observability.otelCollector` with `mlflowEndpoint`, `mlflowWorkspace:
wksp-user2`, `mlflowExperimentID: "2"`; message text has been on the spans by
default since 0.17.16 (`acc/backends/genai_semconv.py`
`trace_messages_enabled`). The newest underwriter turn is in the RHOAI MLflow
as one trace of 22 spans — question, system prompt, two model calls with
tokens, the `uw_queue_view` calls with their results, the answer.

Neither surface says any of it. The UI pods carry no tracing variable at all;
`acc/tui/screens/diagnostics.py` only knows `ACC_MLFLOW_TRACKING_URI` and
answers *"MLflow not configured"*; the audit and episode screens are refused
in a pod (no local store). An operator who does not already know the MLflow
workspace and experiment id has no way to learn them from ACC.

## What changes

### Phase 1 (this ship) — say where the turns go, read-only

* `acc.deployment.tracing(env=None) -> Tracing` beside `agentset()`, same two
  backends: a checkout reads `acc-config.yaml` (`observability.backend`) and
  the environment (`OTEL_EXPORTER_OTLP_ENDPOINT`, `ACC_MLFLOW_TRACKING_URI`,
  `ACC_TRACE_MESSAGES`); a cluster pod reads `AgentCorpus.spec.observability`
  and each agent's `extraEnv` for
  `ACC_TRACE_MESSAGES` — with the read-only ServiceAccount of operator 0.2.27.
  No new right is needed.
* `Tracing.summary()` — one sentence both surfaces lead with.
* `trace_messages_on(raw)` split out of `trace_messages_enabled()` so the
  agent that obeys the variable and the surface that reports it read a value
  the same way.
* TUI: Configuration gains a **Tracing** tab (read in a worker thread).
  Configuration, not Diagnostics: it is a setting of the deployment, and the
  switch of Phase 3 belongs beside it.
* WebGUI: `GET /api/tracing`; the SPA gains *Trace · Where the turns go*.

### Phases 2–N (deferred)

* **The last turns in the surfaces.** Checked on bb3: the UI ServiceAccount
  gets `403 PERMISSION_DENIED` from the RHOAI MLflow (`traces/search`). Reading
  needs a RoleBinding to RHOAI's MLflow integration ClusterRole for the UI
  ServiceAccount — a widening of what operator 0.2.27 deliberately kept
  narrow, so it needs the operator's word.
* **A link that opens MLflow.** The CR carries the in-cluster endpoint only;
  the browser address (on bb3 the RHOAI gateway) is declared nowhere ACC can
  read. Needs a field (or `mlflowTrackingUri` set to the browser address —
  which also turns on run logging from the UI pods, so not silently).
* **The switch** (vault KT-16 / KW-14): export on/off is a write to
  `AgentCorpus.spec.observability`; message text on/off is `ACC_TRACE_MESSAGES`
  on the agents or a bus signal. Waits for KT-00 §6 Q1/Q2 (who owns the CR;
  direct patch or proposal).
* One trace for a hand-off: the bus carries no trace context, so an
  assistant → persona route is two traces.

## Impact

* **Affected code:** `acc/deployment.py`, `acc/backends/genai_semconv.py`,
  `acc/tui/screens/configuration.py`, `acc/tui/help/configuration.md`,
  `acc/webgui/routes_read.py`, `webgui/src/{api/client.ts,tracing.tsx,App.tsx}`.
* **New env knobs:** none.
* **Tests:** `tests/test_deployment_tracing.py` (9).
* **Backward compatibility:** additive; `trace_messages_enabled()` unchanged
  in behaviour.

## What stays open after Phase 1

* Whether the collector is up is not shown: the CRD has
  `status.infrastructure.otelCollectorReady`, the operator never sets it, and
  the UI ServiceAccount may not read Deployments.

* A role's own `telemetry.redact_messages` is not shown (the role files of an
  installed pack are not on the UI pods) — the panel says a role may redact.
* In a checkout the panel reads this process's configuration; agents started
  with a different environment are not seen.
