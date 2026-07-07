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
from acc.pkg.builtin_catalog import BuiltinCatalog, load_builtin_catalog
from acc.pkg.catalog import Catalog
from acc.tui.widgets.nav_bar import NavigationBar, NavScreen

logger = logging.getLogger("acc.tui.catalogs")


_TIER_CHOICES = [
    ("trusted", "trusted"),
    ("tp", "tp"),
    ("community", "community"),
    ("self", "self"),
]
_MODE_CHOICES = [("https", "https"), ("file", "file")]


class CatalogsScreen(NavScreen):
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
        height: 12;
    }
    #catalogs-detail {
        height: 1fr;
        min-height: 4;
        padding: 0 1;
        color: $text-muted;
        overflow-y: auto;
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
        self._catalogs: list[Catalog] = []          # editable workspace override
        self._builtin: BuiltinCatalog = BuiltinCatalog(packages=[])  # read-only, row 0

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="catalogs", id="nav")
        yield Label("Configured catalogs (layered: system → user → workspace)",
                    classes="panel-label")
        yield DataTable(id="catalogs-table")
        yield Static(
            "[dim]Select a catalog to see its roles (one per line).[/dim]",
            id="catalogs-detail",
        )
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
            "id", "name", "roles", "description", "url", "oidc issuer",
        )
        table.cursor_type = "row"
        self.refresh_rows()

    # ------------------------------------------------------------------
    # Data + render
    # ------------------------------------------------------------------

    def refresh_rows(self) -> None:
        self._builtin = load_builtin_catalog()
        try:
            self._catalogs = catalog_admin.load(self._workspace)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"[red]load failed: {exc}[/red]")
            self._catalogs = []
        # Sort priority desc, id asc — matches the catalog resolver.
        self._catalogs = sorted(self._catalogs, key=lambda c: (-c.priority, c.id))

        table = self.query_one("#catalogs-table", DataTable)
        table.clear()

        # Row 0 — the built-in day-0 catalog (bundled role families, read-only).
        b = self._builtin
        roles = b.all_roles()
        roles_cell = f"{len(roles)}: {', '.join(roles[:2])}…" if roles else "—"
        table.add_row(
            b.id, b.name, roles_cell,
            (b.description or "—")[:38], (b.url or "—")[:32],
            (b.oidc_issuer or "—")[:32], key=b.id,
        )
        # Configured (workspace-override) catalogs — editable; the minimal
        # catalog model has no name/roles, so those columns are derived/dashed.
        for c in self._catalogs:
            endpoint = c.url or c.path or "—"
            oidc = (c.required_signer.issuer or "—")
            table.add_row(
                c.id, c.id, "—",
                f"configured · tier {c.tier} · prio {c.priority}",
                endpoint[:32], oidc[:32], key=c.id,
            )
        self._set_status(
            f"[dim]1 built-in + {len(self._catalogs)} configured catalog(s)[/dim]"
        )
        self._update_detail(0)

    def _update_detail(self, row: int | None) -> None:
        """Show the highlighted catalog's roles one-per-line (built-in) or a
        pointer to the Marketplace (configured catalogs)."""
        try:
            detail = self.query_one("#catalogs-detail", Static)
        except Exception:
            return
        if not row:  # row 0 or None → the built-in catalog
            b = self._builtin
            roles = b.all_roles()
            lines = [
                f"[b]{b.id}[/b] — {b.name}",
                b.description or "",
                f"[dim]{len(b.packages)} packs · {len(roles)} roles · "
                f"signer {b.signer or '—'}[/dim]",
                "[b]Roles:[/b]",
            ]
            lines += [f"  • {r}" for r in roles] or ["  [dim](none)[/dim]"]
            detail.update("\n".join(x for x in lines if x))
            return
        idx = row - 1
        if 0 <= idx < len(self._catalogs):
            c = self._catalogs[idx]
            detail.update(
                f"[b]{c.id}[/b]  [dim](configured · tier {c.tier})[/dim]\n"
                f"[dim]endpoint:[/dim] {c.url or c.path or '—'}\n"
                f"[dim]signer:[/dim] {c.required_signer.issuer or '—'}\n"
                "[dim]Its packages + roles are listed in the Marketplace pane.[/dim]"
            )
        else:
            detail.update("[dim]Select a catalog to see its roles.[/dim]")

    def on_data_table_row_highlighted(self, event) -> None:
        if getattr(event, "data_table", None) is not None and \
                event.data_table.id == "catalogs-table":
            self._update_detail(event.cursor_row)

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#catalogs-status", Static).update(markup)
        except Exception:  # pragma: no cover
            pass

    def _selected_id(self) -> str | None:
        table = self.query_one("#catalogs-table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        row = table.cursor_row
        if row == 0:
            return self._builtin.id  # built-in — read-only (guarded in handlers)
        idx = row - 1
        if 0 <= idx < len(self._catalogs):
            return self._catalogs[idx].id
        return None

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
        if cid == self._builtin.id:
            self._set_status("[yellow]the built-in catalog is read-only[/yellow]")
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
        if cid == self._builtin.id:
            self._set_status("[yellow]the built-in catalog is read-only[/yellow]")
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
