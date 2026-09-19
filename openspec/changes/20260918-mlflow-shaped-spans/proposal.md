# 20260918-mlflow-shaped-spans — proposal

## Why

The mortgage workshop puts an ACC agentset behind the same application the
original AgentOps lab runs on LangGraph, and module 4 of that lab walks the
attendee through MLflow's trace views: the trace **Summary** with its
*Inputs* (the user context: `user_id`, `user_email`, `user_name`, the
messages) and *Outputs*, the **ChatOpenAI was called** rows with model,
messages and choices, the **tool was called** rows with arguments and result,
the **Sessions** view grouping traces by `user:<id>`, and the token/cost
columns. On bb3 (2026-09-18, runtime 0.17.15, operator 0.2.22) an ACC turn
reaches MLflow as a trace of nine spans — `acc.task.process` →
`acc.pipeline.{prompt_build,memory_retrieve,gate_pre,context_budget,llm_invoke,
gate_post,drift,persist}` plus `acc.tool.invoke` — with the model on the LLM
span, token counts on `gate_post`, and nothing else: no request or response
text anywhere, no tool arguments or result, no user, no session. The Summary
tab is empty, the Sessions view has no entry, and the "inspect the LLM call"
step has nothing to inspect. Governance is on the trace (the original has no
`gate_pre` / `gate_post` / `drift`); the conversation is not.

MLflow's OTLP ingest already understands the OpenTelemetry GenAI semantic
conventions (`mlflow/tracing/otel/translation/genai_semconv.py`): span type
from `gen_ai.operation.name`, inputs/outputs from `gen_ai.input.messages` /
`gen_ai.output.messages` and `gen_ai.tool.call.arguments` / `.result`, model
from `gen_ai.request.model` / `gen_ai.response.model`, usage from
`gen_ai.usage.*`, and the trace's user and session from the span attributes
`user.id` and `session.id`. ACC's `acc.backends.genai_semconv` maps its
attributes into that namespace already — it just never had the messages,
the tool payloads or the identity to map.

## What changes

### Phase 1 (this ship — v0.17.16)

- **LLM span carries the call.** `acc.pipeline.llm_invoke` gets
  `gen_ai.operation.name=chat`, `gen_ai.input.messages` (system + user, as
  the OTel GenAI message JSON), `gen_ai.output.messages` (the assistant
  choice), `gen_ai.response.model`, `gen_ai.response.finish_reasons`, and the
  usage keys (`gen_ai.usage.input_tokens/output_tokens`) that today sit on
  `gate_post` (kept there too — the gate reads them).
- **Tool span carries the call.** `acc.tool.invoke` gets
  `gen_ai.operation.name=execute_tool`, `gen_ai.tool.call.arguments`,
  `gen_ai.tool.call.result` (JSON), next to the existing `gen_ai.tool.name`
  and type.
- **Root span carries the turn and the identity.** `acc.task.process` gets
  `gen_ai.operation.name=invoke_agent`, `mlflow.spanInputs` (the task's
  request text and the caller context) and `mlflow.spanOutputs` (the answer)
  so MLflow derives the trace's Inputs/Outputs, plus `user.id` and
  `session.id` from the task payload's `requester` / `session` fields. The
  compat endpoint (`acc/compat`) fills those from the application's user
  and chat-session identifiers (the lab app sends `dev_user_id` and a
  session id per conversation).
- **One cap and one switch.** `ACC_TRACE_MESSAGES_MAX_CHARS` (default 8192,
  per attribute; longer text is clipped with a `…[n more]` marker) and
  `ACC_TRACE_MESSAGES=off|on` (default `on`); a role's
  `telemetry.redact_messages: true` turns the message and tool payloads into
  `<redacted>` for that role while keeping the shape.

### Phases 2–3 (deferred)

- Phase 2: assessments — the runtime's `EVAL_OUTCOME` verdicts as MLflow
  assessments on the trace (`/api/3.0/mlflow/traces/{id}/assessments`), so
  the *Quality* tab and the Judges module see ACC's own scoring.
- Phase 3: prompt versions — the role's prompt hash as
  `mlflow.linkedPrompts` so the *Prompts & versions* view links a trace to
  the role definition that produced it.

## Impact

- **Affected code:** `acc/cognitive_core.py` (root/llm_invoke attribute
  sites), `acc/backends/pipeline_tracing.py` (`tool_span` payloads,
  message serialisation helper), `acc/backends/genai_semconv.py` (new keys),
  `acc/compat/*` (requester/session into the task payload), role schema
  (`telemetry.redact_messages`).
- **New env knobs:** `ACC_TRACE_MESSAGES`, `ACC_TRACE_MESSAGES_MAX_CHARS`.
- **Tests:** unit tests on the attribute builders (messages JSON shape, cap
  and marker, redaction, identity propagation), one integration test that
  renders a turn through the OTel in-memory exporter and asserts the MLflow
  keys; the bb3 proof is a trace whose Summary shows inputs/outputs and
  whose Sessions entry is `user:<dev_user_id>`.
- **Backward compatibility:** additive attributes; nothing existing is
  renamed. Collectors that do not know the keys ignore them. The message
  text makes spans larger — the cap bounds it; `ACC_TRACE_MESSAGES=off`
  restores today's shape.
- **OpenShell:** unaffected — spans describe the cognitive pipeline, not the
  sandbox; the tool span already exists.

## What stays open after Phase 1

- The RHOAI MLflow instance authenticates callers by ServiceAccount token
  and scopes traces by workspace; the collector-side headers and token are
  operator work (0.2.23 — `otelCollector.mlflowWorkspace`,
  `otelCollector.mlflowAuth`), not runtime work.
- Message text on spans is evidence: the per-role redaction is the only
  policy in Phase 1; a Cat-B rule that decides redaction per data class is
  Phase 2 material together with assessments.
- Cost columns need `gen_ai.provider.name` and a price table MLflow knows;
  the MaaS gateway's models are not in it.
