"""Unit tests for cognitive-pipeline OTel tracing helpers.

OpenSpec ``20260527-mlflow-otel-telemetry`` Phase 2.  The helpers
must degrade gracefully when ``opentelemetry`` is not installed —
that's the main guarantee these tests exercise so the cognitive
pipeline keeps running on any host.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest

from acc.backends import pipeline_tracing


def _have_otel() -> bool:
    try:
        importlib.import_module("opentelemetry")
        return True
    except Exception:
        return False


_HAVE_OTEL = _have_otel()


def test_task_span_no_op_without_otel(monkeypatch):
    """When the SDK isn't importable the context manager yields None
    and exits cleanly — caller code can run unchanged."""
    monkeypatch.setattr(pipeline_tracing, "_HAVE_OTEL", False)
    monkeypatch.setattr(pipeline_tracing, "_otel_trace", None)
    with pipeline_tracing.task_span("x", {"role": "ingester"}) as span:
        assert span is None


def test_stage_span_no_op_without_otel(monkeypatch):
    monkeypatch.setattr(pipeline_tracing, "_HAVE_OTEL", False)
    monkeypatch.setattr(pipeline_tracing, "_otel_trace", None)
    with pipeline_tracing.stage_span("y") as span:
        assert span is None


def test_emit_stage_no_op_without_otel(monkeypatch):
    """``emit_stage`` is the inline pipeline-marker call — must be a
    silent no-op when otel isn't installed."""
    monkeypatch.setattr(pipeline_tracing, "_HAVE_OTEL", False)
    monkeypatch.setattr(pipeline_tracing, "_otel_trace", None)
    # Should not raise.
    pipeline_tracing.emit_stage("acc.pipeline.gate_pre", {"role": "x"})


def test_set_span_attributes_no_op_on_none():
    """Robust against ``None`` span (the no-op tracer case)."""
    # Should not raise.
    pipeline_tracing.set_span_attributes(None, {"k": "v"})


@pytest.mark.skipif(not _HAVE_OTEL, reason="opentelemetry not installed")
def test_task_span_applies_semconv_mapping_via_set_attribute():
    """When the SDK is present, attributes are mapped through
    build_genai_attributes before set_attribute is called."""
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = (
        mock_span
    )
    mock_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch.object(pipeline_tracing, "_get_tracer", return_value=mock_tracer):
        with pipeline_tracing.task_span(
            "acc.task.process",
            {"role": "coding_agent", "model": "claude", "task_id": "t-1"},
        ) as span:
            assert span is mock_span
    keys_set = {call.args[0] for call in mock_span.set_attribute.call_args_list}
    assert "acc.role" in keys_set
    assert "gen_ai.request.model" in keys_set
    assert "acc.task_id" in keys_set


@pytest.mark.skipif(not _HAVE_OTEL, reason="opentelemetry not installed")
def test_emit_stage_opens_and_closes_child_span():
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = (
        mock_span
    )
    mock_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch.object(pipeline_tracing, "_get_tracer", return_value=mock_tracer):
        pipeline_tracing.emit_stage(
            "acc.pipeline.llm_invoke", {"input_tokens": 42},
        )
    mock_tracer.start_as_current_span.assert_called_once_with(
        "acc.pipeline.llm_invoke",
    )
    keys_set = {call.args[0] for call in mock_span.set_attribute.call_args_list}
    assert "gen_ai.usage.input_tokens" in keys_set


@pytest.mark.skipif(not _HAVE_OTEL, reason="opentelemetry not installed")
def test_set_span_attributes_routes_through_semconv():
    span = MagicMock()
    pipeline_tracing.set_span_attributes(
        span, {"drift_score": 0.3, "role": "ingester"},
    )
    keys_set = {call.args[0] for call in span.set_attribute.call_args_list}
    assert "acc.drift_score" in keys_set
    assert "acc.role" in keys_set


def test_emit_stage_swallows_span_set_attribute_errors(monkeypatch):
    """A misbehaving exporter mustn't propagate exceptions into the
    cognitive pipeline."""
    if not _HAVE_OTEL:
        pytest.skip("opentelemetry not installed")
    bad_tracer = MagicMock()
    bad_span = MagicMock()
    bad_span.set_attribute.side_effect = RuntimeError("boom")
    bad_tracer.start_as_current_span.return_value.__enter__.return_value = (
        bad_span
    )
    bad_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch.object(pipeline_tracing, "_get_tracer", return_value=bad_tracer):
        # Should not raise.
        pipeline_tracing.emit_stage("x", {"role": "r"})
