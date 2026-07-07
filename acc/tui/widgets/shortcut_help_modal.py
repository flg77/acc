"""Keyboard-shortcut cheat sheet — reached via ``Ctrl+A`` → ``h`` on any pane.

Distinct from the ``?`` HelpScreen (per-pane markdown docs): this is a compact,
always-consistent reference for the navigation keys + the current pane's
``Ctrl+A`` which-key menu.  Rendered from the same entry list the leader menu
uses, so the two never drift.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label


class ShortcutHelpModal(ModalScreen[None]):
    """Modal cheat sheet of the keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
        Binding("h", "close", "Close", show=False),
        Binding("question_mark", "close", "Close", show=False),
    ]

    DEFAULT_CSS = """
    ShortcutHelpModal {
        align: center middle;
    }
    ShortcutHelpModal #shortcut-help-box {
        width: auto;
        max-width: 72;
        height: auto;
        max-height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    ShortcutHelpModal #shortcut-help-title {
        text-style: bold;
        color: $accent;
        margin: 0 0 1 0;
    }
    ShortcutHelpModal #shortcut-help-hint {
        color: $text-muted;
        margin: 1 0 0 0;
    }
    """

    def __init__(self, leader_entries: list[tuple[str, str]]) -> None:
        super().__init__()
        # (key, label) for THIS pane's Ctrl+A menu (incl. the universal h/0/1).
        self._leader_entries = leader_entries

    def compose(self) -> ComposeResult:
        with Vertical(id="shortcut-help-box"):
            yield Label("Keyboard shortcuts", id="shortcut-help-title")
            for line in self._lines():
                yield Label(line, classes="shortcut-help-line")
            yield Label("Esc / q / h to close", id="shortcut-help-hint")

    def _lines(self) -> list[str]:
        # One Label per line — Label handles markup reliably where a single
        # multi-line Static does not (Textual 8.x).
        lines = [
            "[b]Navigation[/b]  (from any pane)",
            "  [b]1[/b]-[b]9[/b]      switch pane (Soma … Diagnostics)",
            "  [b]Ctrl+A[/b]     open this pane's menu",
            "  [b]?[/b]          this pane's docs",
            "  [b]q[/b]          quit",
            "",
            "[b]Ctrl+A then…[/b]  (this pane)",
        ]
        for key, label in self._leader_entries:
            lines.append(f"  [b]{key}[/b]{' ' * max(1, 10 - len(key))}{label}")
        return lines

    def action_close(self) -> None:
        self.dismiss(None)
