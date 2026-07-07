"""ACC TUI — MarketplaceScreen: package discovery from the built-in + layered catalogs.

Lists every ``@scope/name`` package: the **built-in** ACC role families (bundled,
shown offline day-0 with full metadata — description + role / skill / MCP counts)
plus anything advertised across the operator's layered catalogs (system → user →
workspace).  Filters by name, rates packages locally (``+`` / ``-``), and stages
an install via PROPOSE_INFUSE → the Compliance pane's Package Proposals queue.

This screen is the *discovery* surface; the Compliance pane is the *install*
surface.  They compose: Install here produces a PROPOSE_INFUSE marker the
Compliance pane renders for the operator to approve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, DataTable, Footer, Input, Label, Static

from acc.marketplace import (
    MarketplaceRow,
    _format_install_marker,
    _TIER_BADGE,
    render_rows,
)
from acc.pkg.builtin_catalog import load_builtin_catalog
from acc.pkg.ratings import get_rating, set_rating, stars_glyph
from acc.tui.widgets.nav_bar import NavigationBar, NavScreen

logger = logging.getLogger("acc.tui.marketplace")


@dataclass(frozen=True)
class _MarketDisplayRow:
    """One flattened display row — built-in packages carry counts + a
    description; configured-catalog packages fill the same columns with ``—``
    where the minimal catalog index has nothing richer."""

    name: str
    description: str
    version: str
    tier_badge: str
    catalog_name: str
    signer: str
    n_skills: str          # "7" or "—"
    n_mcps: str            # "2" or "—"
    install_marker: str


class _StageInstall(Message):
    """Bubbles a PROPOSE_INFUSE marker up to the app for dispatch."""

    def __init__(self, marker_text: str, name: str) -> None:
        super().__init__()
        self.marker_text = marker_text
        self.name = name


class MarketplaceScreen(NavScreen):
    """Package discovery + one-tap install staging + local ratings."""

    BINDINGS = [
        Binding("/", "focus_filter", "Filter"),
        Binding("enter", "install_highlighted", "Install"),
        Binding("r", "refresh", "Refresh"),
        # `1`–`9` are the pane-nav keys (NavScreen), so rate with +/-.
        Binding("plus", "rate_up", "★+"),
        Binding("minus", "rate_down", "★−"),
    ]

    CSS = """
    MarketplaceScreen { layout: vertical; }
    #market-filter-row { height: 3; padding: 0 1; }
    #market-table { height: 1fr; }
    #market-status { height: auto; padding: 0 1; color: $accent; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[_MarketDisplayRow] = []
        self._filter_text: str = ""

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="marketplace", id="nav")
        with Horizontal(id="market-filter-row"):
            yield Label("Filter: ")
            yield Input(placeholder="@scope/name…", id="market-filter-input")
            yield Button("Refresh", id="btn-market-refresh", variant="default")
        yield DataTable(id="market-table")
        yield Static(
            "[dim]↑/↓ select · Enter install · +/- rate · / filter · r refresh[/dim]",
            id="market-status",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#market-table", DataTable)
        table.add_columns(
            "Package", "Description", "Version", "Tier",
            "Catalog", "Origin/Signer", "Skills", "MCPs", "★",
        )
        table.cursor_type = "row"
        self.refresh_rows()

    # ------------------------------------------------------------------
    # Data + render
    # ------------------------------------------------------------------

    def _collect_rows(self) -> list[_MarketDisplayRow]:
        """Built-in packages (rich) + configured-catalog packages (minimal),
        de-duplicated by (name, version), name-sorted."""
        flt = (self._filter_text or "").lower()
        rows: list[_MarketDisplayRow] = []
        seen: set[tuple[str, str]] = set()

        cat = load_builtin_catalog()
        badge = _TIER_BADGE.get(cat.tier, f"[{cat.tier.upper()}]")
        for pkg in cat.packages:
            if flt and flt not in pkg.name.lower():
                continue
            rows.append(_MarketDisplayRow(
                name=pkg.name,
                description=pkg.description or "—",
                version=pkg.version,
                tier_badge=badge,
                catalog_name=cat.name,
                signer=cat.signer or "—",
                n_skills=str(pkg.n_skills),
                n_mcps=str(pkg.n_mcps),
                install_marker=_format_install_marker(
                    pkg.name, f"^{pkg.version.split('-')[0]}"),
            ))
            seen.add((pkg.name, pkg.version))

        # Operator-configured layered catalogs — the minimal index has no
        # description/counts, so those columns show "—".
        try:
            for mr in render_rows(name_filter=self._filter_text or None):
                if (mr.name, mr.version) in seen:
                    continue
                rows.append(self._row_from_marketplace(mr))
                seen.add((mr.name, mr.version))
        except Exception:  # noqa: BLE001 — never let a bad catalog blank the pane
            logger.exception("marketplace: layered catalog query failed")

        rows.sort(key=lambda r: r.name)
        return rows

    @staticmethod
    def _row_from_marketplace(mr: MarketplaceRow) -> _MarketDisplayRow:
        return _MarketDisplayRow(
            name=mr.name,
            description="—",
            version=mr.version,
            tier_badge=mr.tier_badge,
            catalog_name=mr.catalog_id,
            signer=mr.signer,
            n_skills="—",
            n_mcps="—",
            install_marker=mr.install_marker,
        )

    def refresh_rows(self) -> None:
        try:
            self._rows = self._collect_rows()
        except Exception as exc:  # noqa: BLE001
            logger.exception("marketplace: collect rows failed")
            self._set_status(f"[red]load failed: {exc}[/red]")
            self._rows = []
            return
        table = self.query_one("#market-table", DataTable)
        table.clear()
        for r in self._rows:
            table.add_row(
                r.name, (r.description or "—")[:44], r.version, r.tier_badge,
                r.catalog_name[:22], r.signer[:24], r.n_skills, r.n_mcps,
                stars_glyph(get_rating(r.name)),
                key=f"{r.name}@{r.version}@{r.catalog_name}",
            )
        if not self._rows:
            self._set_status("[dim]no packages match the current filter[/dim]")
        else:
            self._set_status(
                f"[green]{len(self._rows)} package(s)[/green] "
                "[dim]· ↑/↓ select · Enter install · +/- rate[/dim]"
            )

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#market-status", Static).update(markup)
        except Exception:  # pragma: no cover
            pass

    def _highlighted_row(self) -> _MarketDisplayRow | None:
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
        self.post_message(_StageInstall(row.install_marker, row.name))
        self._set_status(
            f"[green]✓ staged install for {row.name}@{row.version}; "
            "approve in Compliance pane → Package Proposals[/green]"
        )

    def _rate_delta(self, delta: int) -> None:
        row = self._highlighted_row()
        if row is None:
            self._set_status("[yellow]highlight a package to rate[/yellow]")
            return
        new = max(0, min(5, get_rating(row.name) + delta))
        try:
            set_rating(row.name, new)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"[red]rating failed: {exc}[/red]")
            return
        self.refresh_rows()
        self._set_status(
            f"[green]rated {row.name}: {stars_glyph(new)}[/green] "
            "[dim](local ~/.acc/ratings.yaml)[/dim]"
        )

    def action_rate_up(self) -> None:
        self._rate_delta(+1)

    def action_rate_down(self) -> None:
        self._rate_delta(-1)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-market-refresh":
            self.refresh_rows()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "market-filter-input":
            self._filter_text = event.value.strip()
            self.refresh_rows()


__all__ = ["MarketplaceScreen", "_StageInstall"]
