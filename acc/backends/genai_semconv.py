"""OpenTelemetry GenAI Semantic-Convention attribute mapping.

Helper for OpenSpec ``20260527-mlflow-otel-telemetry`` Phase 1.

MLflow 3.6+ and other GenAI-aware observability tools (Phoenix, Langfuse,
…) recognise spans labelled with the **OpenTelemetry GenAI Semantic
Conventions** — attributes under the ``gen_ai.*`` namespace.  ACC's
existing telemetry call sites pass ad-hoc attribute dicts; this module
provides a single function that translates the ACC vocabulary
(``role``, ``collective_id``, ``task_id``, token counts, eval score,
drift, etc.) into the standardised ``gen_ai.*`` keys plus ACC-specific
namespaced extras (``acc.*``) so a downstream backend that follows
semconv can render the trace correctly while ACC-specific fields are
still queryable.

The semconv version is pinned in ``GENAI_SEMCONV_VERSION`` so a future
upstream rename can be tracked in one place.

Why a helper instead of editing every emit site:
- the call sites in ``acc/agent.py`` and the cognitive-core pipeline
  already have a ``dict`` of attributes ready to go;
- a thin translation layer means the OTel backend stays semconv-aware
  while the log-metrics backend (used by tests) keeps the original
  developer-friendly names;
- we can grow the mapping (e.g. tool-call attributes once we wire MCP
  spans) without touching the agent.
"""

from __future__ import annotations

import json
import os
from typing import Any


# Pinned semconv version — see
# https://opentelemetry.io/docs/specs/semconv/gen-ai/.  Bump this string
# (and the mapping below) when we adopt a newer release.
GENAI_SEMCONV_VERSION = "1.30.0"


# Static mapping of ACC attribute keys → OTel GenAI semconv keys.
# Keys not in this map are passed through verbatim (e.g. operator-
# supplied debug fields).  ACC-specific concepts that don't have a
# semconv equivalent are routed to the ``acc.*`` namespace by the
# caller (see ``_ACC_NAMESPACED`` below).
_GENAI_KEY_MAP: dict[str, str] = {
    # LLM request/response (PR-R landed token counts on the anthropic
    # backend; the helper is forward-compatible with vllm/openai too).
    "model": "gen_ai.request.model",
    "llm_model": "gen_ai.request.model",
    "backend": "gen_ai.system",
    "llm_backend": "gen_ai.system",
    "input_tokens": "gen_ai.usage.input_tokens",
    "prompt_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "completion_tokens": "gen_ai.usage.output_tokens",
    "operation": "gen_ai.operation.name",
    "operation_name": "gen_ai.operation.name",
    "temperature": "gen_ai.request.temperature",
    "max_tokens": "gen_ai.request.max_tokens",
    "finish_reason": "gen_ai.response.finish_reasons",
    # OpenSpec ``20260918-mlflow-shaped-spans`` — the conversation on the
    # trace.  MLflow's OTLP ingest derives a span's Inputs/Outputs from these
    # (``mlflow/tracing/otel/translation/genai_semconv.py``), the model column
    # from the response/request model, and the trace's user and session from
    # ``user.id`` / ``session.id`` on any span.
    "input_messages": "gen_ai.input.messages",
    "output_messages": "gen_ai.output.messages",
    "response_model": "gen_ai.response.model",
    "provider": "gen_ai.provider.name",
    "tool_arguments": "gen_ai.tool.call.arguments",
    "tool_result": "gen_ai.tool.call.result",
    "agent_name": "gen_ai.agent.name",
    "user_id": "user.id",
    "session": "session.id",
}


# ACC fields that don't have a semconv key but should still surface in
# downstream UIs.  These get the ``acc.`` namespace so they sort
# together in MLflow / Phoenix without colliding with semconv evolution.
_ACC_NAMESPACED: frozenset[str] = frozenset({
    "role",
    "collective_id",
    "task_id",
    "agent_id",
    "eval_score",
    "drift_score",
    "domain_drift_score",
    "compliance_health_score",
    "operating_mode",
    "cache_read_tokens",
    "cat_b_deviation_score",
    "reprogramming_level",
})


