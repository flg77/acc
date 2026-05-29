# Tasks — `20260527-mlflow-otel-telemetry`

## Phase 1 (LANDED v0.3.17)

- [x] Add OTLP/HTTP exporter selection via `OTEL_EXPORTER_OTLP_PROTOCOL`
      (`grpc` / `http/protobuf`) — lazy-import HTTP exporter package; unknown
      values warn + fall back to gRPC.
- [x] Helper `acc/backends/genai_semconv.py` — map `model`/`*tokens`/
      `operation`/`backend` to `gen_ai.*`; route ACC fields (`role`,
      `collective_id`, `task_id`, `eval_score`, `drift_score`, …) under
      `acc.*`; pin `GENAI_SEMCONV_VERSION`.
- [x] `OTelMetricsBackend.emit_span`/`emit_metric` apply the mapping before
      handing attributes to the SDK.
- [x] Optional `acc[mlflow]` extra pulling
      `opentelemetry-exporter-otlp-proto-http`.
- [x] Tests: 12 semconv mapping cases; 3 protocol-selection cases; 1 mapped-
      emission case (otel-gated, skip-when-otel-absent).

## Phase 2 — Pipeline span tree (proposed)

- [ ] In `acc/cognitive_core.py`, wrap the task pipeline in a root span
      (`task.process`) and child spans (`gate.pre`, `memory.retrieve`,
      `prompt.build`, `llm.invoke`, `gate.post`, `persist`).
- [ ] Thread the active span via `trace.get_current_span()` so the LLM
      backend can decorate `llm.invoke` with token/model attributes.
- [ ] Cardinality control: respect `ACC_TELEMETRY_SAMPLING` (env, 0.0–1.0)
      for non-root spans; reuse the reasoning-on-bus truncation.
- [ ] Tests: span tree shape against an in-memory exporter; sampling
      threshold; truncation bounds.

## Phase 3 — Collector + runbook (proposed)

- [ ] Sample OTel Collector config (`gitops/observability/otel-collector.
      yaml`) fanning out OTLP → MLflow `/v1/traces` + Phoenix.
- [ ] `docs/observability/mlflow.md` — "ship ACC telemetry to MLflow"
      runbook: env vars, Collector deploy, MLflow Trace UI walkthrough.
- [ ] Operator: `OTEL_EXPORTER_OTLP_PROTOCOL` / `OTEL_EXPORTER_OTLP_ENDPOINT`
      surfaced on `AgentCollectiveSpec.Observability`.

## Phase 4 — Eval + reasoning + tool spans (proposed)

- [ ] Map `EVAL_OUTCOME` to span events on the parent task span.
- [ ] Reasoning-trace → span event (with budget-aware truncation).
- [ ] MCP tool-calls → child spans under `llm.invoke` using `gen_ai.tool.*`
      attributes.
- [ ] Tests + runbook update.
