"""Tests for the Assistant's authoring skills: skill_author, role_author, release_pipe.

These are the operator-goal (2026-06-22) capabilities that let the Assistant
write new skills/roles (draft → reviewer → write) and plan their release. The
draft mode must be side-effect-free; write mode must emit loadable files; the
release planner must mark sign/publish/promote as operator-gated.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SA = _load("sa_adapter", "skills/skill_author/adapter.py")
RA = _load("ra_adapter", "skills/role_author/adapter.py")
RP = _load("rp_adapter", "skills/release_pipe/adapter.py")


def _run(coro):
    return asyncio.run(coro)


# ---- skill_author -------------------------------------------------------

def test_skill_author_draft_is_side_effect_free_and_valid():
    out = _run(SA.SkillAuthorSkill().invoke(
        {"name": "weather_lookup", "purpose": "Look up the weather.", "risk_level": "LOW"}))
    assert out["written"] is False
    assert out["class"] == "WeatherLookupSkill"
    man = yaml.safe_load(out["files"]["skill.yaml"])
    assert man["adapter_class"] == "WeatherLookupSkill"
    assert man["risk_level"] == "LOW"
    # the generated adapter must be importable Python
    compile(out["files"]["adapter.py"], "weather_lookup/adapter.py", "exec")
    assert "class WeatherLookupSkill(Skill)" in out["files"]["adapter.py"]


def test_skill_author_write_creates_loadable_files(tmp_path):
    out = _run(SA.SkillAuthorSkill().invoke(
        {"name": "ping_host", "purpose": "Ping a host.", "mode": "write",
         "base_dir": str(tmp_path)}))
    assert out["written"] is True
    d = tmp_path / "skills" / "ping_host"
    assert (d / "skill.yaml").is_file() and (d / "adapter.py").is_file() and (d / "__init__.py").is_file()
    assert yaml.safe_load((d / "skill.yaml").read_text())["adapter_class"] == "PingHostSkill"


def test_skill_author_rejects_bad_name():
    try:
        _run(SA.SkillAuthorSkill().invoke({"name": "Bad-Name", "purpose": "x"}))
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---- role_author --------------------------------------------------------

def test_role_author_draft_yaml_parses():
    out = _run(RA.RoleAuthorSkill().invoke(
        {"name": "weather_analyst", "purpose": "Analyse weather data.",
         "task_types": ["ANALYZE"], "allowed_skills": ["python_exec"]}))
    assert out["written"] is False
    rd = yaml.safe_load(out["files"]["role.yaml"])["role_definition"]
    assert rd["purpose"].startswith("Analyse")
    assert rd["allowed_skills"] == ["python_exec"]
    assert "# weather_analyst" in out["files"]["role.md"]


def test_role_author_write(tmp_path):
    out = _run(RA.RoleAuthorSkill().invoke(
        {"name": "tide_watcher", "purpose": "Watch tides.", "mode": "write",
         "base_dir": str(tmp_path)}))
    assert out["written"] is True
    assert (tmp_path / "roles" / "tide_watcher" / "role.yaml").is_file()


# ---- release_pipe -------------------------------------------------------

def test_release_pipe_pack_marks_publish_operator_gated():
    out = _run(RP.ReleasePipeSkill().invoke(
        {"kind": "role", "name": "financial_analyst", "pack": "@acc/capital-markets-roles",
         "version": "1.1.0"}))
    titles = [s["title"] for s in out["steps"]]
    assert any("Reviewer" in t for t in titles)           # reviewer first
    assert any("Publish" in t for t in titles)
    assert any("Promote" in t for t in titles)
    # sign/publish/promote must be operator-gated
    gated_titles = [s["title"] for s in out["steps"] if s["n"] in out["operator_gated_steps"]]
    assert any("Publish" in t for t in gated_titles)
    assert any("Promote" in t for t in gated_titles)


def test_release_pipe_intree_plan():
    out = _run(RP.ReleasePipeSkill().invoke({"kind": "role", "name": "assistant", "in_tree": True}))
    assert "in-tree" in out["artifact"]["home"]
    assert any("acc-promote" in (s.get("command") or "") for s in out["steps"])
