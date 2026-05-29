"""Tests for metrics backends — log and OTel."""

from __future__ import annotations

import importlib
import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from acc.backends.metrics_log import LogMetricsBackend


def _importable(modname: str) -> bool:
    """True if *modname* imports cleanly (incl. its heavy native deps).

    ``acc.backends.metrics_otel`` pulls ``opentelemetry``, which isn't
    installable on every dev host (e.g. no wheels for the local Python).
    The OTel tests below ``patch`` symbols inside that module, so they can't
    even import it there — skip gracefully; they run fully in CI / containers
    where the dep is present.
    """
    try:
        importlib.import_module(modname)
        return True
    except Exception:
        return False


_HAVE_OTEL = _importable("acc.backends.metrics_otel")


# ---------------------------------------------------------------------------
# LogMetricsBackend
# ---------------------------------------------------------------------------


class TestLogMetricsBackend:
    def test_emit_span_writes_json_to_stdout(self, capsys):
        backend = LogMetricsBackend()
        backend.emit_span("agent.register", {"agent_id": "a-001", "role": "ingester"})
        captured = capsys.readouterr().out.strip()
        record = json.loads(captured)
        assert record["type"] == "span"
        assert record["name"] == "agent.register"
        assert record["attributes"]["agent_id"] == "a-001"
        assert "ts" in record

    def test_emit_metric_writes_json_to_stdout(self, capsys):
        backend = LogMetricsBackend()
        backend.emit_metric("agent.heartbeat", 1.0, {"role": "ingester"})
        captured = capsys.readouterr().out.strip()
        record = json.loads(captured)
        assert record["type"] == "metric"
        assert record["name"] == "agent.heartbeat"
        assert record["value"] == 1.0
        assert record["labels"]["role"] == "ingester"

    def test_emit_metric_no_labels_defaults_to_empty_dict(self, capsys):
        backend = LogMetricsBackend()
        backend.emit_metric("some.metric", 42.0)
        captured = capsys.readouterr().out.strip()
        record = json.loads(captured)
        assert record["labels"] == {}

    def test_emit_span_ts_is_float(self, capsys):
        backend = LogMetricsBackend()
        backend.emit_span("test.span", {"x": 1})
        captured = capsys.readouterr().out.strip()
        record = json.loads(captured)
        assert isinstance(record["ts"], float)

    def test_multiple_emissions_are_separate_lines(self, capsys):
        backend = LogMetricsBackend()
        backend.emit_span("s1", {"a": 1})
        backend.emit_metric("m1", 1.0)
        captured = capsys.readouterr().out
        lines = [l for l in captured.strip().split("\n") if l]
        assert len(lines) == 2
        records = [json.loads(l) for l in lines]
        assert records[0]["type"] == "span"
        assert records[1]["type"] == "metric"


