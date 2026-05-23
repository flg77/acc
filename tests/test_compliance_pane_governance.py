"""PR-Z1b — Compliance pane governance layers + policy viewer.

The pane now shows WHAT governance is loaded: three collapsible
Cat-A/B/C sections, each a rule_id|summary table populated from
``acc.governance_inventory``; selecting a row opens the source policy
file in a read-only viewer.  These pilot tests pin those contracts
against a synthetic ``regulatory_layer`` root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Collapsible, DataTable

from acc.tui.screens.compliance import ComplianceScreen
from acc.tui.widgets.policy_viewer_modal import PolicyViewerModal


@pytest.fixture
def reg_root(tmp_path, monkeypatch):
    """A synthetic regulatory_layer with one rule per category."""
    (tmp_path / "category_a").mkdir()
    (tmp_path / "category_b").mkdir()
    (tmp_path / "category_c").mkdir()
    (tmp_path / "category_a" / "constitutional.rego").write_text(
        "# Version: 0.6.0\n# A-001: Reject foreign signals.\nallow if { true }\n",
        encoding="utf-8",
    )
    (tmp_path / "category_b" / "conditional.rego").write_text(
        "# Version: 0.3.0\n# B-001: Sync only when healthy.\nallow if { true }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(tmp_path))
    return tmp_path


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())


@pytest.mark.asyncio
async def test_three_collapsibles_present(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        for cid in ("gov-cat-a", "gov-cat-b", "gov-cat-c"):
            screen.query_one(f"#{cid}", Collapsible)
        # Cat-A starts expanded, B/C collapsed.
        assert screen.query_one("#gov-cat-a", Collapsible).collapsed is False
        assert screen.query_one("#gov-cat-b", Collapsible).collapsed is True


@pytest.mark.asyncio
async def test_governance_tables_populated_from_inventory(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        tbl_a = screen.query_one("#gov-table-a", DataTable)
        assert tbl_a.row_count == 1
        # Rule id keyed + cached for the viewer lookup.
        assert "A-001" in screen._gov_rules_by_key
        rule = screen._gov_rules_by_key["A-001"]
        assert rule.source_path.endswith("constitutional.rego")


@pytest.mark.asyncio
async def test_collapsible_title_shows_version_and_count(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        title = screen.query_one("#gov-cat-a", Collapsible).title
        assert "v0.6.0" in title
        assert "1 rules" in title
        assert "🔒" in title  # Cat-A immutable lock marker


@pytest.mark.asyncio
async def test_empty_category_titled_none(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Cat-C has no rego in the fixture.
        assert "(none loaded)" in screen.query_one("#gov-cat-c", Collapsible).title


@pytest.mark.asyncio
async def test_focus_governance_action(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.action_focus_governance()
        await pilot.pause()
        assert app.focused is screen.query_one("#gov-table-a", DataTable)


@pytest.mark.asyncio
async def test_selecting_rule_opens_policy_viewer(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        tbl_a = screen.query_one("#gov-table-a", DataTable)
        first_key = list(tbl_a.rows.keys())[0]
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=tbl_a, cursor_row=0, row_key=first_key,
            )
        )
        await pilot.pause()
        assert isinstance(app.screen, PolicyViewerModal)


@pytest.mark.asyncio
async def test_policy_viewer_renders_file_with_line_numbers(reg_root):
    """The viewer shows the file body (markup-safe) with line numbers."""
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        path = reg_root / "category_a" / "constitutional.rego"
        modal = PolicyViewerModal(path, highlight_line=2)
        body = modal._render_body()
        assert "A-001" in body
        assert "▶   2 │" in body  # highlight marker on line 2
