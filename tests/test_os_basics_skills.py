"""Tests for the 12 OS-basics skill adapters + shell_exec.

OpenSpec `20260603-capability-pool` Phase 1.1 / 1.2.  Each adapter is
imported via the skill loader (the same path the runtime uses) so we
exercise registration end-to-end.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from acc.skills.registry import SkillRegistry


@pytest.fixture(scope="module")
def reg() -> SkillRegistry:
    r = SkillRegistry()
    r.load_from("skills")
    return r


def _invoke(reg: SkillRegistry, sid: str, args: dict) -> dict:
    """Call the adapter directly so we don't exercise the A-017 gate
    here (the gate has its own test file)."""
    adapter = reg.get(sid)
    assert adapter is not None, f"missing skill: {sid}"
    return asyncio.run(adapter.invoke(args))


class TestLsDir:
    def test_lists_directory(self, reg: SkillRegistry, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        out = _invoke(reg, "ls_dir", {"path": str(tmp_path)})
        names = {e["name"] for e in out["entries"]}
        assert names == {"a.txt", "sub"}

    def test_rejects_non_directory(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            _invoke(reg, "ls_dir", {"path": str(f)})


class TestStatPath:
    def test_existing_file(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("hello")
        out = _invoke(reg, "stat_path", {"path": str(f)})
        assert out["exists"] and out["is_file"] and out["size"] == 5

    def test_missing(self, reg: SkillRegistry, tmp_path: Path) -> None:
        out = _invoke(reg, "stat_path", {"path": str(tmp_path / "nope")})
        assert not out["exists"]


class TestReadText:
    def test_head(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("0123456789ABCDEF")
        out = _invoke(reg, "read_text_head", {"path": str(f), "max_bytes": 5})
        assert out["content"] == "01234"
        assert out["truncated"]

    def test_tail(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("0123456789ABCDEF")
        out = _invoke(reg, "read_text_tail", {"path": str(f), "max_bytes": 5})
        assert out["content"] == "BCDEF"


class TestGrep:
    def test_finds_matches(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("alpha\nbeta\nalphabet\n")
        out = _invoke(reg, "grep_text",
                      {"path": str(f), "pattern": "alpha"})
        lines = [m["line"] for m in out["matches"]]
        assert lines == [1, 3]


class TestFindFiles:
    def test_glob(self, reg: SkillRegistry, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.txt").write_text("")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.py").write_text("")
        out = _invoke(reg, "find_files",
                      {"root": str(tmp_path), "pattern": "*.py"})
        names = sorted(Path(p).name for p in out["files"])
        assert names == ["a.py", "c.py"]


class TestWhichCmd:
    def test_finds_python(self, reg: SkillRegistry) -> None:
        out = _invoke(reg, "which_cmd", {"name": "python"})
        # Skip if python not on PATH (test env oddity).
        if not out["found"]:
            pytest.skip("python not on PATH")
        assert out["path"]

    def test_rejects_unsafe_name(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError, match="alnum"):
            _invoke(reg, "which_cmd", {"name": "rm -rf /"})


class TestEnvGet:
    def test_allowlisted(self, reg: SkillRegistry) -> None:
        os.environ["PATH"] = os.environ.get("PATH", "")
        out = _invoke(reg, "env_get", {"name": "PATH"})
        assert out["present"]

    def test_rejects_secret_var(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError, match="allowlist"):
            _invoke(reg, "env_get", {"name": "AWS_SECRET_ACCESS_KEY"})


class TestPwd:
    def test_returns_cwd(self, reg: SkillRegistry) -> None:
        out = _invoke(reg, "pwd", {})
        assert out["cwd"] == os.getcwd()


class TestDiskFree:
    def test_root(self, reg: SkillRegistry) -> None:
        out = _invoke(reg, "disk_free", {"path": str(Path.cwd().anchor or "/")})
        assert out["total"] > 0
        assert out["free"] >= 0


class TestGitSkills:
    def _init_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@e"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "x.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        return repo

    def test_git_status(self, reg: SkillRegistry, tmp_path: Path) -> None:
        if not shutil.which("git"):
            pytest.skip("git not available")
        repo = self._init_repo(tmp_path)
        (repo / "y.txt").write_text("new")
        out = _invoke(reg, "git_status", {"cwd": str(repo)})
        assert out["returncode"] == 0
        assert "y.txt" in out["stdout"]

    def test_git_log_recent(self, reg: SkillRegistry, tmp_path: Path) -> None:
        if not shutil.which("git"):
            pytest.skip("git not available")
        repo = self._init_repo(tmp_path)
        out = _invoke(reg, "git_log_recent", {"cwd": str(repo), "limit": 5})
        assert out["returncode"] == 0
        assert "init" in out["stdout"]


class TestShellExec:
    def test_argv_only(self, reg: SkillRegistry) -> None:
        out = _invoke(
            reg, "shell_exec",
            {"argv": [sys.executable, "-c", "print('hi')"]},
        )
        assert out["returncode"] == 0
        assert "hi" in out["stdout"]

    def test_rejects_non_string_argv(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError):
            _invoke(reg, "shell_exec", {"argv": [1, 2]})

    def test_command_not_found(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError, match="not found"):
            _invoke(reg, "shell_exec",
                    {"argv": ["definitely_not_a_real_command_xyz"]})

    def test_timeout(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError, match="timeout"):
            _invoke(
                reg, "shell_exec",
                {"argv": [sys.executable, "-c", "import time; time.sleep(5)"],
                 "timeout_s": 1},
            )
