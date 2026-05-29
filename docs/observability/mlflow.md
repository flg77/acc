# Ship ACC telemetry to MLflow

> OpenSpec [`20260527-mlflow-otel-telemetry`](../../openspec/changes/20260527-mlflow-otel-telemetry/proposal.md)
> Phase 3 runbook.  Phases 1 + 2 (the OTLP/HTTP exporter, the GenAI
> semconv mapping, and the cognitive-pipeline span tree) landed in
> v0.3.17 / v0.3.18 — this doc shows operators how to actually point
> those at MLflow.

## What you get in MLflow

Every prompt produces one trace in MLflow's Trace UI shaped like:

```
acc.task.process               [acc.role, gen_ai.request.model,
                                 acc.drift_score, acc.latency_ms, …]
 ├─ acc.pipeline.gate_pre
 ├─ acc.pipeline.memory_retrieve [acc.episodes_count, acc.notes_count]
 ├─ acc.pipeline.prompt_build
 ├─ acc.pipeline.llm_invoke      [gen_ai.request.model,
                                  gen_ai.usage.input_tokens,
                                  gen_ai.usage.output_tokens]
 ├─ acc.pipeline.gate_post
 ├─ acc.pipeline.persist
 └─ acc.pipeline.drift           [acc.drift_score]
```

Attributes follow the
[OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) —
MLflow recognises `gen_ai.*` natively and renders the right widgets
(model name, token counts, operation type).  ACC-specific fields are
namespaced under `acc.*` so they sort together in the attribute panel
and don't collide with upstream semconv evolution.

## Two paths

There are two supported topologies.  Pick one based on whether you
already run a Collector.

### Path A — direct ACC → MLflow

Simplest path.  No Collector.  Each ACC agent exports OTLP/HTTP
directly to MLflow's `/v1/traces` endpoint.

```bash
# On each ACC agent host:
pip install 'acc[mlflow]'   # pulls opentelemetry-exporter-otlp-proto-http
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=https://mlflow.example.com
# acc-config.yaml: observability.backend = "otel"
```

ACC agents will POST to `${OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces`
and `${OTEL_EXPORTER_OTLP_ENDPOINT}/v1/metrics` on the 15-second
metric flush + per-task trace boundaries.

Pros: fewest moving parts.
Cons: no fan-out (no Phoenix dashboard side-by-side); MLflow outage
takes the ACC OTel pipeline down (logged, doesn't block agents).

### Path B — ACC → OTel Collector → MLflow + Phoenix (recommended)

For a Kagenti-aligned deployment that wants the MLflow trace UI and
the Phoenix runtime view in parallel.  Run the Collector once, fan out
to both backends.

```bash
# Standalone Collector (compose / podman):
podman run --rm -d \
  --name otel-collector \
  -v $(pwd)/deploy/observability/otel-collector.yaml:/etc/otelcol/config.yaml:Z \
  -e MLFLOW_OTEL_ENDPOINT=https://mlflow.example.com \
  -e MLFLOW_TLS_INSECURE=false \
  -e PHOENIX_OTEL_ENDPOINT=phoenix:4317 \
  -e PHOENIX_TLS_INSECURE=true \
  -p 4317:4317 -p 4318:4318 -p 8889:8889 \
  docker.io/otel/opentelemetry-collector-contrib:0.108.0

# On each ACC agent host — gRPC to the Collector (cheaper than HTTP):
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

The sample Collector config lives at
[`deploy/observability/otel-collector.yaml`](../../deploy/observability/otel-collector.yaml).
It receives OTLP on 4317/4318, batches with a memory limiter, and
fans out to MLflow (OTLP/HTTP), Phoenix (OTLP/gRPC), and a Prometheus
scrape endpoint on 8889.

## Verifying the trace lands

1. `acc-deploy.sh up` (or your equivalent) — agents start and register.
2. From the TUI Prompt screen, send a one-line prompt.
3. Open the MLflow UI → **Traces** tab. You should see one
   `acc.task.process` row per task with the seven child spans
   indented underneath.
4. Click the trace → the **Attributes** panel shows
   `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
   `acc.role`, `acc.drift_score`, `acc.collective_id`, etc.

If you see no traces:

- Confirm the Collector / MLflow endpoint is reachable from the agent
  pod: `curl -v ${OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces` should not
  timeout.
- Check `acc-config.yaml`: `observability.backend: otel` (not `log`).
- For Path A: confirm `opentelemetry-exporter-otlp-proto-http` is
  installed (`pip show opentelemetry-exporter-otlp-proto-http`).
- Tail the agent logs for `metrics_otel:` warnings — a typo'd
  `OTEL_EXPORTER_OTLP_PROTOCOL` warns and falls back to gRPC, which
  won't reach MLflow if MLflow only listens on HTTP.

## Operator-managed deployments

When an `AgentCorpus` CR drives the deployment, the operator's
`OTelCollectorSpec` already exposes `endpoint` + `tlsInsecure` + a
rendered Collector config.  See
[`operator/api/v1alpha1/agentcorpus_types.go`](../../operator/api/v1alpha1/agentcorpus_types.go)
for the schema.  The operator currently does not yet inject
`OTEL_EXPORTER_OTLP_PROTOCOL` as a pod env var (Phase 3b backlog) —
until that lands, set the env via a corpus-level pod template
override or stick to gRPC (the default; works against the
operator-rendered Collector unchanged).

## Cardinality + sampling

Phase 2 stage markers are tiny.  Phase 4 added two more high-value
surfaces:

- **`acc.reasoning` span event** on the root span, carrying the
  agent's externalised reasoning block (when the role opted in).
  Clipped at `ACC_REASONING_EVENT_MAX_CHARS` (default 8192) and
  flagged via `acc.reasoning_truncated` so a runaway chain-of-thought
  can't blow up the trace payload.
- **`acc.eval_outcome` span event** with `verdict` / `score` /
  `rationale` attributes — populated when the LLM emits a structured
  `eval_outcome` (the same one the TASK_COMPLETE envelope carries).
- **`acc.tool.invoke` child spans** under the root for every Skill
  invocation and every MCP tool call.  Attributes follow the OTel
  GenAI semconv: `gen_ai.tool.name`, `gen_ai.tool.type` (`mcp` |
  `skill`), plus ACC namespaced `acc.mcp.server_id` /
  `acc.skill.id` for the respective paths.

`ACC_TELEMETRY_SAMPLING` (env, 0.0–1.0) gates **stage markers only** —
`acc.pipeline.*` children may be dropped at high agent volume.  The
root `acc.task.process` span and the `acc.tool.invoke` children are
always emitted so the trace tree never has orphans.  Default 0.0
keeps everything.

## Related

- [Phase 1 — OTLP/HTTP exporter + GenAI semconv](../../openspec/changes/20260527-mlflow-otel-telemetry/proposal.md)
- [Phase 2 — pipeline span tree](../../openspec/changes/20260527-mlflow-otel-telemetry/proposal.md)
- [Sample Collector config](../../deploy/observability/otel-collector.yaml)
- OTel GenAI semconv: https://opentelemetry.io/docs/specs/semconv/gen-ai/
- MLflow OTLP ingest: https://mlflow.org/docs/latest/tracing/otel.html
