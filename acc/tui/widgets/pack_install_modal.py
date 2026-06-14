"""`PackInstallModal` — load a role pack into the ecosystem from the TUI.

Opened from the Ecosystem (genome) screen with ``g`` ("get pack").  The
operator types a catalog pack spec (``@scope/name`` with an optional
``@constraint``) and, optionally, a catalog id to pin resolution.  On Install
the Ecosystem screen fetches + verifies + installs the pack from the catalog
(the same ``fetch_and_install_closure`` path as ``acc-deploy.sh pkg add`` and
``acc-pkg install``) and refreshes the role roster so the pack's roles can be
infused.

Result is a dict ``{"spec": str, "catalog": str | None}`` on Install, or
``None`` on cancel.
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class PackInstallModal(ModalScreen[Optional[dict]]):
    """Prompt for a catalog pack spec to fetch + install."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    PackInstallModal {
        align: center middle;
    }
    PackInstallModal #pack-install-panel {
        width: 70%;
        max-width: 90;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    PackInstallModal #pack-install-title {
        text-style: bold;
        margin-bottom: 1;
    }
    PackInstallModal .pack-install-hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    PackInstallModal Input {
        margin-bottom: 1;
    }
    PackInstallModal #pack-install-buttons {
        height: auto;
        align-horizontal: right;
    }
    PackInstallModal #pack-install-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Vertical(id="pack-install-panel"):
            yield Static("Load a role pack from the catalog", id="pack-install-title")
            yield Static(
                "Fetches + verifies + installs a signed family pack, then its "
                "roles appear in the genome browser to infuse.",
                classes="pack-install-hint",
            )
            yield Input(
                placeholder="@scope/name[@constraint]   e.g. @acc/workspace-roles@^1.0",
                id="pack-install-spec",
            )
            yield Input(
                placeholder="catalog id (optional — pin resolution to one catalog)",
                id="pack-install-catalog",
            )
            with Horizontal(id="pack-install-buttons"):
                yield Button("Install", id="pack-install-go", variant="primary")
                yield Button("Cancel", id="pack-install-cancel", variant="default")

    def on_mount(self) -> None:
        self.query_one("#pack-install-spec", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in either field submits the form.
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pack-install-go":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        spec = self.query_one("#pack-install-spec", Input).value.strip()
        if not spec:
            self.query_one("#pack-install-spec", Input).focus()
            return
        catalog = self.query_one("#pack-install-catalog", Input).value.strip() or None
        self.dismiss({"spec": spec, "catalog": catalog})
