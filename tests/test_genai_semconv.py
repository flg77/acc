"""Unit tests for OTel GenAI semantic-convention mapping.

OpenSpec ``20260527-mlflow-otel-telemetry`` Phase 1.  Pure-Python
helper — no OTel SDK dep, runs everywhere.
"""

from __future__ import annotations

from acc.backends.genai_semconv import (
    GENAI_SEMCONV_VERSION,
    build_genai_attributes,
)


def test_genai_semconv_version_pinned():
    """The semconv version is a string we can introspect for upgrades."""
    assert isinstance(GENAI_SEMCONV_VERSION, str)
    assert GENAI_SEMCONV_VERSION  # not empty


def test_model_field_maps_to_gen_ai_request_model():
    out = build_genai_attributes({"model": "claude-sonnet-4.5"})
    assert out == {"gen_ai.request.model": "claude-sonnet-4.5"}


def test_token_counts_map_to_gen_ai_usage_keys():
    out = build_genai_attributes(
        {"input_tokens": 1234, "output_tokens": 567},
    )
    assert out["gen_ai.usage.input_tokens"] == 1234
    assert out["gen_ai.usage.output_tokens"] == 567


def test_acc_role_collective_task_become_acc_namespace():
    out = build_genai_attributes(
        {"role": "coding_agent", "collective_id": "c1", "task_id": "t-42"},
    )
    assert out["acc.role"] == "coding_agent"
    assert out["acc.collective_id"] == "c1"
    assert out["acc.task_id"] == "t-42"


def test_operation_kwarg_overrides_dict_value():
    """Explicit operation kwarg wins over any value in the dict."""
    out = build_genai_attributes(
        {"operation": "chat"}, operation="text_completion",
    )
    assert out["gen_ai.operation.name"] == "text_completion"


def test_operation_kwarg_added_when_missing():
    out = build_genai_attributes({"role": "ingester"}, operation="embeddings")
    assert out["gen_ai.operation.name"] == "embeddings"


def test_none_values_dropped():
    """OTel attribute values must not be None — None entries are dropped."""
    out = build_genai_attributes({"model": None, "role": "x"})
    assert "gen_ai.request.model" not in out
    assert out["acc.role"] == "x"


def test_unknown_keys_pass_through():
    """Debug-only fields not in the map are forwarded verbatim."""
    out = build_genai_attributes({"debug_flag": "yes", "row": 7})
    assert out["debug_flag"] == "yes"
    assert out["row"] == 7


def test_input_not_mutated():
    """The helper must not mutate the caller's dict in place."""
    raw = {"model": "claude", "role": "ingester"}
    snapshot = dict(raw)
    build_genai_attributes(raw)
    assert raw == snapshot


def test_empty_dict_returns_empty():
    assert build_genai_attributes({}) == {}


def test_eval_drift_score_under_acc_namespace():
    out = build_genai_attributes(
        {"eval_score": 0.87, "drift_score": 0.12},
    )
    assert out["acc.eval_score"] == 0.87
    assert out["acc.drift_score"] == 0.12


def test_backend_field_maps_to_gen_ai_system():
    out = build_genai_attributes({"backend": "anthropic"})
    assert out["gen_ai.system"] == "anthropic"
