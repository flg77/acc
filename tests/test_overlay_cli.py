"""Tests for ``acc-cli overlay validate|show`` (acc/cli/overlay_cmd.py)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from acc.cli.overlay_cmd import _cmd_show, _cmd_validate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_role(roots: Path, name: str = "coding_agent") -> Path:
    role_dir = roots / name
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "role.yaml").write_text(
        "role_definition:\n"
        "  purpose: 'Write code.'\n"
        "  persona: 'concise'\n"
        "  allowed_skills:\n"
        "    - echo\n"
        "    - git_status\n"
        "  default_skills:\n"
        "    - echo\n"
        "  allowed_mcps:\n"
        "    - arxiv\n"
        "  default_mcps:\n"
        "    - arxiv\n",
        encoding="utf-8",
    )
    return role_dir


def _ns(name: str = "coding_agent", **kw) -> argparse.Namespace:
    base = {"name": name, "format": "json", "allow_unsigned": False}
    base.update(kw)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_clean(tmp_path, monkeypatch, capsys):
    rd = _write_role(tmp_path)
    (rd / "AGENTS.md").write_text(
        "---\nenable_skills: [git_status]\n---\nThis repo.", encoding="utf-8"
    )
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path))

    assert _cmd_validate(_ns()) == 0
    assert "clean" in capsys.readouterr().out


def test_validate_dirty_out_of_envelope(tmp_path, monkeypatch, capsys):
    rd = _write_role(tmp_path)
    (rd / "AGENTS.md").write_text(
        "---\nenable_skills: [rm_rf]\n---\n", encoding="utf-8"
    )
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path))

    assert _cmd_validate(_ns()) == 1
    assert "outside the role envelope" in capsys.readouterr().err


def test_validate_unknown_role(tmp_path, monkeypatch, capsys):
    _write_role(tmp_path)
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path))
    assert _cmd_validate(_ns(name="ghost")) == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_json_reflects_enabled_skill(tmp_path, monkeypatch, capsys):
    rd = _write_role(tmp_path)
    (rd / "AGENTS.md").write_text(
        "---\nenable_skills: [git_status]\n---\nTF repo.", encoding="utf-8"
    )
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path))

    assert _cmd_show(_ns()) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["role"] == "coding_agent"
    prof = data["effective_profile"]
    assert "git_status" in prof["effective_default_skills"]
    assert prof["provenance"]["git_status"] == "AGENTS.md"


def test_show_local_grant_requires_allow_unsigned(tmp_path, monkeypatch, capsys):
    rd = _write_role(tmp_path)
    (rd / "skills" / "tf_plan").mkdir(parents=True)
    (rd / "AGENTS.md").write_text(
        "---\nenable_skills: [tf_plan]\n---\n", encoding="utf-8"
    )
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path))

    # Without --allow-unsigned: tf_plan is out-of-envelope → dropped, not granted.
    assert _cmd_show(_ns(allow_unsigned=False)) == 0
    prof = json.loads(capsys.readouterr().out)["effective_profile"]
    assert "tf_plan" not in prof["effective_default_skills"]
    assert any(d["item"] == "tf_plan" for d in prof["dropped"])

    # With --allow-unsigned: the role-local def is granted (this agent only).
    assert _cmd_show(_ns(allow_unsigned=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["local_candidates"]["skills"] == ["tf_plan"]
    prof2 = out["effective_profile"]
    assert "tf_plan" in prof2["effective_default_skills"]
    assert any(g["item"] == "tf_plan" for g in prof2["local_grants"])


def test_show_yaml_format(tmp_path, monkeypatch, capsys):
    rd = _write_role(tmp_path)
    (rd / "soul.md").write_text(
        "---\nuser_profile: expert\n---\nBe terse.", encoding="utf-8"
    )
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path))

    assert _cmd_show(_ns(format="yaml")) == 0
    out = capsys.readouterr().out
    assert "effective_profile:" in out
    assert "expert" in out
