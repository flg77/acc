"""`WorkspaceSelectModal` — pick / create the trusted working directory
(D-007 / PR-X recreate-on-select).

The operator opens this from the Prompt screen's "+" button.  It
browses the host **browse root** (``ACC_WORKSPACE_BASE``, mounted
READ-ONLY into the TUI at ``/host-home``), lets the operator highlight
an existing directory OR type a new folder name to create one, and on
Confirm writes an *apply request* naming the chosen **host** path.

Why an apply request rather than mounting directly: a containerised
TUI cannot mount a new path into already-running agent containers.  So
the host-side ``acc-apply-watcher`` picks up the request and runs
``acc-deploy.sh apply-workspace <host_path>``, which mkdir's the path,
writes the trust sentinel, and recreates ONLY the agent services with
that directory bind-mounted at ``/workspace``.  The operator's TUI
session and the agents' LanceDB / Redis / NATS memory survive.

The container browses read-only and never writes to the operator's
home — mkdir + trust happen host-side.  Result is the chosen host path
``str`` (or ``None`` on cancel); the Prompt screen surfaces it.
"""

from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Static


def browse_root() -> Path:
    """In-container path the picker browses (the read-only home mount).

    Override via ``ACC_WORKSPACE_BROWSE_ROOT`` (tests / non-container
    runs); defaults to ``/host-home`` where the compose file mounts
    ``ACC_WORKSPACE_BASE`` read-only.
    """
    raw = os.environ.get("ACC_WORKSPACE_BROWSE_ROOT", "").strip()
    return Path(raw) if raw else Path("/host-home")


def base_host_path() -> str:
    """Host path that ``browse_root`` corresponds to (``ACC_WORKSPACE_BASE``).

    A pick of ``<browse_root>/<rel>`` maps to host ``<base>/<rel>`` —
    that host path is what rides the apply request, because the watcher
    mounts the host path into the agents.
    """
    return os.environ.get("ACC_WORKSPACE_BASE", "").strip()


class WorkspaceSelectModal(ModalScreen[str | None]):
    """Read-only directory picker rooted at the host browse mount.

    Dismisses with the chosen HOST path (str), or ``None`` on cancel.
    """

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
    WorkspaceSelectModal #ws-status { height: 2; color: $text-muted; }
    WorkspaceSelectModal DirectoryTree { height: 1fr; margin: 1 0; }
    WorkspaceSelectModal #ws-newrow { height: 3; }
    WorkspaceSelectModal #ws-actions { height: 3; align: right middle; }
    WorkspaceSelectModal #ws-actions Button { margin: 0 1; }
    """

    def __init__(
        self,
        browse: Path | None = None,
        base: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._browse = browse or browse_root()
        self._base = base if base is not None else base_host_path()
        # Directory currently highlighted in the tree, as a container
        # path under self._browse.  Defaults to the browse root.
        self._selected: Path = self._browse

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"Select working directory (host: {self._base or '?'})",
                id="ws-title",
            )
            yield Static(
                "[dim]Highlight a directory, or type a new folder name "
                "below to create one.  Confirm requests it — the agents "
                "restart onto it (your TUI session + memory survive).[/dim]",
                id="ws-status",
            )
            # DirectoryTree needs an existing root; fall back to home
            # when the mount is absent (dev workstation).
            tree_root = self._browse if self._browse.is_dir() else Path.home()
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
        from acc.workspace_apply import write_apply_request  # noqa: PLC0415

        if not self._base:
            self._set_status(
                "[red]ACC_WORKSPACE_BASE not set — the host browse root "
                "is not configured (redeploy with PR-X compose).[/red]"
            )
            return

        # Path of the highlighted dir RELATIVE to the browse root.
        try:
            rel = self._selected.resolve().relative_to(self._browse.resolve())
        except (ValueError, OSError):
            # Highlight outside the browse root (dev fallback) — treat the
            # browse root itself as the selection.
            rel = Path(".")

        try:
            newname = self.query_one("#ws-newname", Input).value.strip()
        except Exception:
            newname = ""
        if newname:
            if "/" in newname or "\\" in newname or newname in ("..", "."):
                self._set_status("[red]invalid folder name[/red]")
                return
            rel = rel / newname

        # Compose the HOST path: <base>/<rel>.  ``rel`` of "." means the
        # base itself.
        rel_str = "" if str(rel) == "." else str(rel)
        host_path = self._base.rstrip("/")
        if rel_str:
            host_path = f"{host_path}/{rel_str}"

        try:
            write_apply_request(host_path, requested_by="tui")
        except OSError as exc:
            self._set_status(f"[red]could not write apply request: {exc}[/red]")
            return
        self.dismiss(host_path)

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#ws-status", Static).update(markup)
        except Exception:
            pass
