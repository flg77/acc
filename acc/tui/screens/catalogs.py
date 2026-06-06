"""ACC TUI — CatalogsScreen: catalog admin (list / add / remove / prioritise).

Stage 2.4 pane consuming :mod:`acc.catalog_admin`.  Operator
manages the per-collective ``<workspace>/.acc/catalogs.yaml`` override
without leaving the TUI.

The form-submit handler builds an :class:`acc.pkg.catalog.Catalog`
via :func:`acc.catalog_admin.parse_form`; Pydantic ``ValidationError``
surfaces inline so per-field errors render in the status line.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Select,
    Static,
)

from acc import catalog_admin
from acc.pkg.catalog import Catalog
from acc.tui.widgets.nav_bar import NavigationBar

logger = logging.getLogger("acc.tui.catalogs")


_TIER_CHOICES = [
    ("trusted", "trusted"),
    ("tp", "tp"),
    ("community", "community"),
    ("self", "self"),
]
_MODE_CHOICES = [("https", "https"), ("file", "file")]


class CatalogsScreen(Screen):
    """Catalog admin pane."""

    BINDINGS = [
        Binding("n", "focus_new", "New"),
        Binding("d", "delete_highlighted", "Delete"),
        Binding("r", "refresh", "Refresh"),
        Binding("+", "raise_priority", "+Priority"),
        Binding("-", "lower_priority", "-Priority"),
    ]

    CSS = """
    CatalogsScreen {
        layout: vertical;
    }
    #catalogs-table {
        height: 1fr;
    }
    #catalogs-form {
        height: auto;
        padding: 1;
        border: solid $accent;
    }
    #catalogs-form-row1, #catalogs-form-row2, #catalogs-form-row3 {
        height: 3;
    }
    #catalogs-status {
        height: 1;
        padding: 0 1;
        color: $accent;
    }
    """

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__()
        self._workspace = workspace
        self._catalogs: list[Catalog] = []

    def compose(self) -> ComposeResult:
        yield NavigationBar(active="catalogs")
        yield Label("Configured catalogs (layered: system → user → workspace)",
                    classes="panel-label")
        yield DataTable(id="catalogs-table")
        yield Static("Add catalog", classes="panel-label")
        with Vertical(id="catalogs-form"):
            with Horizontal(id="catalogs-form-row1"):
                yield Input(placeholder="id", id="form-catalog-id")
                yield Select(_TIER_CHOICES, prompt="tier", id="form-tier")
                yield Select(_MODE_CHOICES, prompt="mode", id="form-mode")
                yield Input(placeholder="priority (100)", id="form-priority")
            with Horizontal(id="catalogs-form-row2"):
                yield Input(placeholder="url (https mode)", id="form-url")
                yield Input(placeholder="path (file mode)", id="form-path")
            with Horizontal(id="catalogs-form-row3"):
                yield Input(placeholder="oidc issuer", id="form-issuer")
                yield Input(placeholder="subject pattern (regex)",
                            id="form-subject")
                yield Input(placeholder="key_path (optional, keypair mode)",
                            id="form-key-path")
            with Horizontal():
                yield Button("Add", id="btn-catalog-add", variant="primary")
                yield Button("Clear", id="btn-catalog-clear", variant="default")
        yield Static("", id="catalogs-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#catalogs-table", DataTable)
        table.add_columns(
            "ID", "Tier", "Mode", "Endpoint", "Priority", "Signer",
        )
        table.cursor_type = "row"
        self.refresh_rows()

    # ------------------------------------------------------------------
    # Data + render
    # ------------------------------------------------------------------

    def refresh_rows(self) -> None:
        try:
            self._catalogs = catalog_admin.load(self._workspace)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"[red]load failed: {exc}[/red]")
            self._catalogs = []
            return

        table = self.query_one("#catalogs-table", DataTable)
        table.clear()
        # Sort priority desc, id asc — matches the catalog resolver
        sorted_cats = sorted(
            self._catalogs,
            key=lambda c: (-c.priority, c.id),
        )
        self._catalogs = sorted_cats
        for c in sorted_cats:
            endpoint = c.url or c.path or "—"
            signer = (
                f"keypair:{Path(c.required_signer.key_path).name}"
                if c.required_signer.key_path
                else f"oidc:{c.required_signer.issuer[:30]}"
            )
            table.add_row(
                c.id, c.tier, c.mode, endpoint[:40],
                str(c.priority), signer[:30],
                key=c.id,
            )
        self._set_status(
            f"[dim]{len(sorted_cats)} catalog(s) in workspace override[/dim]"
        )

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#catalogs-status", Static).update(markup)
        except Exception:  # pragma: no cover
            pass

    def _selected_id(self) -> str | None:
        table = self.query_one("#catalogs-table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        if not (0 <= table.cursor_row < len(self._catalogs)):
            return None
        return self._catalogs[table.cursor_row].id

    # ------------------------------------------------------------------
    # Form helpers
    # ------------------------------------------------------------------

    def _read_form(self) -> dict:
        def _v(field_id: str) -> str:
            return self.query_one(f"#{field_id}", Input).value.strip()

        def _sel(field_id: str) -> str:
            sel = self.query_one(f"#{field_id}", Select)
            return str(sel.value or "")

        priority_raw = _v("form-priority")
        return {
            "catalog_id": _v("form-catalog-id"),
            "tier": _sel("form-tier"),
            "mode": _sel("form-mode"),
            "url": _v("form-url"),
            "path": _v("form-path"),
            "issuer": _v("form-issuer"),
            "subject_pattern": _v("form-subject"),
            "key_path": _v("form-key-path"),
            "priority": int(priority_raw) if priority_raw.isdigit() else 100,
        }

    def _clear_form(self) -> None:
        for field_id in (
            "form-catalog-id", "form-priority", "form-url", "form-path",
            "form-issuer", "form-subject", "form-key-path",
        ):
            try:
                self.query_one(f"#{field_id}", Input).value = ""
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_rows()

    def action_focus_new(self) -> None:
        self.query_one("#form-catalog-id", Input).focus()

    def action_delete_highlighted(self) -> None:
        cid = self._selected_id()
        if cid is None:
            self._set_status("[yellow]highlight a catalog first[/yellow]")
            return
        try:
            result = catalog_admin.remove(cid, workspace=self._workspace)
        except ValueError as exc:
            self._set_status(f"[red]{exc}[/red]")
            return
        self.refresh_rows()
        self._set_status(f"[green]✓ removed {result.catalog_id}[/green]")

    def action_raise_priority(self) -> None:
        self._bump_priority(+10)

    def action_lower_priority(self) -> None:
        self._bump_priority(-10)

    def _bump_priority(self, delta: int) -> None:
        cid = self._selected_id()
        if cid is None:
            self._set_status("[yellow]highlight a catalog first[/yellow]")
            return
        current = next((c for c in self._catalogs if c.id == cid), None)
        if current is None:
            self._set_status("[red]highlighted catalog vanished[/red]")
            return
        new_priority = max(1, min(1000, current.priority + delta))
        try:
            catalog_admin.set_priority(cid, new_priority, workspace=self._workspace)
        except ValueError as exc:
            self._set_status(f"[red]{exc}[/red]")
            return
        self.refresh_rows()
        self._set_status(
            f"[green]✓ {cid} priority {current.priority} → {new_priority}[/green]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-catalog-add":
            self._submit_form()
        elif bid == "btn-catalog-clear":
            self._clear_form()
            self._set_status("[dim]form cleared[/dim]")

    def _submit_form(self) -> None:
        try:
            form = self._read_form()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"[red]form read failed: {exc}[/red]")
            return

        try:
            cat = catalog_admin.parse_form(**form)
        except ValidationError as exc:
            # Pluck the first error's location + message for the status line.
            first_err = exc.errors()[0] if exc.errors() else None
            if first_err:
                loc = ".".join(str(p) for p in first_err.get("loc", ()))
                msg = first_err.get("msg", str(exc))
                self._set_status(f"[red]invalid {loc}: {msg}[/red]")
            else:
                self._set_status(f"[red]invalid form: {exc}[/red]")
            return
        try:
            result = catalog_admin.add(cat, workspace=self._workspace)
        except ValueError as exc:
            self._set_status(f"[red]{exc}[/red]")
            return
        self._clear_form()
        self.refresh_rows()
        self._set_status(f"[green]✓ added {result.catalog_id}[/green]")


__all__ = ["CatalogsScreen"]
