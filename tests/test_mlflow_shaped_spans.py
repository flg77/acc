"""OpenSpec ``20260918-mlflow-shaped-spans`` — the conversation on the trace.

What MLflow's OTLP ingest reads (``mlflow/tracing/otel/translation/genai_semconv.py``
and ``SqlAlchemyStore.log_spans``, 3.14):

* span type from ``gen_ai.operation.name`` (``invoke_agent`` / ``chat`` / ``execute_tool``);
* Inputs / Outputs from ``gen_ai.input.messages`` / ``gen_ai.tool.call.arguments`` and
  ``gen_ai.output.messages`` / ``gen_ai.tool.call.result``;
* the model from ``gen_ai.response.model`` / ``gen_ai.request.model``, usage from
  ``gen_ai.usage.*``;
* the trace's user and session from ``user.id`` / ``session.id`` on any span.

These tests run without the OTel SDK: a recording tracer stands in for it, so
the nesting and the attributes are asserted on every host.  The last test runs
the same turn through the real in-memory exporter where the SDK is installed.
"""

from __future__ import annotations

import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.backends import genai_semconv, pipeline_tracing
from acc.backends.genai_semconv import (
    REDACTED,
    build_genai_attributes,
    clip_text,
    messages_json,
    payload_json,
)
from acc.cognitive_core import CognitiveCore, _trace_identity
from acc.compat_endpoint import parse_request
from acc.config import RoleDefinitionConfig


# ---------------------------------------------------------------------------
# A recording tracer — spans, their attributes and their parents
# ---------------------------------------------------------------------------

class _Span:
    def __init__(self, name: str, parent: "_Span | None") -> None:
        self.name = name
        self.parent = parent
        self.attributes: dict = {}
        self.ended = False

    def set_attribute(self, key, value) -> None:
        self.attributes[key] = value

    def add_event(self, name, attributes=None) -> None:  # pragma: no cover
        pass


class _Tracer:
    def __init__(self) -> None:
        self.spans: list[_Span] = []
        self._stack: list[_Span] = []

    @contextlib.contextmanager
    def start_as_current_span(self, name: str):
        span = _Span(name, self._stack[-1] if self._stack else None)
        self.spans.append(span)
        self._stack.append(span)
        try:
            yield span
        finally:
            self._stack.pop()
            span.ended = True

    def named(self, name: str) -> list[_Span]:
        return [s for s in self.spans if s.name == name]


@pytest.fixture
def tracer(monkeypatch):
    t = _Tracer()
    monkeypatch.setattr(pipeline_tracing, "_get_tracer", lambda: t)
    monkeypatch.delenv("ACC_TRACE_MESSAGES", raising=False)
    monkeypatch.delenv("ACC_TRACE_MESSAGES_MAX_CHARS", raising=False)
    return t


def _core(content: str = "App #501 – Lisa Washington") -> CognitiveCore:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "content": content,
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    })
    llm.embed = AsyncMock(return_value=[0.05] * 384)
    llm.last_response_meta = {
        "request_model": "gpt-oss-120b",
        "response_model": "openai/gpt-oss-120b",
        "finish_reason": "stop",
    }
    vector = MagicMock()
    vector.insert.return_value = 1
    return CognitiveCore(
        agent_id="underwriter-1", collective_id="mortgage-agents",
        llm=llm, vector=vector, redis_client=None, role_label="mortgage_underwriter",
    )


def _role(**kwargs) -> RoleDefinitionConfig:
    base = {"purpose": "Underwrite.", "persona": "concise", "version": "0.1.0"}
    base.update(kwargs)
    return RoleDefinitionConfig.model_validate(base)


def _texts(attr_value: str) -> list[tuple[str, str]]:
    return [(m["role"], m["parts"][0]["content"]) for m in json.loads(attr_value)]


# ---------------------------------------------------------------------------
# 1.1 serialisation
# ---------------------------------------------------------------------------

def test_messages_json_is_the_otel_genai_message_list():
    value = messages_json([("system", "You underwrite."), ("user", "Queue?")])
    assert json.loads(value) == [
        {"role": "system", "parts": [{"type": "text", "content": "You underwrite."}]},
        {"role": "user", "parts": [{"type": "text", "content": "Queue?"}]},
    ]


