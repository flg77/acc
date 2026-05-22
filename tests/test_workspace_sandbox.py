"""PR-U1 (D-007) — trusted-workspace path sandbox.

The security boundary for agent filesystem access.  These tests are
the ones that matter most: they prove an LLM-controlled path cannot
escape the workspace root.  A regression here is a sandbox escape.

Covers acc.workspace.safe_resolve / require_writable_workspace /
trust flag, plus the fs_read / fs_write skill adapters end-to-end
against a tmp workspace.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from acc.workspace import (
    WorkspaceError,
    is_trusted,
    mark_trusted,
    require_writable_workspace,
    safe_resolve,
    workspace_root,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A throwaway workspace root wired via ACC_WORKSPACE_DIR."""
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("ACC_WORKSPACE_DIR", str(root))
    return root


# ---------------------------------------------------------------------------
# workspace_root resolution
# ---------------------------------------------------------------------------


def test_workspace_root_env_override(ws):
    assert workspace_root() == ws


def test_workspace_root_defaults_to_mount(monkeypatch):
    monkeypatch.delenv("ACC_WORKSPACE_DIR", raising=False)
    # Compare via Path so the test passes on Windows (where the dev
    # workstation runs) — the container is Linux where this is
    # literally "/workspace".
    assert workspace_root() == Path("/workspace")


# ---------------------------------------------------------------------------
# safe_resolve — the escape-prevention core
# ---------------------------------------------------------------------------


class TestSafeResolve:
    def test_simple_relative_path_ok(self, ws):
        resolved = safe_resolve("scraper.py")
        assert resolved == (ws.resolve() / "scraper.py")

    def test_nested_relative_path_ok(self, ws):
        resolved = safe_resolve("src/app/main.py")
        assert str(resolved).startswith(str(ws.resolve()))

    def test_absolute_path_rejected(self, ws):
        with pytest.raises(WorkspaceError) as exc:
            safe_resolve("/etc/passwd")
        assert "absolute" in str(exc.value).lower()

    def test_parent_traversal_rejected(self, ws):
        with pytest.raises(WorkspaceError) as exc:
            safe_resolve("../../etc/passwd")
        assert "escape" in str(exc.value).lower()

    def test_sneaky_midpath_traversal_rejected(self, ws):
        # Resolves to ws/../secret → escapes.
        with pytest.raises(WorkspaceError):
            safe_resolve("a/b/../../../secret")

    def test_empty_path_rejected(self, ws):
        with pytest.raises(WorkspaceError):
            safe_resolve("")
        with pytest.raises(WorkspaceError):
            safe_resolve("   ")

    def test_symlink_escape_rejected(self, ws, tmp_path):
        """A symlink INSIDE the workspace pointing OUTSIDE must not
        grant access to the target."""
        outside = tmp_path / "outside_secret"
        outside.mkdir()
        (outside / "loot.txt").write_text("secret", encoding="utf-8")
        link = ws / "escape_link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform/run")
        with pytest.raises(WorkspaceError) as exc:
            safe_resolve("escape_link/loot.txt")
        assert "escape" in str(exc.value).lower()

    def test_dot_path_resolves_to_root(self, ws):
        # "." resolves to the root itself — allowed (root is contained).
        resolved = safe_resolve(".")
        assert resolved == ws.resolve()


# ---------------------------------------------------------------------------
# Trust flag + require_writable_workspace
# ---------------------------------------------------------------------------


class TestTrust:
    def test_untrusted_by_default(self, ws):
        assert is_trusted() is False

    def test_mark_trusted_then_trusted(self, ws):
        mark_trusted(note="operator test")
        assert is_trusted() is True
        # sentinel records provenance.
        sentinel = (ws / ".acc-workspace-trust").read_text(encoding="utf-8")
        assert "trusted_at=" in sentinel
        assert "operator test" in sentinel

    def test_write_blocked_when_untrusted(self, ws):
        with pytest.raises(WorkspaceError) as exc:
            require_writable_workspace("out.txt")
        assert "not trusted" in str(exc.value).lower()

    def test_write_allowed_when_trusted_and_in_bounds(self, ws):
        mark_trusted()
        resolved = require_writable_workspace("out.txt")
        assert resolved == (ws.resolve() / "out.txt")

    def test_write_still_sandboxed_when_trusted(self, ws):
        """Trust does NOT relax the path sandbox — escape still
        rejected even on a trusted workspace."""
        mark_trusted()
        with pytest.raises(WorkspaceError) as exc:
            require_writable_workspace("../../etc/passwd")
        assert "escape" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Skill adapters end-to-end
# ---------------------------------------------------------------------------


class TestFsSkills:
    @pytest.mark.asyncio
    async def test_fs_write_then_fs_read_roundtrip(self, ws):
        from skills.fs_write.adapter import FsWriteSkill
        from skills.fs_read.adapter import FsReadSkill

        mark_trusted()
        w = FsWriteSkill()
        out = await w.invoke({"path": "hello.py", "content": "print('hi')\n"})
        assert out["bytes_written"] == len("print('hi')\n")
        assert (ws / "hello.py").read_text(encoding="utf-8") == "print('hi')\n"

        r = FsReadSkill()
        got = await r.invoke({"path": "hello.py"})
        assert got["content"] == "print('hi')\n"
        assert got["truncated"] is False

    @pytest.mark.asyncio
    async def test_fs_write_creates_parent_dirs(self, ws):
        from skills.fs_write.adapter import FsWriteSkill
        mark_trusted()
        w = FsWriteSkill()
        await w.invoke({"path": "src/pkg/mod.py", "content": "x = 1\n"})
        assert (ws / "src" / "pkg" / "mod.py").is_file()

    @pytest.mark.asyncio
    async def test_fs_write_denied_when_untrusted(self, ws):
        from skills.fs_write.adapter import FsWriteSkill
        w = FsWriteSkill()
        with pytest.raises(ValueError) as exc:
            await w.invoke({"path": "x.py", "content": "x"})
        assert "denied" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_fs_write_denied_on_escape(self, ws):
        from skills.fs_write.adapter import FsWriteSkill
        mark_trusted()
        w = FsWriteSkill()
        with pytest.raises(ValueError) as exc:
            await w.invoke({"path": "../../evil.py", "content": "x"})
        assert "denied" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_fs_read_truncates_at_max_bytes(self, ws):
        from skills.fs_read.adapter import FsReadSkill
        (ws / "big.txt").write_text("A" * 1000, encoding="utf-8")
        r = FsReadSkill()
        got = await r.invoke({"path": "big.txt", "max_bytes": 100})
        assert got["bytes"] == 1000
        assert len(got["content"]) == 100
        assert got["truncated"] is True

    @pytest.mark.asyncio
    async def test_fs_read_denied_on_escape(self, ws):
        from skills.fs_read.adapter import FsReadSkill
        r = FsReadSkill()
        with pytest.raises(ValueError):
            await r.invoke({"path": "/etc/passwd"})


# ---------------------------------------------------------------------------
# D-003 integration — fs_write classifies as a write action
# ---------------------------------------------------------------------------


def test_fs_write_is_write_action_for_operating_modes():
    """The fs_write skill id must trip the ACCEPT_EDITS write gate so
    file writes get human review under that mode."""
    from acc.operating_modes import is_write_action, should_gate_invocation
    assert is_write_action("skill", "fs_write") is True
    assert should_gate_invocation(
        "ACCEPT_EDITS", kind="skill", target="fs_write", risk_level="HIGH",
    ) is True
    # fs_read is not a write action.
    assert is_write_action("skill", "fs_read") is False
