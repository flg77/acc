"""`InvocationDetailModal` — drill into one capability invocation.

PR-F of the workflow rework: the Prompt screen's invocation
waterfall (`#invocation-waterfall`) is a DataTable; clicking a row
opens this modal to show the full per-invocation record.

Today's TASK_COMPLETE.invocations dict ships only the shape
``{kind, target, ok, error}`` — this modal renders that exhaustively
(plus the task_id + agent_id from the parent transcript entry).  As
richer fields land (audit-record reference, skill-result payload),
they slot in here without touching the screen's row-builder.
"""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class InvocationDetailModal(ModalScreen[None]):
    """Modal popup showing one invocation row's full record."""

    DEFAULT_CSS = """
    InvocationDetailModal {
        align: center middle;
    }
    InvocationDetailModal #invocation-detail-panel {
        width: 80%;
        max-width: 120;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    InvocationDetailModal #invocation-detail-title {
        padding: 0 0 1 0;
        color: $accent;
        text-style: bold;
    }
    InvocationDetailModal #invocation-detail-body {
        height: auto;
    }
    InvocationDetailModal #invocation-detail-close-row {
        align: right middle;
        padding: 1 0 0 0;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def __init__(self, record: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._record = dict(record)

    def compose(self) -> ComposeResult:
        rec = self._record
        kind = rec.get("kind", "?")
        target = rec.get("target", "?")
        ok = rec.get("ok", False)
        marker = "[green]✓ ok[/green]" if ok else "[red]✗ failed[/red]"

        with Vertical(id="invocation-detail-panel"):
            yield Static(
                f"[bold]{kind}:{target}[/bold]  {marker}",
                id="invocation-detail-title",
            )
            with ScrollableContainer():
                yield Static(self._render_body(rec), id="invocation-detail-body")
            with Vertical(id="invocation-detail-close-row"):
                yield Button("Close", id="btn-invocation-detail-close",
                              variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-invocation-detail-close":
            self.dismiss(None)

    @staticmethod
    def _render_body(rec: dict[str, Any]) -> str:
        """Render the record as labelled fields + a raw JSON dump.

        Known fields surface as a short, scannable list first; the
        full record follows in a JSON code-fence so the operator can
        copy-paste into a bug report without losing fidelity.
        """
        lines: list[str] = []

        def _row(label: str, value: Any) -> None:
            if value in (None, "", False) and not isinstance(value, bool):
                return
            lines.append(f"[bold]{label}:[/bold] {value}")

        _row("task_id", rec.get("task_id", ""))
        _row("agent_id", rec.get("agent_id", ""))
        _row("kind", rec.get("kind", ""))
        _row("target", rec.get("target", ""))
        ok = rec.get("ok")
        if ok is not None:
            _row("ok", str(ok))
        _row("error", rec.get("error", ""))
        _row("duration_ms", rec.get("duration_ms", ""))
        _row("confidence", rec.get("confidence", ""))
        _row("ts", rec.get("ts", ""))

        body = "\n".join(lines)
        body += "\n\n[dim]raw record:[/dim]\n"
        try:
            raw_json = json.dumps(rec, indent=2, default=str, sort_keys=True)
        except Exception:  # noqa: BLE001
            raw_json = repr(rec)
        body += raw_json
        return body
