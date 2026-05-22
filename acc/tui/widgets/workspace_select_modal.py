"""`WorkspaceSelectModal` — pick / create the trusted working directory
(D-007 / PR-U2b).

The operator opens this from the Prompt screen's "Select Directory"
button.  It browses the workspace mount root (``/workspace`` inside
the acc-tui container, bind-mounted from the host with an SELinux
``:z`` label), lets the operator highlight an existing project
directory OR type a new folder name to create one, and on Confirm:

* creates the new folder if a name was given,
* marks the chosen directory *trusted*
  (``acc.workspace.mark_trusted`` writes the ``.acc-workspace-trust``
  sentinel — visible to every agent via the shared mount),
* dismisses with the chosen absolute :class:`Path`.

Result type is ``Path | None`` (None on cancel).  The Prompt screen
turns the absolute path into a project-relative path it threads into
the TASK_ASSIGN payload so agents resolve files under it.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Static


class WorkspaceSelectModal(ModalScreen[Path | None]):
    """Directory picker rooted at the workspace mount."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+s", "confirm", "Confirm", priority=True),
    ]

    DEFAULT_CSS = """
    WorkspaceSelectModal { align: center middle; }
    WorkspaceSelectModal > Vertical {
        width: 80%; height: 80%;
        border: thick $primary; background: $surface; padding: 1;
    }
    WorkspaceSelectModal #ws-title { height: 1; text-style: bold; color: $primary; }
    WorkspaceSelectModal #ws-status { height: 1; color: $text-muted; }
    WorkspaceSelectModal DirectoryTree { height: 1fr; margin: 1 0; }
    WorkspaceSelectModal #ws-newrow { height: 3; }
    WorkspaceSelectModal #ws-actions { height: 3; align: right middle; }
    WorkspaceSelectModal #ws-actions Button { margin: 0 1; }
    """

    def __init__(self, root: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        from acc.workspace import workspace_root  # noqa: PLC0415
        self._root = root or workspace_root()
        # The directory currently highlighted in the tree (defaults to
        # the root).  Confirm trusts + returns this (or the new folder
        # created under it).
        self._selected: Path = self._root

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"Select working directory (under {self._root})",
                id="ws-title",
            )
            yield Static(
                "[dim]Highlight a directory, or type a new folder name "
                "below to create one.  Confirm trusts it.[/dim]",
                id="ws-status",
            )
            # DirectoryTree needs an existing root; fall back to home
            # when the mount is absent (dev workstation).
            tree_root = self._root if self._root.is_dir() else Path.home()
            yield DirectoryTree(str(tree_root), id="ws-tree")
            with Horizontal(id="ws-newrow"):
                yield Input(
                    placeholder="new folder name (optional)",
                    id="ws-newname",
                )
            with Horizontal(id="ws-actions"):
                yield Button("Confirm", id="ws-confirm", variant="primary")
                yield Button("Cancel", id="ws-cancel", variant="default")

    def on_directory_tree_directory_selected(
        self, event: "DirectoryTree.DirectorySelected",
    ) -> None:
        self._selected = Path(event.path)
        self._set_status(f"selected: {self._selected}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "ws-confirm":
            self.action_confirm()
        elif (event.button.id or "") == "ws-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        from acc.workspace import mark_trusted  # noqa: PLC0415

        target = self._selected
        try:
            newname = self.query_one("#ws-newname", Input).value.strip()
        except Exception:
            newname = ""
        if newname:
            # Reject path separators in a new-folder name so the
            # operator can't traverse out of the highlighted dir.
            if "/" in newname or "\\" in newname or newname in ("..", "."):
                self._set_status("[red]invalid folder name[/red]")
                return
            target = target / newname
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._set_status(f"[red]mkdir failed: {exc}[/red]")
                return
        try:
            mark_trusted(target, note="selected via TUI")
        except OSError as exc:
            self._set_status(f"[red]trust failed: {exc}[/red]")
            return
        self.dismiss(target)

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#ws-status", Static).update(markup)
        except Exception:
            pass
