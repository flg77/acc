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


# ---------------------------------------------------------------------------
# Ecosystem upload-flow integration tests
# ---------------------------------------------------------------------------


def _write_skill_dir(parent: Path, skill_id: str = "uploaded_skill") -> Path:
    """Create a skill source directory the operator could pick from."""
    skill_dir = parent / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        "purpose: 'uploaded fixture'\n"
        "version: '0.1.0'\n"
        "adapter_module: 'adapter'\n"
        f"adapter_class: '{skill_id.title().replace('_','')}Skill'\n"
        "input_schema: {}\n"
        "output_schema: {}\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )
    (skill_dir / "adapter.py").write_text(
        "from acc.skills import Skill\n"
        f"class {skill_id.title().replace('_','')}Skill(Skill):\n"
        "    async def invoke(self, args):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_mcp_dir(parent: Path, server_id: str = "uploaded_mcp") -> Path:
    """Create an MCP source directory the operator could pick from."""
    mcp_dir = parent / server_id
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "mcp.yaml").write_text(
        "purpose: 'uploaded fixture'\n"
        "version: '0.1.0'\n"
        "transport: 'http'\n"
        "url: 'http://localhost:9090/rpc'\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )
    return mcp_dir


@pytest.fixture
def isolated_manifest_roots(tmp_path, monkeypatch):
    """Empty skills/, mcps/, roles/ trees + env vars pointing at them."""
    skills_root = tmp_path / "skills"
    mcps_root = tmp_path / "mcps"
    roles_root = tmp_path / "roles"
    skills_root.mkdir()
    mcps_root.mkdir()
    roles_root.mkdir()
    # One placeholder role so EcosystemScreen's role table mounts.
    role = roles_root / "stub"
    role.mkdir()
    (role / "role.yaml").write_text(
        "role_definition:\n  purpose: 'stub'\n  persona: 'concise'\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("ACC_SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(mcps_root))
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))

    return {
        "skills_root": skills_root,
        "mcps_root": mcps_root,
        "tmp": tmp_path,
    }


class _EcoHarness(App):
    """Mounts EcosystemScreen and captures any push_screen calls."""

    def on_mount(self) -> None:
        self.push_screen(EcosystemScreen())


@pytest.mark.asyncio
async def test_upload_skill_copies_directory_and_refreshes_table(
    isolated_manifest_roots,
):
    """End-to-end: source dir → FileSelected → copytree → table has +1 row."""
    skill_src = _write_skill_dir(
        isolated_manifest_roots["tmp"] / "from_operator",
        "uploaded_skill",
    )

    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EcosystemScreen)

        skills_table = screen.query_one("#skills-table", DataTable)
        # Pre-upload sanity: the only row is the guidance fallback whose
        # key is auto-assigned (NOT "uploaded_skill").
        pre_keys = [
            getattr(k, "value", str(k)) for k in skills_table.rows.keys()
        ]
        assert "uploaded_skill" not in pre_keys, pre_keys

        # Simulate the picker confirming a skill.yaml from the source dir.
        screen._pending_upload_kind = "skill"
        screen.on_file_picker_modal_file_selected(
            FilePickerModal.FileSelected(path=skill_src / "skill.yaml")
        )
        await pilot.pause()

        # Manifest copied — the whole tree, not just the YAML.
        target = isolated_manifest_roots["skills_root"] / "uploaded_skill"
        assert (target / "skill.yaml").exists()
        assert (target / "adapter.py").exists()

        # Table refreshed: now contains a row keyed by the new skill id.
        post_keys = [
            getattr(k, "value", str(k)) for k in skills_table.rows.keys()
        ]
        assert post_keys == ["uploaded_skill"], post_keys


@pytest.mark.asyncio
async def test_upload_mcp_copies_directory_and_refreshes_table(
    isolated_manifest_roots,
):
    """Same shape as the skill test but for the MCP path."""
    mcp_src = _write_mcp_dir(
        isolated_manifest_roots["tmp"] / "from_operator",
        "uploaded_mcp",
    )

    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen._pending_upload_kind = "mcp"
        screen.on_file_picker_modal_file_selected(
            FilePickerModal.FileSelected(path=mcp_src / "mcp.yaml")
        )
        await pilot.pause()

        target = isolated_manifest_roots["mcps_root"] / "uploaded_mcp"
        assert (target / "mcp.yaml").exists()

        mcps_table = screen.query_one("#mcps-table", DataTable)
        assert mcps_table.row_count == 1
        first_key = list(mcps_table.rows.keys())[0]
        assert getattr(first_key, "value", str(first_key)) == "uploaded_mcp"


@pytest.mark.asyncio
async def test_upload_refuses_to_clobber_existing_directory(
    isolated_manifest_roots,
):
    """Pre-existing target dir → notify warning, no copy, no refresh."""
    # Pre-populate the destination.
    pre_existing = (
        isolated_manifest_roots["skills_root"] / "duplicate_skill"
    )
    pre_existing.mkdir()
    (pre_existing / "skill.yaml").write_text("untouched\n")

    # Source dir with the same name.
    skill_src = _write_skill_dir(
        isolated_manifest_roots["tmp"] / "from_operator",
        "duplicate_skill",
    )

    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        notifications: list[tuple[str, str]] = []
        # Capture screen.notify so we can assert the warning fired.
        orig_notify = screen.notify

        def capture(message, *, severity="information", timeout=4.0, **kw):
            notifications.append((message, severity))
            return orig_notify(message, severity=severity, timeout=timeout, **kw)

        screen.notify = capture  # type: ignore[assignment]

        screen._pending_upload_kind = "skill"
        screen.on_file_picker_modal_file_selected(
            FilePickerModal.FileSelected(path=skill_src / "skill.yaml")
        )
        await pilot.pause()

        # Destination unchanged.
        assert (pre_existing / "skill.yaml").read_text() == "untouched\n"
        # No adapter.py (which the source carries) in the target.
        assert not (pre_existing / "adapter.py").exists()
        # Warning notification surfaced.
        assert any("already exists" in m for m, _ in notifications), notifications
        assert any(sev == "warning" for _, sev in notifications), notifications
