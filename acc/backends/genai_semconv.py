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


__all__ = [
    "GENAI_SEMCONV_VERSION",
    "build_genai_attributes",
]
