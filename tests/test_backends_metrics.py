"""Tests for metrics backends — log and OTel."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from acc.backends.metrics_log import LogMetricsBackend


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
