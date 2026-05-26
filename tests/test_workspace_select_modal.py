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
async def test_local_mode_returns_selected_path_directly(tmp_path, apply_dir):
    # base unset ⇒ local mode: confirm returns the local path verbatim
    # and writes no apply request (no host/container split to bridge).
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "")  # base not configured → local mode
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.action_confirm()
        await pilot.pause()

    assert app.result == str(browse.resolve())
    assert read_apply_request(apply_dir) is None


@pytest.mark.asyncio
async def test_local_mode_creates_new_folder(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "")  # local mode
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.query_one("#ws-newname", Input).value = "fresh"
        modal.action_confirm()
        await pilot.pause()

    created = browse / "fresh"
    assert created.is_dir()
    assert app.result == str(created.resolve())
    assert read_apply_request(apply_dir) is None


@pytest.mark.asyncio
async def test_navigate_reroots_to_typed_path(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    (browse / "deep" / "nested").mkdir(parents=True)
    app = _ModalHarness(browse, "")  # local mode
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        target = browse / "deep" / "nested"
        modal._navigate(target)
        await pilot.pause()
        assert modal._root == target.resolve()
        assert modal._selected == target.resolve()
        # The location bar reflects where we are.
        assert modal.query_one("#ws-path", Input).value == str(target.resolve())


@pytest.mark.asyncio
async def test_go_up_climbs_to_parent(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    child = browse / "child"
    child.mkdir(parents=True)
    app = _ModalHarness(child, "")  # start in the child, local mode
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal.action_go_up()
        await pilot.pause()
        assert modal._root == browse.resolve()


@pytest.mark.asyncio
async def test_navigate_rejects_nonexistent_path(tmp_path, apply_dir):
    browse = tmp_path / "host-home"
    browse.mkdir()
    app = _ModalHarness(browse, "")  # local mode
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal._navigate(tmp_path / "does-not-exist")
        await pilot.pause()
        # Root unchanged; the bad path did not take.
        assert modal._root == browse.resolve()


@pytest.mark.asyncio
async def test_host_mode_selection_outside_browse_root_blocked(tmp_path, apply_dir):
    # In host-mapped mode a selection outside the mounted browse root
    # cannot be mapped to a host path → confirm is refused.
    browse = tmp_path / "host-home"
    browse.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    app = _ModalHarness(browse, "/home/flg")
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        modal._selected = elsewhere
        modal.action_confirm()
        await pilot.pause()
        assert app.result == "UNSET"

    assert read_apply_request(apply_dir) is None


def test_host_container_translation_root_base(tmp_path):
    """PR-V4 — with the whole host fs mounted (base=/), the picker shows
    host-true paths and resolves typed host paths into the mount."""
    browse = tmp_path / "hostfs"
    (browse / "home" / "flg").mkdir(parents=True)
    m = WorkspaceSelectModal(browse=browse, base="/")
    # container → host (what the location bar shows)
    assert m._to_host(browse / "home" / "flg") == "/home/flg"
    assert m._to_host(browse) == "/"
    # host (as typed) → container (what the tree navigates)
    assert m._to_container("/home/flg") == browse / "home" / "flg"
    assert m._to_container("/") == browse


def test_host_container_translation_subtree_base(tmp_path):
    browse = tmp_path / "host-home"
    (browse / "proj").mkdir(parents=True)
    m = WorkspaceSelectModal(browse=browse, base="/home/flg")
    assert m._to_host(browse / "proj") == "/home/flg/proj"
    assert m._to_container("/home/flg/proj") == browse / "proj"


def test_translation_identity_in_local_mode(tmp_path):
    """No base ⇒ local mode ⇒ container path == host path (no translation)."""
    m = WorkspaceSelectModal(browse=tmp_path, base="")
    assert m._to_host(tmp_path / "x") == str(tmp_path / "x")
    assert m._to_container("/abs/path") == Path("/abs/path")


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
