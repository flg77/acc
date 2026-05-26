"""`WorkspaceSelectModal` — pick / create the trusted working directory
(D-007 / PR-X recreate-on-select; PR-X2 full-tree navigation).

The operator opens this from the Prompt screen's "+" button.  It is a
small file-manager: a **location bar** showing where you are, a
navigable :class:`~textual.widgets.DirectoryTree`, an **Up** affordance
to climb toward the filesystem root, and a *new folder* field to create
a directory.  It runs in one of two modes:

* **Host-mapped mode** (``ACC_WORKSPACE_BASE`` set).  A containerised
  TUI cannot mount a new path into already-running agent containers, so
  it browses the host **browse root** (``ACC_WORKSPACE_BASE`` mounted
  READ-ONLY at ``/host-home``) and, on Confirm, writes an *apply request*
  naming the chosen **host** path.  The host-side ``acc-apply-watcher``
  then runs ``acc-deploy.sh apply-workspace <host_path>``, which mkdir's
  the path, writes the trust sentinel, and recreates ONLY the agent
  services with that directory bind-mounted at ``/workspace`` — the
  operator's TUI session and the agents' memory survive.  The selection
  must live under the browse root (the only host-mapped subtree); the
  container never writes to the operator's home.

* **Local mode** (``ACC_WORKSPACE_BASE`` unset — a TUI run directly on
  the workstation).  There is no host/container split, so the picker
  navigates the **real local filesystem**, creates the new folder
  directly, and Confirm returns the chosen absolute path verbatim.

Result is the chosen path ``str`` (host path in host-mapped mode, local
absolute path in local mode), or ``None`` on cancel; the Prompt screen
surfaces it.
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
    """In-container path the picker opens at (the read-only home mount).

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
    mounts the host path into the agents.  Empty ⇒ local mode.
    """
    return os.environ.get("ACC_WORKSPACE_BASE", "").strip()


class WorkspaceSelectModal(ModalScreen[str | None]):
    """Navigable directory picker.

    Dismisses with the chosen path (str), or ``None`` on cancel.  In
    host-mapped mode the path is the HOST path; in local mode it is the
    selected absolute local path.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+s", "confirm", "Confirm", priority=True),
        Binding("alt+up", "go_up", "Up", priority=True),
    ]

    DEFAULT_CSS = """
    WorkspaceSelectModal { align: center middle; }
    WorkspaceSelectModal > Vertical {
        width: 80%; height: 80%;
        border: thick $primary; background: $surface; padding: 1;
    }
    WorkspaceSelectModal #ws-title { height: 1; text-style: bold; color: $primary; }
    WorkspaceSelectModal #ws-status { height: 2; color: $text-muted; }
    WorkspaceSelectModal #ws-locrow { height: 3; }
    WorkspaceSelectModal #ws-path { width: 1fr; }
    WorkspaceSelectModal #ws-locrow Button { margin: 0 0 0 1; min-width: 6; }
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
        # DirectoryTree needs an existing root; fall back to home when the
        # configured mount is absent (dev workstation).
        start = self._browse if self._browse.is_dir() else Path.home()
        # Current tree root ("where we are") and the highlighted dir.
        self._root: Path = start
        self._selected: Path = start

    def compose(self) -> ComposeResult:
        with Vertical():
            mode = "host" if self._base else "local"
            yield Static(
                f"Select working directory "
                f"({'host: ' + self._base if self._base else 'local filesystem'})",
                id="ws-title",
            )
            yield Static(
                "[dim]Type a path + Enter (or Go) to jump there; Alt+↑ / Up "
                "climbs a level; highlight a directory in the tree; or type a "
                "new folder name to create one.  Ctrl+S confirms.[/dim]"
                if mode == "local"
                else "[dim]Type any host path + Enter (or Go) to jump there; "
                "Alt+↑ / Up climbs a level; highlight a directory or type a new "
                "folder name.  Confirm requests it — the agents restart onto it "
                "(your TUI session + memory survive).[/dim]",
                id="ws-status",
            )
            with Horizontal(id="ws-locrow"):
                yield Input(
                    value=self._to_host(self._root),
                    placeholder="/path/to/directory",
                    id="ws-path",
                )
                yield Button("Go", id="ws-go", variant="default")
                yield Button("↑ Up", id="ws-up", variant="default")
            yield DirectoryTree(str(self._root), id="ws-tree")
            with Horizontal(id="ws-newrow"):
                yield Input(
                    placeholder="new folder name (optional)",
                    id="ws-newname",
                )
            with Horizontal(id="ws-actions"):
                yield Button("Confirm", id="ws-confirm", variant="primary")
                yield Button("Cancel", id="ws-cancel", variant="default")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate(self, target: Path) -> None:
        """Re-root the tree at ``target`` if it is an accessible directory."""
        try:
            resolved = target.expanduser().resolve()
        except (OSError, RuntimeError):
            self._set_status(f"[red]invalid path: {target}[/red]")
            return
        if not resolved.is_dir():
            self._set_status(f"[red]not a directory: {resolved}[/red]")
            return
        try:
            # Probe readability so we fail loud rather than render an
            # empty tree on a permission error.
            os.scandir(resolved).close()
        except PermissionError:
            self._set_status(f"[red]permission denied: {resolved}[/red]")
            return
        except OSError as exc:
            self._set_status(f"[red]cannot open: {exc}[/red]")
            return

        self._root = resolved
        self._selected = resolved
        try:
            tree = self.query_one("#ws-tree", DirectoryTree)
            tree.path = str(resolved)
            tree.reload()
        except Exception:
            pass
        self._sync_path_input(resolved)
        self._set_status(f"here: {self._to_host(resolved)}")

    def action_go_up(self) -> None:
        self._navigate(self._root.parent)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Only the location bar navigates; the new-folder field is inert.
        # The bar holds a HOST path — translate to the container path.
        if (event.input.id or "") == "ws-path":
            typed = event.value.strip()
            self._navigate(self._to_container(typed) if typed else self._root)

    def on_directory_tree_directory_selected(
        self, event: "DirectoryTree.DirectorySelected",
    ) -> None:
        self._selected = Path(event.path)
        self._sync_path_input(self._selected)
        self._set_status(f"selected: {self._to_host(self._selected)}")

    # ------------------------------------------------------------------
    # Buttons / confirm / cancel
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "ws-confirm":
            self.action_confirm()
        elif bid == "ws-cancel":
            self.action_cancel()
        elif bid == "ws-go":
            try:
                value = self.query_one("#ws-path", Input).value.strip()
            except Exception:
                value = ""
            self._navigate(self._to_container(value) if value else self._root)
        elif bid == "ws-up":
            self.action_go_up()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        # Resolve the new-folder name (validated: a single path segment).
        try:
            newname = self.query_one("#ws-newname", Input).value.strip()
        except Exception:
            newname = ""
        if newname and ("/" in newname or "\\" in newname or newname in ("..", ".")):
            self._set_status("[red]invalid folder name[/red]")
            return

        if self._base:
            self._confirm_host_mapped(newname)
        else:
            self._confirm_local(newname)

    # ----- host-mapped mode (apply request) ---------------------------

    def _confirm_host_mapped(self, newname: str) -> None:
        from acc.workspace_apply import write_apply_request  # noqa: PLC0415

        # The selection must live under the browse root — that is the only
        # subtree mounted from the host, so the only one we can map.
        try:
            rel = self._selected.resolve().relative_to(self._browse.resolve())
        except (ValueError, OSError):
            self._set_status(
                "[red]selection is outside the mounted host tree — pick a "
                "directory under it.[/red]"
            )
            return
        if newname:
            rel = rel / newname

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

    # ----- local mode (direct path, mkdir) ----------------------------

    def _confirm_local(self, newname: str) -> None:
        target = self._selected
        if newname:
            target = target / newname
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._set_status(f"[red]could not create folder: {exc}[/red]")
                return
        if not target.is_dir():
            self._set_status(f"[red]not a directory: {target}[/red]")
            return
        self.dismiss(str(target.resolve()))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sync_path_input(self, path: Path) -> None:
        try:
            self.query_one("#ws-path", Input).value = self._to_host(path)
        except Exception:
            pass

    # ----- host ↔ container path translation --------------------------
    #
    # The TUI runs in a container that mounts a slice of the host fs at
    # ``browse_root`` (with ``ACC_WORKSPACE_BASE`` = / when the whole host is
    # mounted).  Internally everything works in CONTAINER paths (the tree,
    # navigation, selection); the operator only ever sees / types HOST-true
    # paths, so the location bar + status translate.  In local mode (no base)
    # the two coincide.

    def _to_host(self, container: Path) -> str:
        """Container path → host-true path for display."""
        if not self._base:
            return str(container)
        try:
            rel = Path(container).resolve().relative_to(self._browse.resolve())
        except (ValueError, OSError):
            return str(container)
        base = self._base.rstrip("/")
        # Host paths are POSIX — use forward slashes regardless of the platform
        # the TUI process runs on (the container is Linux; tests run on Windows).
        rel_str = "" if rel == Path(".") else rel.as_posix()
        return f"{base}/{rel_str}" if rel_str else (base or "/")

    def _to_container(self, host: str) -> Path:
        """Host-true path (as typed) → container path under the browse root."""
        if not self._base:
            return Path(host)
        base = self._base.rstrip("/")
        h = host.strip()
        rel = h[len(base):].lstrip("/") if base and h.startswith(base) else h.lstrip("/")
        return (self._browse / rel) if rel else self._browse

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#ws-status", Static).update(markup)
        except Exception:
            pass