def test_messages_json_drops_empty_messages():
    assert json.loads(messages_json([("system", ""), ("user", "hi")])) == [
        {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
    ]


def test_each_text_is_clipped_on_its_own_with_a_count():
    value = messages_json([("system", "s" * 50), ("user", "u" * 12)], max_chars=10)
    system, user = _texts(value)
    assert system == ("system", "s" * 10 + "…[40 more]")
    # the long system prompt did not push the user's turn off the span
    assert user == ("user", "u" * 10 + "…[2 more]")


def test_clip_text_leaves_short_text_alone():
    assert clip_text("short", 10) == "short"


def test_the_cap_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("ACC_TRACE_MESSAGES_MAX_CHARS", "4")
    assert clip_text("abcdefgh") == "abcd…[4 more]"
    monkeypatch.setenv("ACC_TRACE_MESSAGES_MAX_CHARS", "nonsense")
    assert genai_semconv.messages_max_chars() == 8192


def test_redaction_keeps_the_shape():
    value = messages_json([("system", "secret policy"), ("user", "ssn 123")], redact=True)
    assert _texts(value) == [("system", REDACTED), ("user", REDACTED)]


def test_payload_json_is_always_a_json_document():
    assert json.loads(payload_json({"application_id": 501})) == {"application_id": 501}
    assert json.loads(payload_json("plain text")) == "plain text"
    assert json.loads(payload_json({"x": 1}, redact=True)) == REDACTED
    clipped = json.loads(payload_json({"rows": "r" * 100}, max_chars=20))
    assert isinstance(clipped, str) and clipped.endswith("more]")


def test_new_keys_map_into_the_namespaces_mlflow_reads():
    mapped = build_genai_attributes({
        "input_messages": "[]", "output_messages": "[]",
        "response_model": "openai/gpt-oss-120b",
        "tool_arguments": "{}", "tool_result": "{}",
        "user_id": "underwriter-demo", "session": "conv-1",
        "agent_name": "mortgage_underwriter",
    })
    assert set(mapped) == {
        "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.response.model",
        "gen_ai.tool.call.arguments", "gen_ai.tool.call.result",
        "user.id", "session.id", "gen_ai.agent.name",
    }


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def test_the_end_user_names_the_trace_user_and_the_named_session_its_session():
    assert _trace_identity({
        "end_user": "underwriter-demo", "session_id": "conv-1",
        "requested_by": "compat:mortgage-app",
    }) == {"user_id": "underwriter-demo", "session": "conv-1"}


def test_without_an_end_user_the_admitted_requester_is_the_user():
    assert _trace_identity({"requested_by": "tui:flg"}) == {"user_id": "tui:flg"}


def test_an_unattributed_one_turn_task_gets_neither():
    # no session per task: MLflow's Sessions view lists what a client named
    assert _trace_identity({"task_id": "t1"}) == {}


def test_the_compat_request_carries_the_standard_user_field():
    body = {"model": "mortgage_underwriter",
            "messages": [{"role": "user", "content": "Queue?"}],
            "user": "  underwriter-demo  "}
    assert parse_request(body, session_id="conv-1").end_user == "underwriter-demo"
    assert parse_request({**body, "user": "x" * 500}).end_user == "x" * 128
    del body["user"]
    assert parse_request(body).end_user == ""


# ---------------------------------------------------------------------------
# 1.2 – 1.4 the spans of a turn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_llm_span_carries_the_call(tracer):
    await _core().process_task(
        {"task_id": "t1", "content": "Which applications wait for me?"}, _role(),
    )
    (llm,) = tracer.named("acc.pipeline.llm_invoke")
    assert llm.parent is not None and llm.parent.name == "acc.task.process"
    a = llm.attributes
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.response.model"] == "openai/gpt-oss-120b"
    assert a["gen_ai.request.model"] == "gpt-oss-120b"   # the role names none
    assert a["gen_ai.response.finish_reasons"] == ["stop"]
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (120, 30)
    sent = _texts(a["gen_ai.input.messages"])
    assert [r for r, _ in sent] == ["system", "user"]
    assert "Which applications wait for me?" in sent[1][1]
    assert _texts(a["gen_ai.output.messages"]) == [
        ("assistant", "App #501 – Lisa Washington"),
    ]


@pytest.mark.asyncio
async def test_the_root_span_carries_the_turn_and_the_identity(tracer):
    await _core().process_task({
        "task_id": "t1", "content": "Which applications wait for me?",
        "end_user": "underwriter-demo", "session_id": "conv-1",
    }, _role())
    (root,) = tracer.named("acc.task.process")
    a = root.attributes
    assert a["gen_ai.operation.name"] == "invoke_agent"
    assert a["gen_ai.agent.name"] == "mortgage_underwriter"
    assert (a["user.id"], a["session.id"]) == ("underwriter-demo", "conv-1")
    assert _texts(a["gen_ai.input.messages"]) == [
        ("user", "Which applications wait for me?"),
    ]
    assert _texts(a["gen_ai.output.messages"]) == [
        ("assistant", "App #501 – Lisa Washington"),
    ]


@pytest.mark.asyncio
async def test_the_switch_restores_the_old_shape(tracer, monkeypatch):
    monkeypatch.setenv("ACC_TRACE_MESSAGES", "off")
    await _core().process_task({"task_id": "t1", "content": "Queue?"}, _role())
    for span in tracer.spans:
        assert "gen_ai.input.messages" not in span.attributes
        assert "gen_ai.output.messages" not in span.attributes
    # the model and the usage are not message text: they stay
    (llm,) = tracer.named("acc.pipeline.llm_invoke")
    assert llm.attributes["gen_ai.usage.input_tokens"] == 120


@pytest.mark.asyncio
async def test_a_redacting_role_keeps_the_shape_and_loses_the_text(tracer):
    role = _role(telemetry={"redact_messages": True})
    await _core().process_task({"task_id": "t1", "content": "ssn 123-45-6789"}, role)
    (llm,) = tracer.named("acc.pipeline.llm_invoke")
    assert _texts(llm.attributes["gen_ai.input.messages"]) == [
        ("system", REDACTED), ("user", REDACTED),
    ]
    for span in tracer.spans:
        assert "123-45-6789" not in json.dumps(span.attributes)


def test_the_tool_span_carries_arguments_and_result(tracer):
    with pipeline_tracing.tool_span(
        "uw_queue_view", server_id="mortgage_ai_underwriter",
        arguments={"limit": 5},
    ) as span:
        pipeline_tracing.set_tool_result(span, {"content": [{"text": "501, 500, 502"}]})
    (tool,) = tracer.named("acc.tool.invoke")
    a = tool.attributes
    assert a["gen_ai.operation.name"] == "execute_tool"
    assert a["gen_ai.tool.name"] == "uw_queue_view"
    assert json.loads(a["gen_ai.tool.call.arguments"]) == {"limit": 5}
    assert json.loads(a["gen_ai.tool.call.result"]) == {"content": [{"text": "501, 500, 502"}]}


def test_the_tool_span_follows_switch_and_redaction(tracer, monkeypatch):
    with pipeline_tracing.tool_span("t", skill_id="t", arguments={"a": 1}, redact=True) as span:
        pipeline_tracing.set_tool_result(span, {"b": 2}, redact=True)
    a = tracer.named("acc.tool.invoke")[0].attributes
    assert json.loads(a["gen_ai.tool.call.arguments"]) == REDACTED
    assert json.loads(a["gen_ai.tool.call.result"]) == REDACTED

    monkeypatch.setenv("ACC_TRACE_MESSAGES", "off")
    with pipeline_tracing.tool_span("t", skill_id="t", arguments={"a": 1}) as span:
        pipeline_tracing.set_tool_result(span, {"b": 2})
    a = tracer.named("acc.tool.invoke")[1].attributes
    assert "gen_ai.tool.call.arguments" not in a and "gen_ai.tool.call.result" not in a
    assert a["gen_ai.tool.name"] == "t"


# ---------------------------------------------------------------------------
# one turn, one trace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_turn_scope_parents_both_passes_and_the_tool_call(tracer):
    """First pass, tool call, tool-result pass: three roots until 0.17.15."""
    core = _core()
    turn = pipeline_tracing.TurnScope()
    turn.open("acc.turn", {"operation_name": "invoke_agent", "session": "conv-1"})
    try:
        await core.process_task({"task_id": "t1", "content": "Queue?"}, _role())
        with pipeline_tracing.tool_span("uw_queue_view", server_id="s", arguments={}):
            pass
        await core.process_task({"task_id": "t1", "content": "tool results"}, _role())
    finally:
        turn.close()

    roots = [s for s in tracer.spans if s.parent is None]
    assert [s.name for s in roots] == ["acc.turn"]
    assert roots[0].ended
    children = [s.name for s in tracer.spans if s.parent is roots[0]]
    assert children == ["acc.task.process", "acc.tool.invoke", "acc.task.process"]


def test_a_turn_scope_is_idempotent_and_safe_unopened(tracer):
    turn = pipeline_tracing.TurnScope()
    turn.close()                     # never opened: the early-return paths
    first = turn.open("acc.turn")
    assert turn.open("acc.turn") is first   # a second open does not stack
    turn.close()
    turn.close()
    assert len(tracer.named("acc.turn")) == 1 and tracer.spans[0].ended


def test_a_turn_scope_is_a_no_op_without_the_sdk(monkeypatch):
    monkeypatch.setattr(pipeline_tracing, "_get_tracer", lambda: None)
    turn = pipeline_tracing.TurnScope()
    assert turn.open("acc.turn") is None
    turn.close()


# ---------------------------------------------------------------------------
# the same turn through the real SDK (skipped where it is not installed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_turn_through_the_in_memory_exporter():
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch.object(
        pipeline_tracing, "_get_tracer",
        lambda: provider.get_tracer(pipeline_tracing.TRACER_NAME),
    ):
        turn = pipeline_tracing.TurnScope()
        turn.open("acc.turn", {"operation_name": "invoke_agent",
                               "user_id": "underwriter-demo", "session": "conv-1"})
        try:
            await _core().process_task({"task_id": "t1", "content": "Queue?"}, _role())
        finally:
            turn.close()

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert len({s.context.trace_id for s in spans.values()}) == 1
    assert spans["acc.turn"].parent is None
    assert spans["acc.turn"].attributes["session.id"] == "conv-1"
    llm = spans["acc.pipeline.llm_invoke"]
    assert llm.attributes["gen_ai.operation.name"] == "chat"
    assert json.loads(llm.attributes["gen_ai.output.messages"])[0]["role"] == "assistant"
    # OTel attribute values must be primitives or sequences of them
    assert tuple(llm.attributes["gen_ai.response.finish_reasons"]) == ("stop",)
