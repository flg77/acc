"""PR-Z2c — Compliance pane frameworks section: list, import, gap scan.

Pilots the Frameworks collapsible: it lists built-in + imported
catalogs, "+ Add" imports a custom catalog, and "Run gap scan" runs the
deterministic analysis, writes the audit doc, updates the coverage
cell, and opens the markdown report in the read-only viewer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Input

from acc.tui.screens.compliance import ComplianceScreen
from acc.tui.widgets.policy_viewer_modal import PolicyViewerModal

_BSI = """\
framework_id: bsi_c5
name: "BSI C5"
version: "2020"
source: "BSI C5:2020"
controls:
  - control_id: OPS-01
    title: "Capacity planning"
    description: "Plan capacity for membrane signals and collective drift."
    category: OPS
"""


@pytest.fixture
def reg_root(tmp_path, monkeypatch):
    """Synthetic regulatory_layer (with built-in frameworks) + writable
    import/report stores, all redirected to tmp_path."""
    (tmp_path / "category_a").mkdir()
    (tmp_path / "category_a" / "c.rego").write_text(
        "# Version: 0.6.0\n# A-001: Reject foreign collective signals.\n",
        encoding="utf-8",
    )
    fw_builtin = tmp_path / "frameworks"
    fw_builtin.mkdir()
    (fw_builtin / "soc2.yaml").write_text(
        "framework_id: soc2\nname: SOC2\ncontrols:\n"
        "  - control_id: CC6.1\n    title: Logical access\n"
        "    description: protect information assets\n    category: SECURITY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(tmp_path))
    monkeypatch.setenv("ACC_FRAMEWORKS_IMPORT_ROOT", str(tmp_path / "imported"))
    monkeypatch.setenv("ACC_COMPLIANCE_REPORTS_ROOT", str(tmp_path / "reports"))
    return tmp_path


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())


@pytest.mark.asyncio
async def test_frameworks_table_lists_builtin(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 55)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#fw-table", DataTable)
        keys = [str(getattr(k, "value", k)) for k in table.rows.keys()]
        assert "soc2" in keys


@pytest.mark.asyncio
async def test_import_framework_adds_row(reg_root, tmp_path):
    src = tmp_path / "bsi.yaml"
    src.write_text(_BSI, encoding="utf-8")
    app = _Harness()
    async with app.run_test(size=(160, 55)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#fw-add-input", Input).value = str(src)
        screen._import_framework()
        await pilot.pause()
        table = screen.query_one("#fw-table", DataTable)
        keys = [str(getattr(k, "value", k)) for k in table.rows.keys()]
        assert "bsi_c5" in keys
        assert (reg_root / "imported" / "bsi_c5.yaml").is_file()


@pytest.mark.asyncio
async def test_import_bad_path_sets_error(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 55)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_fw_status = lambda m: statuses.append(m)  # type: ignore
        screen.query_one("#fw-add-input", Input).value = "/nonexistent/x.yaml"
        screen._import_framework()
        await pilot.pause()
        assert any("failed" in s.lower() for s in statuses)


@pytest.mark.asyncio
async def test_run_gap_scan_writes_report_and_opens_viewer(reg_root):
    app = _Harness()
    async with app.run_test(size=(160, 55)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#fw-table", DataTable)
        table.move_cursor(row=0)  # soc2
        await pilot.pause()
        screen._run_gap_scan()
        await pilot.pause()
        # A report doc was written...
        reports = list((reg_root / "reports").glob("gap-soc2-*.md"))
        assert reports, "no gap report written"
        # ...coverage cell cached...
        assert "soc2" in screen._coverage_by_fw
        # ...and the markdown opened in the read-only viewer.
        assert isinstance(app.screen, PolicyViewerModal)


@pytest.mark.asyncio
async def test_run_gap_scan_without_selection_warns(reg_root, monkeypatch):
    # Empty frameworks: point built-in + imported at empty dirs.
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(reg_root / "empty"))
    (reg_root / "empty").mkdir()
    app = _Harness()
    async with app.run_test(size=(160, 55)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_fw_status = lambda m: statuses.append(m)  # type: ignore
        screen._run_gap_scan()
        await pilot.pause()
        assert any("highlight" in s.lower() for s in statuses)
