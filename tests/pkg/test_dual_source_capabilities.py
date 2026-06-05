"""Slice 1 — dual-source MCP/skill discovery from installed packages.

After the Stage 2 cutover, skills/MCPs bundled in an installed .accpkg
land under ACC_PACKAGES_ROOT but were never scanned. These tests pin the
new behavior: the default registries discover packaged capabilities
(in-tree stays authoritative on collision; an explicit base_dir scans
exactly that dir).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from acc.mcp.registry import MCPRegistry
from acc.pkg.build import build
from acc.pkg.install import install
from acc.pkg.registry import Registry, installed_capability_dirs
from acc.skills.registry import SkillRegistry


def _make_pack(src: Path) -> None:
    src.mkdir(parents=True)
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "name": "@acc/cap-test", "version": "0.1.0",
        "roles": [],
        "skills": [{"name": "pkg_skill", "tier": "bundle_in_role", "path": "skills/pkg_skill/"}],
        "mcps": [{"name": "pkg_mcp", "tier": "bundle_in_role", "path": "mcps/pkg_mcp/"}],
    }), encoding="utf-8")
    m = src / "mcps" / "pkg_mcp"; m.mkdir(parents=True)
    (m / "mcp.yaml").write_text(yaml.safe_dump({
        "server_id": "pkg_mcp", "purpose": "packaged test mcp",
        "transport": "http", "url": "http://x/rpc"}), encoding="utf-8")
    s = src / "skills" / "pkg_skill"; s.mkdir(parents=True)
    (s / "__init__.py").write_text("", encoding="utf-8")
    (s / "skill.yaml").write_text(yaml.safe_dump({
        "purpose": "packaged test skill", "adapter_class": "PkgSkill",
        "risk_level": "LOW",
        "input_schema": {"type": "object", "additionalProperties": True},
        "output_schema": {"type": "object", "additionalProperties": True}}), encoding="utf-8")
    (s / "adapter.py").write_text(
        "from acc.skills import Skill\n\n\n"
        "class PkgSkill(Skill):\n"
        "    async def invoke(self, args):\n"
        "        return {'ok': True}\n", encoding="utf-8")


def _install_pack(tmp_path, monkeypatch) -> Path:
    src = tmp_path / "src"; _make_pack(src)
    pkg = tmp_path / "cap-test-0.1.0.accpkg"; build(src, pkg)
    root = tmp_path / "install"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    install(pkg, registry=Registry(root=root))
    # Empty in-tree dirs so the assertions isolate the packaged source.
    (tmp_path / "empty_mcps").mkdir()
    (tmp_path / "empty_skills").mkdir()
    monkeypatch.setenv("ACC_MCPS_ROOT", str(tmp_path / "empty_mcps"))
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(tmp_path / "empty_skills"))
    return root


def test_helper_returns_installed_dirs(tmp_path, monkeypatch):
    _install_pack(tmp_path, monkeypatch)
    mcp_dirs = installed_capability_dirs("mcps")
    skill_dirs = installed_capability_dirs("skills")
    assert any(d.name == "mcps" for d in mcp_dirs)
    assert any(d.name == "skills" for d in skill_dirs)


def test_packaged_mcp_discovered_by_default_load(tmp_path, monkeypatch):
    _install_pack(tmp_path, monkeypatch)
    reg = MCPRegistry(); reg.load_from()
    assert "pkg_mcp" in reg.list_server_ids()


def test_packaged_skill_discovered_by_default_load(tmp_path, monkeypatch):
    _install_pack(tmp_path, monkeypatch)
    reg = SkillRegistry(); reg.load_from()
    assert "pkg_skill" in reg.list_skill_ids()


def test_explicit_base_dir_excludes_packages(tmp_path, monkeypatch):
    _install_pack(tmp_path, monkeypatch)
    only = tmp_path / "only"; only.mkdir()
    mreg = MCPRegistry(); mreg.load_from(only)
    sreg = SkillRegistry(); sreg.load_from(only)
    assert "pkg_mcp" not in mreg.list_server_ids()
    assert "pkg_skill" not in sreg.list_skill_ids()
