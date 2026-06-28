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

from acc.skills import SkillSchemaError, resolve_argv
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


class TestResolveArgv:
    """Unit tests for the shared argv/cmd resolver (``acc.skills.resolve_argv``).

    The ``{"cmd": "<string>"}`` alias lets LLM callers that emit a single
    command string drive the argv-shaped exec skills (shell_exec,
    ssh_exec); it is :func:`shlex.split` to argv and NEVER handed to a
    shell.  ``{"argv": [...]}`` stays the canonical form.
    """

    def test_cmd_splits_to_argv(self) -> None:
        assert resolve_argv({"cmd": "git status"}, skill="shell_exec") == [
            "git", "status",
        ]

    def test_cmd_equivalent_to_argv(self) -> None:
        # The whole point: {"cmd": "git status"} resolves to the same
        # argv as {"argv": ["git", "status"]}.
        assert (
            resolve_argv({"cmd": "git status"}, skill="shell_exec")
            == resolve_argv({"argv": ["git", "status"]}, skill="shell_exec")
            == ["git", "status"]
        )

    def test_cmd_honours_shell_quoting(self) -> None:
        # shlex, not str.split — a quoted arg stays one token, quotes stripped.
        assert resolve_argv(
            {"cmd": 'git commit -m "fix: the thing"'}, skill="shell_exec",
        ) == ["git", "commit", "-m", "fix: the thing"]

    def test_rejects_both_forms(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            resolve_argv({"argv": ["git"], "cmd": "git"}, skill="shell_exec")

    def test_rejects_neither_form(self) -> None:
        with pytest.raises(ValueError, match="either 'argv' or 'cmd'"):
            resolve_argv({}, skill="shell_exec")

    def test_rejects_empty_cmd(self) -> None:
        with pytest.raises(ValueError, match="empty argv"):
            resolve_argv({"cmd": "   "}, skill="shell_exec")

    def test_rejects_unbalanced_quotes(self) -> None:
        with pytest.raises(ValueError, match="could not parse"):
            resolve_argv({"cmd": 'git commit -m "oops'}, skill="shell_exec")

    def test_rejects_non_string_cmd(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            resolve_argv({"cmd": 123}, skill="shell_exec")

    def test_rejects_non_string_argv(self) -> None:
        with pytest.raises(ValueError, match="non-empty list of strings"):
            resolve_argv({"argv": [1, 2]}, skill="shell_exec")

    def test_error_message_names_skill(self) -> None:
        with pytest.raises(ValueError, match="ssh_exec:"):
            resolve_argv({}, skill="ssh_exec")


class TestShellExecCmdAlias:
    """The ``{"cmd": "<string>"}`` alias on shell_exec.

    The lighthouse e2e (2026-06-18) surfaced the gap this closes: a model
    (qwen3-14b, dev+AUTO) emitted ``{"cmd": "git add ..."}`` and hit
    ``SkillSchemaError: 'argv' is a required property``.  These pin that
    the alias is now (a) accepted at the schema layer — the exact path
    that raised — and (b) executes identically to the argv form.
    """

    def test_cmd_executes_identically_to_argv(self, reg: SkillRegistry) -> None:
        if not shutil.which("git"):
            pytest.skip("git not available")
        a = _invoke(reg, "shell_exec", {"cmd": "git --version"})
        b = _invoke(reg, "shell_exec", {"argv": ["git", "--version"]})
        assert a["returncode"] == b["returncode"] == 0
        assert a["stdout"] == b["stdout"]

    def test_cmd_git_status_matches_argv(
        self, reg: SkillRegistry, tmp_path: Path,
    ) -> None:
        # The literal example from the task: {"cmd": "git status"} must
        # execute identically to {"argv": ["git", "status"]} in the same
        # repo.
        if not shutil.which("git"):
            pytest.skip("git not available")
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "new.txt").write_text("hi")
        a = _invoke(
            reg, "shell_exec", {"cmd": "git status", "cwd": str(repo)},
        )
        b = _invoke(
            reg, "shell_exec", {"argv": ["git", "status"], "cwd": str(repo)},
        )
        assert a["returncode"] == b["returncode"] == 0
        assert a["stdout"] == b["stdout"]
        assert "new.txt" in a["stdout"]

    def test_schema_accepts_cmd_form(self, reg: SkillRegistry) -> None:
        # Goes through reg.invoke — the exact registry path (schema
        # validation THEN adapter) that raised SkillSchemaError in the
        # lighthouse run.  No raise == the gap is closed.
        if not shutil.which("git"):
            pytest.skip("git not available")
        out = asyncio.run(reg.invoke("shell_exec", {"cmd": "git --version"}))
        assert out["returncode"] == 0

    def test_schema_rejects_both_forms(self, reg: SkillRegistry) -> None:
        with pytest.raises(SkillSchemaError):
            asyncio.run(
                reg.invoke("shell_exec", {"argv": ["git"], "cmd": "git"})
            )

    def test_schema_rejects_neither_form(self, reg: SkillRegistry) -> None:
        with pytest.raises(SkillSchemaError):
            asyncio.run(reg.invoke("shell_exec", {"cwd": "."}))

    def test_adapter_rejects_both_forms(self, reg: SkillRegistry) -> None:
        # Defence in depth: even when the schema is bypassed (the minimal
        # CLI image ships without jsonschema), the adapter still rejects
        # ambiguous input.
        with pytest.raises(ValueError, match="not both"):
            _invoke(reg, "shell_exec", {"argv": ["git"], "cmd": "git"})
