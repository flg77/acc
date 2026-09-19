# 20260918-mlflow-shaped-spans — tasks

## Phase 1 (v0.17.16) — the conversation on the trace

### 1.1 Message serialisation
- [x] `acc/backends/genai_semconv.py`: `messages_json(messages, max_chars)` — the OTel GenAI
      message list (`role`, `parts[{type: text, content}]`), clipped per attribute with a
      `…[n more]` marker; `redacted_messages(messages)` keeps roles, replaces content.
- [x] Map `input_messages` → `gen_ai.input.messages`, `output_messages` → `gen_ai.output.messages`,
      `response_model` → `gen_ai.response.model`, `tool_arguments` → `gen_ai.tool.call.arguments`,
      `tool_result` → `gen_ai.tool.call.result`, `requester` → `user.id`, `session` → `session.id`.

### 1.2 LLM span
- [x] `acc/cognitive_core.py` `llm_invoke`: after `_call_llm`, set the input/output messages,
      response model, finish reasons and usage on the open stage span (`stage_span` returns it;
      today `emit_stage` closes immediately — the call moves inside the span).
- [x] `gen_ai.operation.name=chat` on the LLM span (already via `operation_name`).

### 1.3 Tool span
- [x] `acc/backends/pipeline_tracing.py` `tool_span`: accept `arguments`; the caller sets
      `tool_result` on the yielded span after the call (`set_span_attributes`).
- [x] `gen_ai.operation.name=execute_tool`.

### 1.4 Root span
- [x] `acc.task.process`: `gen_ai.operation.name=invoke_agent`, the request and the answer,
      `user.id`, `session.id`. As `gen_ai.input.messages` / `gen_ai.output.messages`, not
      `mlflow.spanInputs` / `mlflow.spanOutputs`: MLflow JSON-encodes every incoming OTLP
      attribute once more (`Span.from_otel_proto`), so a value set under its own key arrives
      double-encoded, while the GenAI keys go through its translator into those very fields.
- [x] **One turn, one trace** (found while implementing): the first pass, each tool call and
      the tool-result pass were each the root of their own trace, because the task loop runs
      them one after another. `acc.turn` (`TurnScope`, opened by the task loop after its
      routing filters, closed by the handler's wrapper on every exit) parents them.
- [x] `acc/compat`: the session already travelled (`X-ACC-Session` → `session_id`); the
      application's end user now does too (the standard `user` field → `end_user`). It names
      the trace's user and nothing else — the requester stays the key's principal.
- [ ] The lab app's bridge sends both (`dev_user_id`, the conversation id) — app-side change.

### 1.5 Policy
- [x] `ACC_TRACE_MESSAGES` (on/off), `ACC_TRACE_MESSAGES_MAX_CHARS` (8192) — read where the
      other telemetry knobs are (`acc/backends/genai_semconv.py`), not in `acc/config.py`.
- [x] Role schema: `telemetry.redact_messages: bool` (default false); redaction applied in 1.1.

### Verification
- [x] Unit: message JSON shape, cap + marker, redaction, identity propagation, usage on the
      LLM span, tool arguments/result.
- [x] Integration: one turn through the in-memory OTel exporter; assert the MLflow keys on
      root/LLM/tool spans (`tests/test_mlflow_shaped_spans.py`; a recording tracer runs the
      same assertions on hosts without the SDK).
- [ ] bb3: an ACC trace in the RHOAI MLflow workspace whose Summary shows Inputs/Outputs, whose
      LLM span shows the messages and `gpt-oss-120b`, whose tool span shows arguments/result,
      and whose Sessions entry is `user:<dev_user_id>` — the module-4 walk-through holds.
- [ ] Full sweep; lighthouse smoke.

## Phase 2 (deferred)
- [ ] `EVAL_OUTCOME` → MLflow assessments (`/api/3.0/mlflow/traces/{id}/assessments`).
- [ ] Cat-B data-class rule deciding redaction per span.

## Phase 3 (deferred)
- [ ] Role prompt hash as `mlflow.linkedPrompts`.