def build_genai_attributes(
    raw: dict[str, Any],
    *,
    operation: str | None = None,
) -> dict[str, Any]:
    """Translate an ACC attribute dict into OTel GenAI semconv keys.

    Args:
        raw: the attribute dict the caller would have passed to
            ``emit_span``/``emit_metric`` pre-semconv.
        operation: optional GenAI operation name (``"chat"``,
            ``"text_completion"``, ``"embeddings"``, ``"tool"``).  When
            given, becomes ``gen_ai.operation.name`` (overrides any
            value already in *raw* under that name).

    Returns:
        A new dict.  Keys in ``_GENAI_KEY_MAP`` are renamed to their
        ``gen_ai.*`` equivalents; keys in ``_ACC_NAMESPACED`` are
        rewritten to ``acc.<key>``; everything else passes through
        verbatim so debug-only fields still travel.  ``None`` values
        are dropped — OTel attribute values must be primitive non-None
        types.

    The input dict is not mutated.
    """
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if value is None:
            continue
        if key in _GENAI_KEY_MAP:
            out[_GENAI_KEY_MAP[key]] = value
        elif key in _ACC_NAMESPACED:
            out[f"acc.{key}"] = value
        else:
            out[key] = value
    if operation is not None:
        out["gen_ai.operation.name"] = operation
    return out


# ---------------------------------------------------------------------------
# Message payloads (OpenSpec ``20260918-mlflow-shaped-spans``)
# ---------------------------------------------------------------------------

#: What a redacting role's payloads read as.  The shape (roles, one part per
#: message, a JSON value for a tool payload) is kept so the trace still shows
#: THAT a system prompt, a user turn and an answer existed.
REDACTED = "<redacted>"

_DEFAULT_MESSAGES_MAX_CHARS = 8192


def trace_messages_enabled() -> bool:
    """``ACC_TRACE_MESSAGES`` — ``on`` (default) puts message and tool payload
    text on the spans, ``off`` restores the pre-0.17.16 shape."""
    raw = os.environ.get("ACC_TRACE_MESSAGES", "on").strip().lower()
    return raw not in ("off", "0", "false", "no")


def messages_max_chars() -> int:
    """``ACC_TRACE_MESSAGES_MAX_CHARS`` — the cap per text, default 8192."""
    try:
        value = int(os.environ.get(
            "ACC_TRACE_MESSAGES_MAX_CHARS", str(_DEFAULT_MESSAGES_MAX_CHARS),
        ))
    except (TypeError, ValueError):
        return _DEFAULT_MESSAGES_MAX_CHARS
    return value if value > 0 else _DEFAULT_MESSAGES_MAX_CHARS


def clip_text(text: str, max_chars: int | None = None) -> str:
    """Clip *text* at the cap and say how much was cut.

    The marker counts characters, so a reader knows whether a prompt was cut
    by a line or by a hundred kilobytes.
    """
    cap = messages_max_chars() if max_chars is None else max_chars
    if len(text) <= cap:
        return text
    return f"{text[:cap]}…[{len(text) - cap} more]"


def messages_json(
    messages: list[tuple[str, str]],
    *,
    redact: bool = False,
    max_chars: int | None = None,
) -> str:
    """Serialise ``(role, text)`` pairs as the OTel GenAI message list.

    ``[{"role": "user", "parts": [{"type": "text", "content": "…"}]}, …]`` —
    the value of ``gen_ai.input.messages`` / ``gen_ai.output.messages``.  Each
    text is clipped on its own, so a long system prompt cannot push the user's
    turn off the span.  Messages with no text are dropped.  *redact* keeps the
    roles and replaces every text with :data:`REDACTED`.
    """
    out: list[dict[str, Any]] = []
    for role, text in messages:
        if not text:
            continue
        content = REDACTED if redact else clip_text(str(text), max_chars)
        out.append({
            "role": str(role),
            "parts": [{"type": "text", "content": content}],
        })
    return json.dumps(out, ensure_ascii=False)


def payload_json(
    value: Any,
    *,
    redact: bool = False,
    max_chars: int | None = None,
) -> str:
    """Serialise a tool's arguments or result for a span attribute.

    Always a JSON document: MLflow reads ``gen_ai.tool.call.arguments`` and
    ``.result`` as JSON, and a bare string would not parse.  Oversized payloads
    are clipped as text and carried as a JSON string, marker included.
    """
    if redact:
        return json.dumps(REDACTED)
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps(str(value), ensure_ascii=False)
    cap = messages_max_chars() if max_chars is None else max_chars
    if len(text) <= cap:
        return text
    return json.dumps(clip_text(text, cap), ensure_ascii=False)


__all__ = [
    "GENAI_SEMCONV_VERSION",
    "REDACTED",
    "build_genai_attributes",
    "clip_text",
    "messages_json",
    "messages_max_chars",
    "payload_json",
    "trace_messages_enabled",
]
