"""PR-A2 tests — FilePickerModal + Ecosystem upload flow.

The FilePickerModal is a small Textual ModalScreen wrapping
:class:`textual.widgets.DirectoryTree`.  These tests exercise:

1. The modal posts ``FileSelected`` only when the operator has chosen
   a file whose name matches ``target_filename`` (case-sensitive).
2. Cancelling (button or Esc) dismisses without posting anything.
3. The Ecosystem screen's upload flow copies the source directory
   into the resolved manifest root and refreshes the affected table.

We exercise the modal through its public message + action surface
rather than through DirectoryTree mouse / key dispatch — the same
"synthetic event + direct action call" pattern PR-A used for the
RowSelected / RowHighlighted handlers (Pilot's harness can't reliably
drive nested-widget mouse + keyboard events in test mode).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, DirectoryTree

from acc.tui.screens.ecosystem import EcosystemScreen
from acc.tui.widgets.file_picker import FilePickerModal


# ---------------------------------------------------------------------------
# FilePickerModal isolated tests
# ---------------------------------------------------------------------------


class _PickerHarness(App):
    """Mounts a FilePickerModal and captures its FileSelected message."""

    def __init__(self, modal: FilePickerModal) -> None:
        super().__init__()
        self._modal = modal
        self.captured: list[Path] = []

    def on_mount(self) -> None:
        self.push_screen(self._modal)

    def on_file_picker_modal_file_selected(
        self, message: FilePickerModal.FileSelected
    ) -> None:
        self.captured.append(message.path)


@pytest.mark.asyncio
async def test_modal_confirm_with_matching_filename_posts_message(tmp_path):
    """Selecting a file whose name matches target_filename → FileSelected."""
    skill_yaml = tmp_path / "fixture_skill" / "skill.yaml"
    skill_yaml.parent.mkdir()
    skill_yaml.write_text("dummy: 1\n")

    modal = FilePickerModal(
        target_filename="skill.yaml",
        title="test pick",
        start_path=tmp_path,
    )
    app = _PickerHarness(modal)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Synthetic DirectoryTree.FileSelected — same shape Textual emits.
        tree = modal.query_one("#fp-tree", DirectoryTree)
        modal.on_directory_tree_file_selected(
            DirectoryTree.FileSelected(node=tree.root, path=skill_yaml)
        )
        await pilot.pause()

        modal.action_confirm()
        await pilot.pause()

        assert len(app.captured) == 1
        # Path is resolved on egress.
        assert app.captured[0] == skill_yaml.resolve()


@pytest.mark.asyncio
async def test_modal_confirm_with_wrong_filename_is_noop(tmp_path):
    """Selecting a non-matching filename keeps Confirm disabled.

    The on_directory_tree_file_selected handler updates Confirm's
    state based on filename match.  Pressing Confirm anyway must NOT
    dispatch FileSelected — the modal stays open.
    """
    wrong_yaml = tmp_path / "other" / "settings.yaml"
    wrong_yaml.parent.mkdir()
    wrong_yaml.write_text("k: v\n")

    modal = FilePickerModal(
        target_filename="skill.yaml",
        title="test",
        start_path=tmp_path,
    )
    app = _PickerHarness(modal)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = modal.query_one("#fp-tree", DirectoryTree)
        modal.on_directory_tree_file_selected(
            DirectoryTree.FileSelected(node=tree.root, path=wrong_yaml)
        )
        await pilot.pause()

        # action_confirm short-circuits on mismatched filename.
        modal.action_confirm()
        await pilot.pause()

        assert app.captured == []


@pytest.mark.asyncio
async def test_modal_cancel_dismisses_without_message(tmp_path):
    """Esc / Cancel button dismisses with no FileSelected emission."""
    modal = FilePickerModal(
        target_filename="skill.yaml",
        title="test",
        start_path=tmp_path,
    )
    app = _PickerHarness(modal)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal.action_cancel()
        await pilot.pause()

        assert app.captured == []



# Proposal 009 — Ecosystem-screen upload-flow integration
# tests removed.  Upload buttons moved to the Configuration
# pane in proposal 003 PR-4; coverage now lives in
# tests/test_configuration_screen_pilot.py.  Modal-only tests
# above remain valid (they exercise FilePickerModal in
# isolation, no Ecosystem dependency).
