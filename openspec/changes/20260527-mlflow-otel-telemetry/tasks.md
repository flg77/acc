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

## Phase 2 — Pipeline span tree (LANDED v0.3.18)

- [x] New helper `acc/backends/pipeline_tracing.py` — `task_span(...)`,
      `stage_span(...)`, `emit_stage(...)`, `set_span_attributes(...)`.
      No-op when `opentelemetry` isn't installed (lazy import + guard).
      Attribute dicts go through the GenAI semconv mapping from Phase 1.
- [x] Wrap `CognitiveCore.process_task` with a root span
      `acc.task.process` carrying task / role / collective / agent /
      operating_mode / model + GenAI operation = `chat`.  Delegates to
      a renamed `_process_task_body` for the existing pipeline logic
      (early returns inside the with-block still close the span
      correctly).
- [x] Emit child stage markers via `emit_stage(...)` at the six
      pipeline step boundaries: `acc.pipeline.gate_pre`,
      `acc.pipeline.memory_retrieve`, `acc.pipeline.prompt_build`,
      `acc.pipeline.llm_invoke`, `acc.pipeline.gate_post`,
      `acc.pipeline.persist`, `acc.pipeline.drift`.  Parenting is
      automatic via OTel's contextvar-backed current span.
- [x] Late-bind final attributes (`drift_score`, `cat_b_deviation_
      score`, `blocked`, `block_reason`, `latency_ms`) on the root
      span after the body returns so MLflow Trace UI sees the
      complete record.
- [x] Tests: `tests/test_pipeline_tracing.py` covers no-op fallback
      (no otel installed), semconv mapping on attributes, child-span
      emission, defensive swallowing of misbehaving exporters.
- [ ] (deferred to 2b) `ACC_TELEMETRY_SAMPLING` env for non-root span
      sampling — current emission is already tiny (one-shot markers,
      no payload) so sampling is a Phase 3 concern when the
      reasoning + tool-call payloads land.

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
