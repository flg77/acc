"""stdout JSON metrics backend (standalone / development)."""

from __future__ import annotations

import json
import sys
import time


class LogMetricsBackend:
    """Writes each emission as a JSON line to stdout.

    Output format per REQ-MET-002::

        {"ts": 1712345678.123, "type": "span",   "name": "...", "attributes": {...}}
        {"ts": 1712345678.456, "type": "metric",  "name": "...", "value": 1.0, "labels": {...}}
    """

    def emit_span(self, name: str, attributes: dict[str, str | float | int]) -> None:
        record = {
            "ts": time.time(),
            "type": "span",
            "name": name,
            "attributes": attributes,
        }
        print(json.dumps(record), flush=True)

    def emit_metric(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        record = {
            "ts": time.time(),
            "type": "metric",
            "name": name,
            "value": value,
            "labels": labels or {},
        }
        print(json.dumps(record), flush=True)