# ---------------------------------------------------------------------------
# OTelMetricsBackend — mocked SDK
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAVE_OTEL, reason="opentelemetry not installed (acc.backends.metrics_otel unimportable)")
class TestOTelMetricsBackend:
    def _make_backend(self):
        """Instantiate OTelMetricsBackend with all OTel SDK calls mocked."""
        mocks = {}

        with patch("acc.backends.metrics_otel.TracerProvider") as MockTP, \
             patch("acc.backends.metrics_otel.MeterProvider") as MockMP, \
             patch("acc.backends.metrics_otel.OTLPSpanExporter"), \
             patch("acc.backends.metrics_otel.OTLPMetricExporter"), \
             patch("acc.backends.metrics_otel.BatchSpanProcessor"), \
             patch("acc.backends.metrics_otel.PeriodicExportingMetricReader"), \
             patch("acc.backends.metrics_otel.trace") as mock_trace, \
             patch("acc.backends.metrics_otel.metrics") as mock_metrics:

            mock_tracer = MagicMock()
            mock_trace.get_tracer.return_value = mock_tracer
            mock_meter = MagicMock()
            mock_metrics.get_meter.return_value = mock_meter

            from acc.backends.metrics_otel import OTelMetricsBackend
            backend = OTelMetricsBackend(service_name="acc-test")
            mocks["tracer"] = mock_tracer
            mocks["meter"] = mock_meter

        return backend, mocks

    def test_emit_span_starts_span_with_attributes(self):
        backend, mocks = self._make_backend()
        mock_span = MagicMock()
        mocks["tracer"].start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mocks["tracer"].start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        backend.emit_span("test.span", {"key": "value", "count": 3})
        mocks["tracer"].start_as_current_span.assert_called_once_with("test.span")

    def test_emit_metric_creates_gauge_on_first_call(self):
        backend, mocks = self._make_backend()
        mock_gauge = MagicMock()
        mocks["meter"].create_gauge.return_value = mock_gauge

        backend.emit_metric("cpu.usage", 0.75, {"host": "node-01"})
        mocks["meter"].create_gauge.assert_called_once_with("cpu.usage")
        mock_gauge.set.assert_called_once_with(0.75, {"host": "node-01"})

    def test_emit_metric_reuses_existing_gauge(self):
        backend, mocks = self._make_backend()
        mock_gauge = MagicMock()
        mocks["meter"].create_gauge.return_value = mock_gauge

        backend.emit_metric("cpu.usage", 0.5)
        backend.emit_metric("cpu.usage", 0.6)
        # create_gauge should only be called once
        assert mocks["meter"].create_gauge.call_count == 1
        assert mock_gauge.set.call_count == 2

    def test_emit_span_applies_genai_semconv_mapping(self):
        """OpenSpec 20260527-mlflow-otel-telemetry Phase 1: span
        attributes are translated to gen_ai.* / acc.* keys before
        being set on the span."""
        backend, mocks = self._make_backend()
        mock_span = MagicMock()
        mocks["tracer"].start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mocks["tracer"].start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        backend.emit_span(
            "llm.invoke",
            {"model": "claude-sonnet-4.5", "role": "coding_agent",
             "input_tokens": 100},
        )
        # Collect every key passed to span.set_attribute.
        keys_set = {
            call.args[0] for call in mock_span.set_attribute.call_args_list
        }
        assert "gen_ai.request.model" in keys_set
        assert "gen_ai.usage.input_tokens" in keys_set
        assert "acc.role" in keys_set
        # The original ad-hoc keys must not leak through.
        assert "model" not in keys_set
        assert "role" not in keys_set


# ---------------------------------------------------------------------------
# OTLP exporter protocol selection — Phase 1 of MLflow OTel proposal
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAVE_OTEL, reason="opentelemetry not installed")
class TestOTLPProtocolSelection:
    def test_default_protocol_uses_grpc_exporters(self, monkeypatch):
        """Unset OTEL_EXPORTER_OTLP_PROTOCOL → gRPC exporters
        (preserves pre-Phase-1 behaviour)."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
        from acc.backends import metrics_otel
        span, metric = metrics_otel._resolve_exporters(
            "http://collector:4317",
        )
        # The gRPC exporter classes are the ones imported at module
        # top-level — identity comparison is enough.
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter as GRPCMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GRPCSpanExporter,
        )
        assert isinstance(span, GRPCSpanExporter)
        assert isinstance(metric, GRPCMetricExporter)

    def test_http_protocol_uses_http_exporters_when_available(
        self, monkeypatch,
    ):
        """OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf → HTTP exporters.

        Skips if the HTTP exporter package isn't installed (it's an
        opt-in extra).
        """
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                OTLPSpanExporter as HTTPSpanExporter,
            )
        except ImportError:
            pytest.skip("opentelemetry-exporter-otlp-proto-http not installed")
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf",
        )
        from acc.backends import metrics_otel
        span, metric = metrics_otel._resolve_exporters(
            "http://mlflow:4318/v1/traces",
        )
        assert isinstance(span, HTTPSpanExporter)

    def test_unknown_protocol_falls_back_to_grpc_with_warning(
        self, monkeypatch, caplog,
    ):
        """A typo in the env var must not silently disable telemetry —
        warn + fall back to gRPC."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "carrier-pigeon")
        from acc.backends import metrics_otel
        with caplog.at_level("WARNING", logger="acc.backends.metrics_otel"):
            span, metric = metrics_otel._resolve_exporters(
                "http://collector:4317",
            )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GRPCSpanExporter,
        )
        assert isinstance(span, GRPCSpanExporter)
        assert any(
            "carrier-pigeon" in rec.message for rec in caplog.records
        )
