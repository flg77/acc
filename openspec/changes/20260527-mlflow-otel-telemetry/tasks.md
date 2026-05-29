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

## Phase 3 — Collector + runbook (LANDED v0.3.19)

- [x] Sample standalone OTel Collector config
      `deploy/observability/otel-collector.yaml` fanning out OTLP →
      MLflow `/v1/traces` + Phoenix gRPC + Prometheus scrape + debug
      sink, with env-driven endpoints (`MLFLOW_OTEL_ENDPOINT`,
      `PHOENIX_OTEL_ENDPOINT`, …).
- [x] `docs/observability/mlflow.md` — "ship ACC telemetry to MLflow"
      runbook covering Path A (direct ACC → MLflow) and Path B
      (recommended ACC → Collector → MLflow + Phoenix), the expected
      trace shape, attribute key list, verification steps, and a
      troubleshooting section.
- [x] Operator: `OTelCollectorSpec` extended with
      - `Protocol` (`grpc` / `http/protobuf`, default `grpc`) matching
        the upstream `OTEL_EXPORTER_OTLP_PROTOCOL` env var,
      - `MLflowEndpoint` for optional in-Collector fan-out to MLflow.
- [x] Operator template `otel_config.go` renders an `otlphttp/mlflow`
      exporter on the traces pipeline when `MLflowEndpoint` is set;
      omits the section cleanly when unset (no syntactic noise).
- [x] Unit test `TestRenderOTelConfig_MLflowFanOut` asserts the
      fan-out shape; existing `TestRenderOTelConfig` extended to
      assert the section is absent when `MLflowEndpoint` is unset.
- [ ] (deferred to 3b) Operator injection of
      `OTEL_EXPORTER_OTLP_PROTOCOL` as a pod env var on agent
      Deployments — needed once operators want to route agents
      directly at MLflow (Path A) without a Collector.  Until then
      operators stick to gRPC (default) or override the env via a
      pod-template extension.

## Phase 4 — Eval + reasoning + tool spans (LANDED v0.3.20)

- [x] `pipeline_tracing.add_event(span, name, attrs)` attaches a named
      event to the root span; truncates `reasoning` text at
      `ACC_REASONING_EVENT_MAX_CHARS` (default 8192) and flags via
      `acc.reasoning_truncated` so the trace payload stays bounded.
- [x] Reasoning trace surfaced as `acc.reasoning` event on the root
      task span (when the role opted into reasoning externalisation).
- [x] `EVAL_OUTCOME` verdict surfaced as `acc.eval_outcome` event
      with `verdict` / `score` / `rationale` attributes — extracted
      via the same `acc.agent._extract_eval_outcome` helper the agent
      task loop uses for TASK_COMPLETE, so the span carries the same
      verdict downstream consumers see on NATS.
- [x] `pipeline_tracing.tool_span(name, *, server_id, skill_id)`
      child span — `acc.tool.invoke` span name, GenAI semconv
      `gen_ai.tool.name` + `gen_ai.tool.type` (`mcp` | `skill`),
      ACC-namespaced `acc.mcp.server_id` / `acc.skill.id` for the
      respective paths.  Always emitted (never sampled out — tool
      calls are the high-value trace evidence).
- [x] `CognitiveCore.invoke_skill` + `invoke_mcp_tool` wrapped in
      `tool_span` so each invocation lands as a child span under the
      root `acc.task.process`.
- [x] `ACC_TELEMETRY_SAMPLING` env (0.0 keep all, 1.0 drop all,
      clamped); applies to stage markers only — root span + tool
      spans are always emitted so the trace tree is never orphaned.
- [x] Tests (`tests/test_pipeline_tracing.py` extended to 21 cases):
      add_event no-op-on-None; truncation; short-pass-through;
      sampling clamp + drop + zero; tool_span no-op without otel;
      gen_ai.tool.* attribute mapping for MCP vs skill paths.
