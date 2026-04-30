"""Modal file picker — operator-facing manifest upload (PR-A2).

Textual ships :class:`textual.widgets.DirectoryTree` for filesystem
navigation but **no FilePicker / FileOpen dialog**, so PR-A2 builds
one as a thin wrapper.

Design choice: the operator picks a *file* (a ``skill.yaml`` or
``mcp.yaml``).  The receiving screen infers the parent directory as
the upload unit and copies the whole tree — that way a skill (which
is ``skill.yaml`` + ``adapter.py`` co-resident in one directory) and
an MCP (which is just ``mcp.yaml`` in its own directory) follow the
same workflow on the operator's side.

Lifecycle::

    modal = FilePickerModal(
        target_filename="skill.yaml",
        title="Upload a skill",
        start_path=Path.home(),
    )
    self.app.push_screen(modal)
    # ... operator navigates, selects file, presses Confirm
    # → modal posts FileSelected(path) and self-dismisses
    # OR presses Cancel / Esc → no message, just dismiss

The receiving screen registers an ``on_file_selected`` handler:

    def on_file_picker_modal_file_selected(
        self, message: FilePickerModal.FileSelected
    ) -> None:
        ...

Naming: by Textual's snake-case convention, the message class
``FilePickerModal.FileSelected`` dispatches to handler
``on_file_picker_modal_file_selected``.

Validation: the modal accepts any file *during navigation* but the
Confirm button is enabled only when the highlighted file's name
matches ``target_filename`` (case-sensitive).  This prevents the
operator from accidentally uploading the wrong file type.

Cancellation: Esc and the Cancel button both dismiss without
posting any message.  Pressing Confirm with no file selected is a
no-op (button stays disabled until selection).
"""

from __future__ import annotations

import logging
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Footer, Label, Static

logger = logging.getLogger("acc.tui.widgets.file_picker")


class FilePickerModal(ModalScreen):
    """Modal screen wrapping :class:`textual.widgets.DirectoryTree`.

    Args:
        target_filename: Exact filename the operator must select before
            Confirm enables (e.g. ``"skill.yaml"``).  Case-sensitive.
        title: Header text shown at the top of the modal.
        start_path: Filesystem location the DirectoryTree opens at.
            Defaults to ``Path.home()``.

    Posts:
        :class:`FilePickerModal.FileSelected` on Confirm.

    Bindings (priority=True so they win over DirectoryTree's defaults):

    * ``escape`` — cancel and dismiss.
    * ``ctrl+s`` — confirm (alias for the button click).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+s", "confirm", "Confirm", priority=True),
    ]

    DEFAULT_CSS = """
    FilePickerModal {
        align: center middle;
    }
    FilePickerModal > Vertical {
        width: 80%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }
    FilePickerModal #fp-title {
        height: 1;
        text-style: bold;
        color: $primary;
    }
    FilePickerModal #fp-status {
        height: 1;
        color: $text-muted;
    }
    FilePickerModal DirectoryTree {
        height: 1fr;
        margin: 1 0;
    }
    FilePickerModal #fp-actions {
        height: 3;
        align: right middle;
    }
    FilePickerModal #fp-actions Button {
        margin: 0 1;
    }
    """

    class FileSelected(Message):
        """Posted when the operator confirms a selection.

        Attributes:
            path: Absolute :class:`Path` of the selected file.  The
                receiving screen typically reads ``path.parent`` to
                find the upload unit.
        """

        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def __init__(
        self,
        target_filename: str,
        *,
        title: str = "Pick a file",
        start_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._target_filename = target_filename
        self._title = title
        self._start_path = start_path or Path.home()
        # Currently-highlighted file (None until DirectoryTree's
        # FileSelected event fires).  Confirm reads this.
        self._selected_path: Path | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, id="fp-title")
            yield Static(
                f"[dim]Navigate with arrow keys; Enter to descend; "
                f"select a file named [b]{self._target_filename}[/b][/dim]",
                id="fp-status",
            )
            yield DirectoryTree(str(self._start_path), id="fp-tree")
            with Horizontal(id="fp-actions"):
                yield Button("Cancel", id="fp-cancel", variant="default")
                yield Button(
                    "Confirm",
                    id="fp-confirm",
                    variant="primary",
                    disabled=True,
                )
            yield Footer()

    # ------------------------------------------------------------------
    # DirectoryTree event hooks
    # ------------------------------------------------------------------

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        """User clicked a file (Enter on a leaf node).

        We capture the path and toggle the Confirm button based on
        whether the filename matches ``target_filename``.  We do NOT
        auto-confirm — the operator must click Confirm explicitly so
        accidental clicks during navigation can't trigger an upload.
        """
        path = Path(event.path)
        self._selected_path = path
        confirm_btn = self.query_one("#fp-confirm", Button)
        status = self.query_one("#fp-status", Static)
        if path.name == self._target_filename:
            confirm_btn.disabled = False
            status.update(
                f"[green]✓ Ready: {path}[/green]"
            )
        else:
            confirm_btn.disabled = True
            status.update(
                f"[red]Wrong filename — expected [b]{self._target_filename}[/b], "
                f"got [b]{path.name}[/b][/red]"
            )

    # ------------------------------------------------------------------
    # Button + key actions
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Confirm or Cancel buttons."""
        if event.button.id == "fp-confirm":
            self.action_confirm()
        elif event.button.id == "fp-cancel":
            self.action_cancel()

    def action_confirm(self) -> None:
        """Post FileSelected(path) and dismiss.

        Idempotent against accidental double-fires: if no path is
        selected (or the selected path's filename doesn't match), this
        is a no-op rather than dispatching a malformed message.
        """
        path = self._selected_path
        if path is None or path.name != self._target_filename:
            logger.debug(
                "file_picker: confirm pressed without valid selection "
                "(target=%r, current=%r)",
                self._target_filename,
                path,
            )
            return
        # Resolve once on egress so the receiving screen never has to
        # re-resolve, and an unstable cwd between the modal opening and
        # closing can't shift the result.
        resolved = path.resolve()
        self.dismiss()
        # post_message AFTER dismiss so the receiving screen processes
        # the upload after the modal is fully unmounted — avoids any
        # focus-stealing race during the copytree call.
        self.app.post_message(self.FileSelected(path=resolved))

    def action_cancel(self) -> None:
        """Dismiss without posting any message."""
        self.dismiss()
