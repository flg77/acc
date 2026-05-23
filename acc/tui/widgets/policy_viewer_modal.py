"""`PolicyViewerModal` — read-only browser for a governance policy file
(PR-Z1b, Compliance enhancements).

Opened from the Compliance pane's Cat-A/B/C governance tables when the
operator selects a rule row.  Shows the source ``.rego`` / ``.json``
file content so the operator can read exactly what is loaded.  Strictly
read-only — policy authoring happens via the gap-closure / signed-bundle
path, never by editing a file in this modal.

Rego content contains ``[...]`` set syntax, so the file body is rendered
with Rich markup DISABLED (escaped) to avoid the TUI interpreting it as
colour tags.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class PolicyViewerModal(ModalScreen[None]):
    """Scrollable, read-only view of a single policy file."""

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("q", "close", "Close", priority=True),
    ]

    DEFAULT_CSS = """
    PolicyViewerModal { align: center middle; }
    PolicyViewerModal > Vertical {
        width: 90%; height: 85%;
        border: thick $primary; background: $surface; padding: 1;
    }
    PolicyViewerModal #pv-title { height: 1; text-style: bold; color: $primary; }
    PolicyViewerModal #pv-meta { height: 1; color: $text-muted; }
    PolicyViewerModal #pv-scroll { height: 1fr; border: round $primary; margin: 1 0; }
    PolicyViewerModal #pv-actions { height: 3; align: right middle; }
    """

    def __init__(self, path: str | Path, *, highlight_line: int = 0, **kwargs) -> None:
        super().__init__(**kwargs)
        self._path = Path(path)
        self._highlight_line = highlight_line

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Policy: {self._path.name}", id="pv-title")
            yield Static(f"[dim]{self._path}[/dim]", id="pv-meta")
            with ScrollableContainer(id="pv-scroll"):
                yield Static(self._render_body(), id="pv-body", markup=False)
            with Horizontal(id="pv-actions"):
                yield Button("Close", id="pv-close", variant="primary")

    def _render_body(self) -> str:
        """Return the file content with line numbers (markup-safe).

        Best-effort: an unreadable file shows a short error instead of
        raising — the modal must always open."""
        try:
            text = self._path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"(could not read {self._path}: {exc})"
        out: list[str] = []
        for i, line in enumerate(text.splitlines(), start=1):
            marker = "▶" if i == self._highlight_line else " "
            out.append(f"{marker}{i:>4} │ {line}")
        return "\n".join(out) or "(empty file)"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "pv-close":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)
