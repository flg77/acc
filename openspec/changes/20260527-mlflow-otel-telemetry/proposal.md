# OpenSpec — MLflow agent telemetry via OTel GenAI semconv

| Field | Value |
|---|---|
| Change ID | `20260527-mlflow-otel-telemetry` |
| Target version | `v0.4.0` (proposed) — Phase 1 in `v0.3.17`, Phase 2 in `v0.3.18` |
| Status | Phases 1 + 2 LANDED; Phases 3–4 proposed |
| Depends on | OTel observability backend (`acc/backends/metrics_otel.py`), PR-R (token usage), reasoning-trace |
| Notes mirror | `Notes/Development/AgenticCellCorpus/ACC Openspec/20260527-mlflow-otel-telemetry — OpenSpec (proposed).md` |

## Problem statement

ACC already emits OpenTelemetry **spans + gauges** via OTLP **gRPC** to
`OTEL_EXPORTER_OTLP_ENDPOINT` (`acc/backends/metrics_otel.py`). MLflow 3.6+ — the
RHOAI-standard GenAI tool, also used alongside Kagenti's Phoenix — ingests OTel
traces through its OTLP **`/v1/traces` (HTTP)** endpoint and recognises the
**OpenTelemetry GenAI Semantic Conventions** (`gen_ai.*`). Two mismatches kept
ACC's rich telemetry (per-task spans, reasoning traces, `EVAL_OUTCOME` scores,
drift, token usage) from landing cleanly in MLflow:

1. ACC exported **gRPC-only** (no HTTP path for MLflow's endpoint).
2. ACC's span/metric attributes were **ad-hoc** (e.g. `model`, `role`), not
   `gen_ai.*` semconv keys.

## Proposed approach (full proposal)

- **OTLP/HTTP exporter option** — add an HTTP exporter path (env-selected: gRPC
  `:4317` vs HTTP `/v1/traces` on `:4318`) so ACC can target MLflow's endpoint
  directly, or fan out through a shared **OTel Collector** to MLflow (eval/trace
  UI) **and** Phoenix (Kagenti runtime view).
- **GenAI semantic-convention mapping** — label the existing telemetry with
  `gen_ai.*` (`gen_ai.request.model`, `gen_ai.usage.input_tokens` /
  `output_tokens`, `gen_ai.operation.name`, …), plus ACC-specific attributes
  (role, collective_id, task_id, eval score, drift) as `acc.*` namespaced extras.
- **Trace shape** — emit a span per task with child spans for the pipeline
  stages (gate → retrieve → prompt → LLM → post-gate → persist) so MLflow's
  Trace UI shows the agent's full step tree, including the reasoning block.
- **Config** — surface the exporter protocol + endpoint via
  `OTEL_EXPORTER_OTLP_PROTOCOL` / `OTEL_EXPORTER_OTLP_ENDPOINT` env (matches
  the OTel spec env vars), defaulting to current behaviour (opt-in HTTP/MLflow).

## Phasing

| Phase | Scope | Status |
|---|---|---|
| **1** | OTLP/HTTP exporter switch + `gen_ai.*`/`acc.*` attribute mapping | **LANDED v0.3.17** |
| 2 | Pipeline span tree — per-task root span + child spans for gate/retrieve/prompt/LLM/post-gate/persist | proposed |
| 3 | Sample OTel Collector config fanning out to MLflow `/v1/traces` + Phoenix; runbook | proposed |
| 4 | Eval-outcome + reasoning-trace span events; `gen_ai.tool.*` for MCP tool-calls | proposed |

## Phase 1 — what landed

- New helper `acc/backends/genai_semconv.py` — `build_genai_attributes(raw,
  operation=None)` translates ACC's ad-hoc attribute dicts into `gen_ai.*` keys
  (`model` → `gen_ai.request.model`, `input_tokens` →
  `gen_ai.usage.input_tokens`, …) and routes ACC-specific fields (`role`,
  `collective_id`, `task_id`, `eval_score`, `drift_score`, …) under the
  `acc.*` namespace. Semconv version pinned in `GENAI_SEMCONV_VERSION`.
- `acc/backends/metrics_otel.py` — added `_resolve_exporters(endpoint)` that
  reads `OTEL_EXPORTER_OTLP_PROTOCOL` (`grpc` / `http/protobuf`). HTTP
  exporters are lazy-imported so the gRPC default keeps working without the
  extra dep. Unknown values warn + fall back to gRPC so a typo can't silently
  disable telemetry. `emit_span` / `emit_metric` now run the attribute dict
  through the semconv mapping before handing it to the SDK.
- New optional `pip install 'acc[mlflow]'` extra pulls
  `opentelemetry-exporter-otlp-proto-http`.
- Tests: `tests/test_genai_semconv.py` (12 cases — pure-Python, runs
  everywhere); `tests/test_backends_metrics.py::TestOTLPProtocolSelection`
  (3 cases — gRPC default, HTTP selection, unknown-protocol fallback) and a
  new `test_emit_span_applies_genai_semconv_mapping` confirming the OTel
  backend passes the mapped keys to `span.set_attribute`.

## Out of scope (full proposal)

- Replacing the `log` metrics backend default (this is an OTel-backend
  enhancement).
- MLflow experiment/run management or model registry — telemetry/tracing only.
- Phoenix-specific dashboards (Collector fan-out makes them free).

## Risks

- **Semconv churn** — the OTel GenAI semantic conventions are still maturing;
  the helper pins `GENAI_SEMCONV_VERSION` and keeps ACC-specific attributes
  under `acc.*` so an upstream rename doesn't break ingestion. Bump the
  constant + the mapping in one place when adopting a newer release.
- **Cardinality** — per-step spans × many agents can be heavy; reuse the
  existing reasoning-on-bus truncation + sampling. Lands with Phase 2.
- **gRPC↔HTTP parity** — both exporters now go through the same `_resolve_
  exporters` selector and the same semconv-mapped attribute path, so a Phase 2
  span tree will behave identically across protocols.

## Linked scope & risk analyses (notes vault)

- Scope: `[[MLflow scope — log metrics backend default retained]]` ·
  `[[MLflow scope — no run-experiment-registry management]]` ·
  `[[MLflow scope — Phoenix dashboards]]`
- Risks: `[[MLflow risk — GenAI semconv churn]]` ·
  `[[MLflow risk — telemetry cardinality]]` ·
  `[[MLflow risk — gRPC HTTP exporter parity]]`

## Verification (Phase 1)

- `pytest tests/test_genai_semconv.py tests/test_backends_metrics.py` — 17
  pass on a host without `opentelemetry`; 7 OTel-gated tests skip cleanly.
  In CI / containers where the dep is present all 24 run.
- A `git diff` review shows: one new helper module, one revised exporter
  module, one new optional extra, two test files extended. No call-site
  changes — Phase 2 wires the pipeline span tree into agent/cognitive_core.

## What stays open after Phase 1

- Phase 2 wires the per-task root span + child spans into the cognitive-core
  pipeline. Until it lands, MLflow will see `agent.register` /
  `agent.heartbeat` spans (now semconv-labelled) but not the full step tree.
- Phase 3 ships the sample Collector config + the "ship ACC telemetry to
  MLflow" runbook.
- Phase 4 maps reasoning-trace + EVAL_OUTCOME + MCP tool-calls onto span
  events / `gen_ai.tool.*` attributes.
