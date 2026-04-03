"""OpenTelemetry metrics/tracing backend (RHOAI)."""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class OTelMetricsBackend:
    """OpenTelemetry SDK backend.

    Exports via OTLP gRPC to the endpoint configured in
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` (e.g. ``http://otel-collector:4317``).

    The OTel service name is set from *service_name*.
    """

    def __init__(self, service_name: str) -> None:
        resource = Resource.create({"service.name": service_name})
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        # --- Tracing ---
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(tracer_provider)
        self._tracer = trace.get_tracer(service_name)

        # --- Metrics ---
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=True),
            export_interval_millis=15_000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        self._meter = metrics.get_meter(service_name)
        self._gauges: dict[str, Any] = {}

    def emit_span(self, name: str, attributes: dict[str, str | float | int]) -> None:
        """Start and immediately end a span with *attributes*."""
        with self._tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)

    def emit_metric(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record a gauge observation for *name*."""
        if name not in self._gauges:
            self._gauges[name] = self._meter.create_gauge(name)
        self._gauges[name].set(value, labels or {})
