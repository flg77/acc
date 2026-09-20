"""ACC TUI — CatalogsScreen: catalog admin (list / add / remove / prioritise).

Stage 2.4 pane consuming :mod:`acc.catalog_admin`.  Shows every catalog
layer the resolver uses — the bundled day-0 catalog, the compiled-in
default, ``/etc/acc/catalogs.yaml`` (system; the operator renders the
in-cluster AccCatalogs there), ``~/.acc/catalogs.yaml`` (user) and
``<workspace>/.acc/catalogs.yaml`` (workspace).  Only the workspace layer
is editable here; the other rows are read-only.

The form-submit handler builds an :class:`acc.pkg.catalog.Catalog`
via :func:`acc.catalog_admin.parse_form`; Pydantic ``ValidationError``
surfaces inline so per-field errors render in the status line.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Collapsible,
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
from acc.tui.env_gate import gate, refuse
from acc.tui.widgets.nav_bar import NavigationBar, NavScreen

logger = logging.getLogger("acc.tui.catalogs")


_TIER_CHOICES = [
    ("trusted", "trusted"),
    ("tp", "tp"),
    ("community", "community"),
    ("self", "self"),
]
_MODE_CHOICES = [("https", "https"), ("file", "file")]

#: Layer label of row 0 — the offline day-0 content shipped in the package.
BUNDLED_LAYER = "bundled"

#: Table cells never carry more than this many characters of a URL.
_URL_CELL_MAX = 36


def _table_url(value: str, limit: int = _URL_CELL_MAX) -> str:
    """A URL as a table cell: scheme dropped, ellipsised when long.

    The TUI runs inside web terminals (ttyd / xterm.js) that turn any
    ``http(s)://`` text into a clickable link.  A cell cut at a fixed width
    became a clickable link to a URL that does not exist (``…/acc-ecos`` →
    404).  Without the scheme the cell is not auto-linked, so shortening it is
    harmless; the detail pane below the table shows the full URL.
    """
    text = (value or "").strip()
    if not text:
        return "—"
    for scheme in ("https://", "http://"):
        if text.startswith(scheme):
            text = text[len(scheme):]
            break
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


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
        height: auto;
        max-height: 12;
    }
    #catalogs-detail-scroll {
        height: 1fr;
        min-height: 8;
        padding: 0 1;
    }
    #catalogs-detail {
        height: auto;
        color: $text-muted;
    }
    #catalogs-form-collapsible {
        height: auto;
    }
    #catalogs-form {
        height: auto;
        padding: 0 1;
        border: solid $accent;
    }
    #catalogs-form-row1, #catalogs-form-row2, #catalogs-form-row3,
    #catalogs-form-actions {
        height: auto;
    }
    #catalogs-status {
        height: auto;
        max-height: 3;
        padding: 0 1;
        color: $accent;
    }
    """

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__()
        self._workspace = workspace
        self._catalogs: list[Catalog] = []          # editable workspace override
        self._builtin: BuiltinCatalog = BuiltinCatalog(packages=[])  # read-only, row 0
        # Every layered catalog, in table order (table row = index + 1).
        self._layered: list[catalog_admin.LayeredCatalog] = []

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="catalogs", id="nav")
        yield Label(
            "Catalogs (layered: default → system → user → workspace; "
            "only workspace rows are editable here)",
            classes="panel-label",
        )
        yield DataTable(id="catalogs-table")
        with VerticalScroll(id="catalogs-detail-scroll"):
            yield Static(
                "[dim]Select a catalog to see its details.[/dim]",
                id="catalogs-detail",
            )
        with Collapsible(
            title="Add catalog (workspace layer) — press n",
            collapsed=True,
            id="catalogs-form-collapsible",
        ):
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
                with Horizontal(id="catalogs-form-actions"):
                    yield Button("Add", id="btn-catalog-add", variant="primary")
                    yield Button("Clear", id="btn-catalog-clear", variant="default")
        yield Static("", id="catalogs-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#catalogs-table", DataTable)
        table.add_columns(
            "id", "layer", "name", "roles", "description", "url", "oidc issuer",
        )
        table.cursor_type = "row"
        self.refresh_rows()
        reason = gate(self, "catalog.write", "#catalogs-form-collapsible")
        if reason:
            self._set_status(f"[dim]read-only here — {reason}[/dim]")

    # ------------------------------------------------------------------
    # Data + render
    # ------------------------------------------------------------------

    def refresh_rows(self) -> None:
        self._builtin = load_builtin_catalog()
        errors: list[catalog_admin.LayerError] = []
        try:
            self._layered, errors = catalog_admin.load_layers(self._workspace)
        except Exception as exc:  # noqa: BLE001
            logger.exception("catalogs: layered load failed")
            self._set_status(f"[red]load failed: {exc}[/red]")
            self._layered = []
        self._catalogs = [
            e.catalog for e in self._layered
            if e.layer == catalog_admin.EDITABLE_LAYER
        ]

        table = self.query_one("#catalogs-table", DataTable)
        table.clear()

        # Row 0 — the bundled day-0 catalog (role families, read-only).
        b = self._builtin
        roles = b.all_roles()
        roles_cell = f"{len(roles)}: {', '.join(roles[:2])}…" if roles else "—"
        table.add_row(
            b.id, BUNDLED_LAYER, b.name, roles_cell,
            (b.description or "—")[:38], _table_url(b.url),
            _table_url(b.oidc_issuer), key=b.id,
        )
        # Every layer the resolver reads.  The minimal catalog model has no
        # name/roles, so those columns are derived/dashed.
        for i, entry in enumerate(self._layered):
            c = entry.catalog
            note = f"tier {c.tier} · prio {c.priority}"
            if entry.shadowed_by:
                note += f" · shadowed by {entry.shadowed_by}"
            table.add_row(
                c.id, entry.layer, c.id, "—", note,
                _table_url(c.url or c.path), _table_url(c.required_signer.issuer),
                key=f"{entry.layer}:{c.id}:{i}",
            )
        status = (
            f"[dim]1 bundled + {len(self._layered)} layered catalog(s) · "
            f"{len(self._catalogs)} editable (workspace)[/dim]"
        )
        if errors:
            status += "\n" + "\n".join(
                f"[red]{e.layer} layer unreadable ({escape(str(e.path))}): "
                f"{escape(e.error)}[/red]"
                for e in errors
            )
        self._set_status(status)
        self._update_detail(0)

    def _entry_for_row(self, row: int | None) -> catalog_admin.LayeredCatalog | None:
        if not row:
            return None
        idx = row - 1
        if 0 <= idx < len(self._layered):
            return self._layered[idx]
        return None

    def _update_detail(self, row: int | None) -> None:
        """Show the highlighted catalog in full: the bundled catalog's roles
        one-per-line, or a layered catalog's source, endpoint and signer."""
        try:
            detail = self.query_one("#catalogs-detail", Static)
        except Exception:
            return
        if not row:  # row 0 or None → the bundled catalog
            b = self._builtin
            roles = b.all_roles()
            lines = [
                f"[b]{b.id}[/b] — {b.name}  [dim]({BUNDLED_LAYER}, read-only)[/dim]",
                b.description or "",
                f"[dim]url:[/dim] {b.url or '—'}",
                f"[dim]{len(b.packages)} packs · {len(roles)} roles · "
                f"signer {b.signer or '—'}[/dim]",
                "[b]Roles:[/b]",
            ]
            lines += [f"  • {r}" for r in roles] or ["  [dim](none)[/dim]"]
            detail.update("\n".join(x for x in lines if x))
            return
        entry = self._entry_for_row(row)
        if entry is None:
            detail.update("[dim]Select a catalog to see its details.[/dim]")
            return
        c = entry.catalog
        signer = c.required_signer
        access = "editable" if not entry.read_only else "read-only"
        source = str(entry.source) if entry.source else "compiled into ACC"
        # Catalog fields are operator-supplied (a subject pattern is a regex
        # full of '[') — escape them so they never parse as markup.
        lines = [
            f"[b]{escape(c.id)}[/b]  [dim]({entry.layer} layer · {access})[/dim]",
            f"[dim]source:[/dim] {escape(source)}",
            f"[dim]tier:[/dim] {c.tier}   [dim]mode:[/dim] {c.mode}   "
            f"[dim]priority:[/dim] {c.priority}",
            f"[dim]endpoint:[/dim] {escape(c.url or c.path or '—')}",
            f"[dim]signer issuer:[/dim] {escape(signer.issuer or '—')}",
            f"[dim]signer subject:[/dim] {escape(signer.subject_pattern or '—')}",
        ]
        if signer.key_path:
            lines.append(f"[dim]signer key:[/dim] {escape(signer.key_path)}")
        if entry.shadowed_by:
            lines.append(
                f"[yellow]shadowed by the {entry.shadowed_by} layer — that "
                f"catalog with the same id is the one resolved[/yellow]"
            )
        lines.append(
            "[dim]Its packages + roles are listed in the Marketplace pane.[/dim]"
        )
        detail.update("\n".join(lines))

    def on_data_table_row_highlighted(self, event) -> None:
        if getattr(event, "data_table", None) is not None and \
                event.data_table.id == "catalogs-table":
            self._update_detail(event.cursor_row)

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#catalogs-status", Static).update(markup)
        except Exception:  # pragma: no cover
            pass

    def _editable_selection(self) -> str | None:
        """The highlighted catalog's id when it is a workspace row, else None
        (with the reason in the status line)."""
        table = self.query_one("#catalogs-table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            self._set_status("[yellow]highlight a catalog first[/yellow]")
            return None
        row = table.cursor_row
        if row == 0:
            self._set_status(
                f"[yellow]the {BUNDLED_LAYER} catalog is read-only[/yellow]"
            )
            return None
        entry = self._entry_for_row(row)
        if entry is None:
            self._set_status("[red]highlighted catalog vanished[/red]")
            return None
        if entry.read_only:
            where = f" ({escape(str(entry.source))})" if entry.source else ""
            self._set_status(
                f"[yellow]{escape(entry.catalog.id)} is in the {entry.layer} layer"
                f"{where} — read-only here; only workspace catalogs can be "
                f"changed[/yellow]"
            )
            return None
        return entry.catalog.id

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
        if refuse(self, "catalog.write"):
            return
        self.query_one("#catalogs-form-collapsible", Collapsible).collapsed = False
        self.call_after_refresh(self.query_one("#form-catalog-id", Input).focus)

    def action_delete_highlighted(self) -> None:
        if refuse(self, "catalog.write"):
            return
        cid = self._editable_selection()
        if cid is None:
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
        if refuse(self, "catalog.write"):
            return
        cid = self._editable_selection()
        if cid is None:
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
