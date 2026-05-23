"""PR-Z3d — Compliance pane Rule Proposals review surface.

Pilots the Rule Proposals collapsible: it lists pending proposals,
focuses via `p`, and Approve/Reject act on the highlighted proposal
(approve → the signed-bundle overlay).
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import DataTable

from acc.tui.screens.compliance import ComplianceScreen


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_RULE_PROPOSALS_ROOT", str(tmp_path))
    monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "propose")
    # Keep governance/framework loads from touching the real tree.
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(tmp_path / "reg"))
    (tmp_path / "reg").mkdir()
    return tmp_path


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())


def _seed(n: int = 2):
    from acc.rule_proposals import create_proposal
    ids = []
    for i in range(n):
        p = create_proposal(
            source="gap", category="C", rule_text=f"r{i}",
            rationale=f"because {i}",
        )
        ids.append(p.proposal_id)
    return ids


@pytest.mark.asyncio
async def test_proposals_table_lists_pending(store):
    ids = _seed(2)
    app = _Harness()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_proposals()
        await pilot.pause()
        table = screen.query_one("#proposals-table", DataTable)
        assert table.row_count == 2
        keys = {str(getattr(k, "value", k)) for k in table.rows.keys()}
        assert set(ids) == keys


@pytest.mark.asyncio
async def test_focus_proposals_action(store):
    _seed(1)
    app = _Harness()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_proposals()
        await pilot.pause()
        screen.action_focus_proposals()
        await pilot.pause()
        assert app.focused is screen.query_one("#proposals-table", DataTable)


@pytest.mark.asyncio
async def test_approve_highlighted_proposal_writes_overlay(store):
    from acc.rule_proposals import get_proposal, overlay_path
    ids = _seed(2)
    app = _Harness()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_proposals()
        await pilot.pause()
        table = screen.query_one("#proposals-table", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()
        target = screen._selected_proposal_id()
        screen._decide_proposal(approve=True)
        await pilot.pause()
        assert get_proposal(target).status == "APPROVED"
        assert overlay_path().exists()


@pytest.mark.asyncio
async def test_reject_highlighted_proposal(store):
    from acc.rule_proposals import get_proposal
    _seed(1)
    app = _Harness()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_proposals()
        await pilot.pause()
        table = screen.query_one("#proposals-table", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()
        target = screen._selected_proposal_id()
        screen._decide_proposal(approve=False)
        await pilot.pause()
        assert get_proposal(target).status == "REJECTED"


@pytest.mark.asyncio
async def test_decide_without_selection_warns(store):
    app = _Harness()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_proposals()  # empty
        statuses: list[str] = []
        screen._set_proposals_status = lambda m: statuses.append(m)  # type: ignore
        screen._decide_proposal(approve=True)
        await pilot.pause()
        assert any("highlight" in s.lower() for s in statuses)
