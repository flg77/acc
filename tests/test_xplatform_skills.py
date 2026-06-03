"""Tests for the v0.3.50 cross-platform skill pool.

Twelve stdlib-based skills that behave identically on Linux, macOS,
and Windows.  Smoke each adapter end-to-end.
"""

from __future__ import annotations

import asyncio
import json
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
    skill = reg.get(sid)
    assert skill is not None, f"missing skill: {sid}"
    return asyncio.run(skill.invoke(args))


class TestHeadTail:
    def test_head(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        out = _invoke(reg, "head_text", {"path": str(f), "lines": 3})
        assert out["lines"] == ["a", "b", "c"]
        assert out["count"] == 3

    def test_tail(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        out = _invoke(reg, "tail_text", {"path": str(f), "lines": 2})
        assert out["lines"] == ["d", "e"]


class TestCountLines:
    def test_counts(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("a\nb\nc\n")
        out = _invoke(reg, "count_lines", {"path": str(f)})
        assert out["lines"] == 3


class TestSystemInfo:
    def test_hostname(self, reg: SkillRegistry) -> None:
        out = _invoke(reg, "hostname", {})
        assert out["hostname"]

    def test_whoami(self, reg: SkillRegistry) -> None:
        out = _invoke(reg, "whoami", {})
        assert "user" in out

    def test_uname(self, reg: SkillRegistry) -> None:
        out = _invoke(reg, "uname_info", {})
        assert out["system"] in {"Linux", "Darwin", "Windows"}
        assert out["python"]

    def test_date_now(self, reg: SkillRegistry) -> None:
        out = _invoke(reg, "date_now", {})
        assert "utc_iso" in out
        assert out["epoch"] > 0


class TestFsOps:
    def test_du_dir(self, reg: SkillRegistry, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("12345")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("67")
        out = _invoke(reg, "du_dir", {"path": str(tmp_path)})
        assert out["bytes"] == 7
        assert out["files"] == 2

    def test_mkdir_p(self, reg: SkillRegistry, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "tree" / "here"
        out = _invoke(reg, "mkdir_p", {"path": str(target)})
        assert out["exists"]
        assert target.is_dir()

    def test_touch_file(self, reg: SkillRegistry, tmp_path: Path) -> None:
        target = tmp_path / "marker"
        out = _invoke(reg, "touch_file", {"path": str(target)})
        assert out["exists"]
        assert target.is_file()


class TestReadJson:
    def test_parses(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "x.json"
        f.write_text(json.dumps({"hello": "world", "n": 42}))
        out = _invoke(reg, "read_json", {"path": str(f)})
        assert out["data"] == {"hello": "world", "n": 42}

    def test_rejects_invalid(self, reg: SkillRegistry, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("{not valid")
        with pytest.raises(ValueError, match="invalid JSON"):
            _invoke(reg, "read_json", {"path": str(f)})


class TestHttpGet:
    def test_rejects_non_http(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError, match="http"):
            _invoke(reg, "http_get", {"url": "file:///etc/passwd"})

    def test_requires_external_api_action(self, reg: SkillRegistry) -> None:
        m = reg.manifest("http_get")
        assert m is not None
        assert "use_external_api" in m.requires_actions
        assert m.risk_level == "MEDIUM"


class TestAssistantElevation:
    def test_assistant_has_operator_requested_skills(self) -> None:
        """v0.3.50 — operator-requested elevation.  The Assistant now
        gets the explicit skill set: grep_text, pwd, shell_exec,
        test_execution, fs_write, fs_read, find_files, echo, env_get,
        git_status, ls_dir, which_cmd."""
        import yaml
        from acc.config import RoleDefinitionConfig
        data = yaml.safe_load(open("roles/assistant/role.yaml"))["role_definition"]
        r = RoleDefinitionConfig(**data)
        for sid in [
            "grep_text", "pwd", "shell_exec", "test_execution",
            "fs_write", "fs_read", "find_files", "echo", "env_get",
            "git_status", "ls_dir", "which_cmd",
        ]:
            assert sid in r.allowed_skills, f"assistant missing {sid}"
        assert r.workspace_access is True
        assert r.max_skill_risk_level == "HIGH"
        assert "execute_shell" in r.allowed_actions


class TestSampleRoleYaml:
    def test_sample_parses(self) -> None:
        import yaml
        from acc.config import RoleDefinitionConfig
        data = yaml.safe_load(open("docs/sample-role.yaml"))["role_definition"]
        r = RoleDefinitionConfig(**data)
        assert r.os_basics is True
        assert r.workspace_access is True
        # Sample includes shell_exec + ssh_exec + xplatform skills.
        for sid in ["shell_exec", "ssh_exec", "http_get",
                    "hostname", "uname_info", "ls_dir"]:
            assert sid in r.allowed_skills, sid
