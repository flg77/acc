"""Catalogs pane shows every catalog layer the resolver reads.

Live on OpenShift (2026-09-16) the pane claimed "layered: system → user →
workspace" but listed only ``<cwd>/.acc/catalogs.yaml``: the in-cluster
workshop catalog at ``/etc/acc/catalogs.yaml`` was invisible although the
Marketplace resolved against it.  The URL column was cut to 32 characters,
which a web terminal turned into a clickable link to a URL that 404s, and the
4-row detail pane could not scroll to the roles list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

import acc.tui.app as appmod
from acc import catalog_admin
from acc.tui.screens.catalogs import CatalogsScreen, _table_url

_APP_CSS = Path(appmod.__file__).parent / "app.tcss"

LONG_URL = "https://catalogs.workshop.apps.example.internal/acc-ecosystem-workshop"


def _catalog(cid: str, *, url: str = "", path: str = "", priority: int = 100) -> dict:
    doc = {
        "id": cid, "tier": "trusted", "mode": "https" if url else "file",
        "priority": priority,
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": r"^https://github\.com/[a-z0-9-]+/",
        },
    }
    if url:
        doc["url"] = url
    else:
        doc["path"] = path
    return doc


def _write(path: Path, *catalogs: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"catalogs": list(catalogs)}), encoding="utf-8")
    return path


@pytest.fixture
def layers(tmp_path, monkeypatch):
    system = _write(tmp_path / "etc" / "catalogs.yaml",
                    _catalog("workshop", url=LONG_URL, priority=200))
    user = _write(tmp_path / "home" / "catalogs.yaml",
                  _catalog("personal", path="/srv/acc/personal"),
                  _catalog("workshop", path="/srv/acc/override"))
    workspace = tmp_path / "ws"
    _write(workspace / ".acc" / "catalogs.yaml", _catalog("local", path="/srv/local"))
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(system))
    monkeypatch.setenv("ACC_USER_CATALOG", str(user))
    return {"system": system, "user": user, "workspace": workspace}


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------


def test_load_layers_reports_every_layer_with_shadowing(layers):
    rows, errors = catalog_admin.load_layers(layers["workspace"])
    assert errors == []
    seen = [(r.layer, r.catalog.id) for r in rows]
    assert seen == [
        ("default", "acc-canonical"),
        ("system", "workshop"),
        ("user", "personal"),
        ("user", "workshop"),
        ("workspace", "local"),
    ]
    by = {(r.layer, r.catalog.id): r for r in rows}
    assert by[("system", "workshop")].shadowed_by == "user"
    assert by[("user", "workshop")].shadowed_by == ""
    assert by[("system", "workshop")].source == layers["system"]
    assert [r.read_only for r in rows] == [True, True, True, True, False]


def test_load_layers_keeps_readable_layers_when_one_is_broken(layers):
    layers["user"].write_text(": not :: yaml", encoding="utf-8")
    rows, errors = catalog_admin.load_layers(layers["workspace"])
    assert [e.layer for e in errors] == ["user"]
    assert {r.catalog.id for r in rows} == {"acc-canonical", "workshop", "local"}


def test_table_url_is_never_a_clickable_partial_link():
    cell = _table_url(LONG_URL)
    assert "://" not in cell
    assert cell.endswith("…") and len(cell) <= 36
    assert _table_url("https://flg77.github.io/acc-ecosystem") == \
        "flg77.github.io/acc-ecosystem"
    assert _table_url("") == "—"


# ---------------------------------------------------------------------------
# Pilot
# ---------------------------------------------------------------------------


class _Harness(App):
    CSS_PATH = _APP_CSS

    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self._ws = workspace

    def on_mount(self) -> None:
        self.push_screen(CatalogsScreen(workspace=self._ws))


def _rows(table: DataTable) -> list[list[str]]:
    return [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]


def _row_index(table: DataTable, layer: str, cid: str) -> int:
    for i, row in enumerate(_rows(table)):
        if row[0] == cid and row[1] == layer:
            return i
    raise AssertionError(f"{layer}/{cid} not in {_rows(table)}")


@pytest.mark.asyncio
async def test_pane_lists_system_user_and_workspace_rows(layers):
    app = _Harness(layers["workspace"])
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#catalogs-table", DataTable)
        layers_seen = {(r[1], r[0]) for r in _rows(table)}
        assert {("bundled", "acc-builtin"), ("default", "acc-canonical"),
                ("system", "workshop"), ("user", "personal"),
                ("workspace", "local")} <= layers_seen
        # No cell anywhere holds a scheme — nothing a web terminal can linkify.
        assert not any("://" in cell for row in _rows(table) for cell in row)


@pytest.mark.asyncio
async def test_detail_shows_full_url_and_scrolls(layers):
    app = _Harness(layers["workspace"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#catalogs-table", DataTable)
        table.move_cursor(row=_row_index(table, "system", "workshop"))
        await pilot.pause()
        detail = str(screen.query_one("#catalogs-detail", Static).render())
        assert LONG_URL in detail
        assert "system layer" in detail and "shadowed by the user layer" in detail
        # The signer regex carries '[' — rendered literally, not as markup.
        assert "[a-z0-9-]+" in detail

        # Bundled catalog: the roles list is longer than the pane, and the
        # pane scrolls to it instead of cutting it off.
        table.move_cursor(row=0)
        await pilot.pause()
        scroller = screen.query_one("#catalogs-detail-scroll", VerticalScroll)
        assert scroller.size.height >= 8
        assert scroller.max_scroll_y > 0
        scroller.scroll_end(animate=False)
        await pilot.pause()
        assert scroller.scroll_y == scroller.max_scroll_y


@pytest.mark.asyncio
async def test_read_only_layers_refuse_delete_and_workspace_rows_delete(layers):
    app = _Harness(layers["workspace"])
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#catalogs-table", DataTable)
        status = screen.query_one("#catalogs-status", Static)

        table.move_cursor(row=_row_index(table, "system", "workshop"))
        await pilot.pause()
        screen.action_delete_highlighted()
        await pilot.pause()
        assert "read-only" in str(status.render())
        assert "workshop" in layers["system"].read_text(encoding="utf-8")

        table.move_cursor(row=_row_index(table, "workspace", "local"))
        await pilot.pause()
        screen.action_delete_highlighted()
        await pilot.pause()
        assert "removed local" in str(status.render())
        assert catalog_admin.load(layers["workspace"]) == []
        assert ("workspace", "local") not in {(r[1], r[0]) for r in _rows(table)}


@pytest.mark.asyncio
async def test_n_opens_the_add_form(layers):
    from textual.widgets import Collapsible, Input

    app = _Harness(layers["workspace"])
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#catalogs-form-collapsible", Collapsible).collapsed
        screen.action_focus_new()
        await pilot.pause()
        await pilot.pause()
        assert not screen.query_one("#catalogs-form-collapsible", Collapsible).collapsed
        assert screen.query_one("#form-catalog-id", Input).has_focus
