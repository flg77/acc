"""OpenTelemetry metrics/tracing backend (RHOAI).

Phase 1 of OpenSpec ``20260527-mlflow-otel-telemetry``:

- The exporter protocol is selectable via ``OTEL_EXPORTER_OTLP_PROTOCOL``
  (matches the OTel spec env var).  Supported values:

  * ``grpc`` (default — preserves pre-Phase-1 behaviour, targets the
    OTel Collector on port 4317);
  * ``http/protobuf`` — targets an HTTP collector endpoint (e.g.
    MLflow's ``/v1/traces``) on port 4318.  The matching HTTP exporter
    package is imported lazily so the gRPC default keeps working
    without the extra dep.

- Span/metric attributes are run through
  :func:`acc.backends.genai_semconv.build_genai_attributes` so MLflow
  and other semconv-aware backends recognise model/token/operation
  fields and render the trace correctly.  ACC-specific fields are kept
  under the ``acc.*`` namespace.
"""

from __future__ import annotations

import logging
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

from acc.backends.genai_semconv import build_genai_attributes

logger = logging.getLogger(__name__)


def _resolve_exporters(endpoint: str) -> tuple[Any, Any]:
    """Pick the OTLP exporter pair (span, metric) for the active protocol.

    Reads ``OTEL_EXPORTER_OTLP_PROTOCOL`` (``grpc`` / ``http/protobuf``).
    Falls back to gRPC for unknown values with a warning so a typo
    can't silently disable telemetry.  Lazy-imports the HTTP exporter
    so the gRPC default doesn't grow a hard dep on the HTTP package.
    """
    protocol = os.environ.get(
        "OTEL_EXPORTER_OTLP_PROTOCOL", "grpc",
    ).strip().lower()

    if protocol in ("http/protobuf", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (  # noqa: PLC0415
                OTLPMetricExporter as HTTPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                OTLPSpanExporter as HTTPSpanExporter,
            )
        except ImportError as exc:
            raise RuntimeError(
                "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf requires the "
                "opentelemetry-exporter-otlp-proto-http package — install "
                "with `pip install 'acc[mlflow]'` (or directly).  Got: "
                f"{exc}"
            ) from exc
        # HTTP exporters accept the full /v1/traces and /v1/metrics
        # paths; if the operator passed a bare host:port we let the
        # exporter append the default sub-paths.
        return (
            HTTPSpanExporter(endpoint=endpoint),
            HTTPMetricExporter(endpoint=endpoint),
        )

    if protocol != "grpc":
        logger.warning(
            "metrics_otel: unknown OTEL_EXPORTER_OTLP_PROTOCOL=%r, "
            "falling back to grpc",
            protocol,
        )
    return (
        OTLPSpanExporter(endpoint=endpoint, insecure=True),
        OTLPMetricExporter(endpoint=endpoint, insecure=True),
    )


class OTelMetricsBackend:
    """OpenTelemetry SDK backend.

    Exports via OTLP to ``OTEL_EXPORTER_OTLP_ENDPOINT``.  Protocol is
    selected by ``OTEL_EXPORTER_OTLP_PROTOCOL`` (``grpc`` / ``http/
    protobuf``).  See :func:`_resolve_exporters`.

    Attribute dicts on ``emit_span``/``emit_metric`` are run through
    the GenAI semconv mapping so downstream backends (MLflow Trace UI,
    Phoenix, …) see standardised ``gen_ai.*`` keys.
    """

    def __init__(self, service_name: str) -> None:
        resource = Resource.create({"service.name": service_name})
        # Default endpoint depends on the protocol — gRPC uses 4317,
        # HTTP/protobuf uses 4318.  Operators can override with the
        # explicit env var.
        protocol = os.environ.get(
            "OTEL_EXPORTER_OTLP_PROTOCOL", "grpc",
        ).strip().lower()
        default_endpoint = (
            "http://localhost:4318" if protocol.startswith("http")
            else "http://localhost:4317"
        )
        endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", default_endpoint,
        )

        span_exporter, metric_exporter = _resolve_exporters(endpoint)

        # --- Tracing ---
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        self._tracer = trace.get_tracer(service_name)

        # --- Metrics ---
        reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=15_000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        self._meter = metrics.get_meter(service_name)
        self._gauges: dict[str, Any] = {}

    def emit_span(self, name: str, attributes: dict[str, str | float | int]) -> None:
        """Start and immediately end a span with *attributes*.

        Attributes are run through the GenAI semconv mapping so a key
        like ``model`` becomes ``gen_ai.request.model`` and ``role``
        becomes ``acc.role`` — see
        :func:`acc.backends.genai_semconv.build_genai_attributes`.
        """
        mapped = build_genai_attributes(attributes)
        with self._tracer.start_as_current_span(name) as span:
            for key, value in mapped.items():
                span.set_attribute(key, value)

    def emit_metric(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record a gauge observation for *name*.

        Labels are run through the same semconv mapping so high-
        cardinality fields stay under the ``acc.*`` namespace.
        """
        if name not in self._gauges:
            self._gauges[name] = self._meter.create_gauge(name)
        mapped_labels = build_genai_attributes(labels or {})
        self._gauges[name].set(value, mapped_labels)
