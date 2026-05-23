"""Pilot tests for :class:`acc.tui.widgets.workspace_select_modal.WorkspaceSelectModal`.

PR-X recreate-on-select behaviour: the modal browses the read-only host
mount (``/host-home`` in prod; an ``ACC_WORKSPACE_BROWSE_ROOT`` override
here) and, on Confirm, writes an *apply request* naming the chosen HOST
path (``ACC_WORKSPACE_BASE`` + the relative pick).  It does NOT mkdir or
write the trust sentinel — those happen host-side in the apply-watcher.
Dismisses with the host path ``str`` (or ``None`` on cancel).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Input

from acc.tui.widgets.workspace_select_modal import WorkspaceSelectModal
from acc.workspace_apply import read_apply_request


class _ModalHarness(App):
    """Pushes a WorkspaceSelectModal and stashes its dismiss result."""

    def __init__(self, browse: Path, base: str) -> None:
        super().__init__()
        self._browse = browse
        self._base = base
        self.result = "UNSET"  # type: ignore[assignment]

    def on_mount(self) -> None:
        def _store(value):
            self.result = value
        self.push_screen(
            WorkspaceSelectModal(browse=self._browse, base=self._base), _store,
        )


@pytest.fixture
def apply_dir(tmp_path, monkeypatch):
    d = tmp_path / "apply"
    d.mkdir()
    monkeypatch.setenv("ACC_APPLY_DIR", str(d))
    return d


@pytest.mark.asyncio
async def test_confirm_root_writes_request_with_base(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "/home/flg")
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, WorkspaceSelectModal)
        # Default highlight is the browse root → host path == base.
        modal.action_confirm()
        await pilot.pause()

    assert app.result == "/home/flg"
    req = read_apply_request(apply_dir)
    assert req is not None and req["host_path"] == "/home/flg"


@pytest.mark.asyncio
async def test_confirm_new_folder_appends_to_host_path(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "/home/flg")
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.query_one("#ws-newname", Input).value = "projecta"
        modal.action_confirm()
        await pilot.pause()

    assert app.result == "/home/flg/projecta"
    assert read_apply_request(apply_dir)["host_path"] == "/home/flg/projecta"


@pytest.mark.asyncio
async def test_confirm_highlighted_subdir(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    (browse / "existing").mkdir(parents=True)
    app = _ModalHarness(browse, "/home/flg")
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        # Simulate the tree highlighting an existing subdir.
        modal._selected = browse / "existing"
        modal.action_confirm()
        await pilot.pause()

    assert app.result == "/home/flg/existing"


@pytest.mark.asyncio
async def test_new_folder_name_with_separator_rejected(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "/home/flg")
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.query_one("#ws-newname", Input).value = "../escape"
        modal.action_confirm()
        await pilot.pause()
        # Still open, no dismiss, no request written.
        assert app.result == "UNSET"
        assert isinstance(app.screen, WorkspaceSelectModal)

    assert read_apply_request(apply_dir) is None


@pytest.mark.asyncio
async def test_missing_base_blocks_confirm(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "")  # base not configured
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.action_confirm()
        await pilot.pause()
        assert app.result == "UNSET"

    assert read_apply_request(apply_dir) is None


@pytest.mark.asyncio
async def test_cancel_returns_none(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "/home/flg")
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.action_cancel()
        await pilot.pause()

    assert app.result is None
    assert read_apply_request(apply_dir) is None
