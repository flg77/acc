"""Cognitive-pipeline OTel tracing helpers — MLflow telemetry Phase 2.

OpenSpec ``20260527-mlflow-otel-telemetry`` Phase 2: emit a per-task
root span ``acc.task.process`` with child spans for the pipeline
stages (``gate.pre`` → ``memory.retrieve`` → ``prompt.build`` →
``llm.invoke`` → ``gate.post`` → ``persist`` → ``drift``).  MLflow's
Trace UI (and Phoenix) render this as the full agent step tree.

Design constraints:

* **Zero runtime hard-dep on opentelemetry.** ``acc.cognitive_core``
  must remain importable on a host without ``opentelemetry`` (the dev
  workstation, slim CI runners).  We try to import ``trace`` lazily
  and fall back to a no-op context manager so the pipeline keeps
  running unchanged when the SDK is absent.

* **No side effects without a configured TracerProvider.** When
  ``OTelMetricsBackend`` is wired up, ``trace.get_tracer(...)`` returns
  the SDK tracer and spans land in OTLP.  When it isn't (log metrics
  backend, tests), OTel returns its built-in NoOpTracer — spans are
  created and immediately discarded.  Either way the call sites in
  the cognitive pipeline don't need to know.

* **GenAI semconv applied automatically.** Span attributes are run
  through :func:`acc.backends.genai_semconv.build_genai_attributes`
  before being set on the span so ``model`` / ``input_tokens`` /
  ``role`` / ``collective_id`` etc. land under the correct namespaces.

Usage from the cognitive core::

    from acc.backends.pipeline_tracing import task_span, stage_span

    with task_span("acc.task.process", task_attributes):
        with stage_span("gate.pre"):
            ...
        with stage_span("llm.invoke", {"model": role.llm_model}):
            ...

Both context managers are no-ops when ``opentelemetry`` is not
installed.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

from acc.backends.genai_semconv import build_genai_attributes

logger = logging.getLogger(__name__)


try:
    from opentelemetry import trace as _otel_trace  # noqa: PLC0415
    _HAVE_OTEL = True
except Exception:  # pragma: no cover — exercised on hosts without otel
    _otel_trace = None  # type: ignore[assignment]
    _HAVE_OTEL = False


# Tracer name used for every cognitive-pipeline span.  Stable so
# downstream filters / dashboards can pin on it.
TRACER_NAME = "acc.cognitive_core"


def _get_tracer() -> Any | None:
    """Return an OTel tracer if the SDK is importable, else ``None``.

    Uses the global tracer provider — when the OTel backend has been
    wired up (``acc.backends.metrics_otel.OTelMetricsBackend``) this
    returns the real SDK tracer and spans land in OTLP.  When the
    backend isn't wired (log metrics / tests) OTel's built-in
    NoOpTracerProvider returns a no-op tracer — calls succeed, spans
    are discarded, zero runtime cost.
    """
    if not _HAVE_OTEL:
        return None
    try:
        return _otel_trace.get_tracer(TRACER_NAME)
    except Exception:  # pragma: no cover — defensive
        logger.debug("pipeline_tracing: get_tracer failed", exc_info=True)
        return None


@contextlib.contextmanager
def task_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Open the root span for one ``process_task`` invocation.

    Yields the underlying OTel span (or ``None`` when the SDK is
    absent) so the caller can attach late-arriving attributes —
    typically the final token counts, the drift score, the eval
    outcome — once they're known at the end of the pipeline.

    Attributes go through :func:`build_genai_attributes` first so
    ``model`` becomes ``gen_ai.request.model``, ``role`` becomes
    ``acc.role``, etc.
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return
    mapped = build_genai_attributes(attributes or {})
    with tracer.start_as_current_span(name) as span:
        for key, value in mapped.items():
            try:
                span.set_attribute(key, value)
            except Exception:
                pass
        yield span


@contextlib.contextmanager
def stage_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Open a child span for one pipeline stage.

    Parented automatically by OTel's current-span context — when called
    inside a :func:`task_span` block the child appears under the root
    span in the resulting trace.
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return
    mapped = build_genai_attributes(attributes or {})
    with tracer.start_as_current_span(name) as span:
        for key, value in mapped.items():
            try:
                span.set_attribute(key, value)
            except Exception:
                pass
        yield span


def emit_stage(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Open and immediately close a thin marker child span.

    Used inline at pipeline step boundaries (PRE-GATE, PROMPT-BUILD,
    …) to mark stage entry without wrapping the stage body in a
    ``with`` block — keeps the existing cognitive-core control flow
    (early returns, async branches) intact.  When called inside a
    :func:`task_span` context the marker is parented under the root
    span automatically (OTel maintains the current-span context).
    No-op when the SDK isn't installed.
    """
    tracer = _get_tracer()
    if tracer is None:
        return
    mapped = build_genai_attributes(attributes or {})
    try:
        with tracer.start_as_current_span(name) as span:
            for key, value in mapped.items():
                try:
                    span.set_attribute(key, value)
                except Exception:
                    pass
    except Exception:  # pragma: no cover — defensive
        logger.debug("pipeline_tracing: emit_stage failed for %s", name,
                     exc_info=True)


def set_span_attributes(span: Any, attributes: dict[str, Any]) -> None:
    """Apply late-arriving attributes to an open span (no-op when ``span`` is None).

    Used at the end of :meth:`CognitiveCore.process_task` to attach
    final token counts / drift / eval scores to the root task span.
    Attributes go through the same semconv mapping.
    """
    if span is None or not attributes:
        return
    mapped = build_genai_attributes(attributes)
    for key, value in mapped.items():
        try:
            span.set_attribute(key, value)
        except Exception:
            pass


__all__ = [
    "TRACER_NAME",
    "task_span",
    "stage_span",
    "emit_stage",
    "set_span_attributes",
]
