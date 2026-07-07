"""Modal Skills / MCPs toggler for the Nucleus role form.

Reached via ``Ctrl+A`` → ``s`` (Skills) / ``m`` (MCPs).  Lists every capability
the deploy knows about — the role's current grant ∪ what's installed — with a
row cursor:

    ↑/↓ move · ←/→ off/on · Space toggle · Enter save · Esc cancel

Dismisses with the edited **allowed** set (``set[str]``), or ``None`` on cancel.
The caller (InfuseScreen) overlays the returned set onto the form; it is
persisted to ``roles/<name>/role.yaml`` on Apply.

The toggle keys are ``priority`` bindings so they win over the focused
DataTable (which owns ↑/↓ for the row cursor); ``priority`` beats a focused
widget's own bindings in Textual.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label

_ON = "[green]●[/green]"
_OFF = "[dim]○[/dim]"


class CapsEditorModal(ModalScreen[set]):
    """Toggle which skills / MCPs a role is allowed to reach."""

    BINDINGS = [
        Binding("left", "toggle_off", "Off", show=False, priority=True),
        Binding("right", "toggle_on", "On", show=False, priority=True),
        Binding("space", "toggle", "Toggle", show=False, priority=True),
        Binding("enter", "save", "Save", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    CapsEditorModal {
        align: center middle;
    }
    CapsEditorModal #caps-editor-box {
        width: 64;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    CapsEditorModal #caps-editor-title {
        text-style: bold;
        color: $accent;
    }
    CapsEditorModal #caps-editor-table {
        height: auto;
        max-height: 18;
        margin: 1 0;
    }
    CapsEditorModal #caps-editor-hint {
        color: $text-muted;
    }
    """

    def __init__(
        self,
        *,
        kind: str,
        title: str,
        rows: list[dict],
        allowed: set[str],
    ) -> None:
        super().__init__()
        self._kind = kind
        self._title = title
        self._rows = rows  # [{"id", "installed": bool, "risk": str}]
        self._allowed = set(allowed)

    def compose(self) -> ComposeResult:
        with Vertical(id="caps-editor-box"):
            yield Label(self._title, id="caps-editor-title")
            yield DataTable(id="caps-editor-table")
            yield Label(
                "↑/↓ move · ←/→ off/on · Space toggle · Enter save · Esc cancel",
                id="caps-editor-hint",
            )

    def on_mount(self) -> None:
        table = self.query_one("#caps-editor-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("on", self._kind, "inst", "risk")
        self._repaint()
        table.focus()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _repaint(self) -> None:
        table = self.query_one("#caps-editor-table", DataTable)
        cursor = table.cursor_row if table.row_count else 0
        table.clear()
        for row in self._rows:
            cid = row["id"]
            on = _ON if cid in self._allowed else _OFF
            inst = "[green]✓[/green]" if row.get("installed") else "[dim]·[/dim]"
            table.add_row(on, cid, inst, str(row.get("risk", "—")))
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def _current_id(self) -> str | None:
        # Rows are painted in `self._rows` order and never reordered, so the
        # cursor index maps straight back to the id.
        table = self.query_one("#caps-editor-table", DataTable)
        idx = table.cursor_row
        if 0 <= idx < len(self._rows):
            return self._rows[idx]["id"]
        return None

    def _set(self, cid: str, on: bool) -> None:
        if on:
            self._allowed.add(cid)
        else:
            self._allowed.discard(cid)
        self._repaint()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_on(self) -> None:
        cid = self._current_id()
        if cid is not None:
            self._set(cid, True)

    def action_toggle_off(self) -> None:
        cid = self._current_id()
        if cid is not None:
            self._set(cid, False)

    def action_toggle(self) -> None:
        cid = self._current_id()
        if cid is not None:
            self._set(cid, cid not in self._allowed)

    def action_save(self) -> None:
        self.dismiss(self._allowed)

    def action_cancel(self) -> None:
        self.dismiss(None)
