"""ACC TUI — MarketplaceScreen: package discovery from layered catalogs.

Stage 2.4 pane consuming :mod:`acc.marketplace`.  Lists every
``@scope/name`` advertised across all layered catalogs (system →
user → workspace), filters by name prefix, and stages an install
via PROPOSE_INFUSE — routed to the Compliance pane's Package
Proposals queue (PR #32) where the operator's approval flow lives.

This screen is the *discovery* surface; the Compliance pane is the
*install* surface.  They compose: clicking Install here produces a
PROPOSE_INFUSE marker that the Compliance pane's Package Proposals
tab renders for the operator to approve.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Static,
)

from acc.marketplace import (
    MarketplaceRow,
    list_versions,
    render_rows,
    stage_install,
)
from acc.tui.widgets.nav_bar import NavigationBar, NavScreen

if TYPE_CHECKING:
    pass

logger = logging.getLogger("acc.tui.marketplace")


class _StageInstall(Message):
    """Bubbles a PROPOSE_INFUSE marker up to the app for dispatch.

    The app's observer reads this + publishes the synthetic marker on
    the bus so the Compliance pane's Package Proposals queue picks
    it up.
    """

    def __init__(self, marker_text: str, row: MarketplaceRow) -> None:
        super().__init__()
        self.marker_text = marker_text
        self.row = row


class MarketplaceScreen(NavScreen):
    """Package discovery + one-tap install staging."""

    BINDINGS = [
        Binding("/", "focus_filter", "Filter"),
        Binding("enter", "install_highlighted", "Install"),
        Binding("r", "refresh", "Refresh"),
    ]

    CSS = """
    MarketplaceScreen {
        layout: vertical;
    }
    #market-filter-row {
        height: 3;
        padding: 0 1;
    }
    #market-table {
        height: 1fr;
    }
    #market-status {
        height: 1;
        padding: 0 1;
        color: $accent;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[MarketplaceRow] = []
        self._filter_text: str = ""

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="marketplace", id="nav")
        with Horizontal(id="market-filter-row"):
            yield Label("Filter: ")
            yield Input(placeholder="@scope/name…", id="market-filter-input")
            yield Button("Refresh", id="btn-market-refresh", variant="default")
        yield DataTable(id="market-table")
        yield Static("", id="market-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#market-table", DataTable)
        table.add_columns(
            "Package", "Version", "Tier", "Catalog", "Signer",
        )
        table.cursor_type = "row"
        self.refresh_rows()

    # ------------------------------------------------------------------
    # Data + render
    # ------------------------------------------------------------------

    def refresh_rows(self) -> None:
        """Re-query layered catalogs + re-render the table."""
        try:
            self._rows = render_rows(
                name_filter=self._filter_text or None,
            )
        except Exception as exc:  # noqa: BLE001 — surface, never crash
            logger.exception("marketplace: render_rows failed")
            self._set_status(f"[red]load failed: {exc}[/red]")
            self._rows = []
            return

        table = self.query_one("#market-table", DataTable)
        table.clear()
        for r in self._rows:
            table.add_row(
                r.name,
                r.version,
                f"{r.tier_badge}",
                r.catalog_id,
                r.signer,
                key=f"{r.name}@{r.version}@{r.catalog_id}",
            )
        if not self._rows:
            self._set_status("[dim]no packages match the current filter[/dim]")
        else:
            self._set_status(
                f"[green]{len(self._rows)} package(s) across layered catalogs[/green]"
            )

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#market-status", Static).update(markup)
        except Exception:  # pragma: no cover — widget not mounted yet
            pass

    def _highlighted_row(self) -> MarketplaceRow | None:
        table = self.query_one("#market-table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        if not (0 <= table.cursor_row < len(self._rows)):
            return None
        return self._rows[table.cursor_row]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_focus_filter(self) -> None:
        self.query_one("#market-filter-input", Input).focus()

    def action_refresh(self) -> None:
        self.refresh_rows()

    def action_install_highlighted(self) -> None:
        row = self._highlighted_row()
        if row is None:
            self._set_status("[yellow]highlight a package first[/yellow]")
            return
        try:
            marker = stage_install(row)
        except ValueError as exc:
            self._set_status(f"[red]{exc}[/red]")
            return
        self.post_message(_StageInstall(marker, row))
        self._set_status(
            f"[green]✓ staged install for {row.name}@{row.version}; "
            "approve in Compliance pane → Package Proposals[/green]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-market-refresh":
            self.refresh_rows()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "market-filter-input":
            self._filter_text = event.value.strip()
            self.refresh_rows()


# Re-export the message so the app + tests can import it
__all__ = ["MarketplaceScreen", "_StageInstall"]
