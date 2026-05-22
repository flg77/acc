"""Pilot tests for :class:`acc.tui.widgets.workspace_select_modal.WorkspaceSelectModal`.

The modal is the operator-facing half of the D-007 trusted-workspace
feature (PR-U2b): it browses the ``/workspace`` mount, lets the
operator highlight or create a project directory, and on Confirm marks
it trusted (writes the ``.acc-workspace-trust`` sentinel) then dismisses
with the chosen absolute :class:`Path`.

These tests mount the modal in a tiny Textual harness rooted at a
``tmp_path`` so no real ``/workspace`` mount is needed.  They assert:

* Confirm on a highlighted dir trusts + returns it.
* Confirm with a new-folder name creates it under the highlight,
  trusts it, and returns the nested path.
* A folder name containing a path separator / ``..`` is rejected
  (traversal guard) and the modal stays open.
* Cancel returns ``None``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Input

from acc.tui.widgets.workspace_select_modal import WorkspaceSelectModal
from acc.workspace import is_trusted


class _ModalHarness(App):
    """Pushes a WorkspaceSelectModal and stashes its dismiss result."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self.result: Path | None = "UNSET"  # type: ignore[assignment]

    def on_mount(self) -> None:
        def _store(value):
            self.result = value
        self.push_screen(WorkspaceSelectModal(self._root), _store)


@pytest.mark.asyncio
async def test_confirm_highlighted_dir_trusts_and_returns(tmp_path):
    app = _ModalHarness(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, WorkspaceSelectModal)
        # Default highlight is the root itself.
        modal.action_confirm()
        await pilot.pause()

    assert app.result == tmp_path
    assert is_trusted(tmp_path)


@pytest.mark.asyncio
async def test_confirm_creates_new_folder_and_trusts_it(tmp_path):
    app = _ModalHarness(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.query_one("#ws-newname", Input).value = "myproject"
        modal.action_confirm()
        await pilot.pause()

    expected = tmp_path / "myproject"
    assert app.result == expected
    assert expected.is_dir()
    assert is_trusted(expected)


@pytest.mark.asyncio
async def test_new_folder_name_with_separator_rejected(tmp_path):
    app = _ModalHarness(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.query_one("#ws-newname", Input).value = "../escape"
        modal.action_confirm()
        await pilot.pause()
        # Still open — the traversal name was rejected, no dismiss.
        assert app.result == "UNSET"
        assert isinstance(app.screen, WorkspaceSelectModal)

    # And nothing was created/trusted outside the root.
    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.asyncio
async def test_cancel_returns_none(tmp_path):
    app = _ModalHarness(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.action_cancel()
        await pilot.pause()

    assert app.result is None
