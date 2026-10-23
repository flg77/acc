"""061 — every skill and MCP is checked against its recorded digest at load.

Tamper with a capability and ``enforce`` refuses it with a message naming the
capability, the expected and actual digest and the source; ``report`` logs the
same message and loads it anyway.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from acc import capability_verify as cv
from acc.mcp.registry import MCPRegistry
from acc.pkg import builtin_capabilities
from acc.pkg.build import build, tree_digest
from acc.pkg.install import install
from acc.pkg.registry import Registry
from acc.skills.registry import SkillRegistry

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(cv.MODE_ENV, raising=False)
    monkeypatch.delenv(builtin_capabilities.PUBKEY_ENV, raising=False)
    cv.clear_cache()
    builtin_capabilities._load_cached.cache_clear()
    yield
    cv.clear_cache()
    builtin_capabilities._load_cached.cache_clear()


def _make_pack(src: Path, *, schema_version: int = 1) -> None:
    src.mkdir(parents=True)
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "schema_version": schema_version, "name": "@acc/verify-test", "version": "0.1.0",
        "skills": [{"name": "pkg_skill", "tier": "bundle_in_role", "path": "skills/pkg_skill/"}],
        "mcps": [{"name": "pkg_mcp", "tier": "bundle_in_role", "path": "mcps/pkg_mcp/"}],
    }), encoding="utf-8")
    m = src / "mcps" / "pkg_mcp"; m.mkdir(parents=True)
    (m / "mcp.yaml").write_text(yaml.safe_dump({
        "server_id": "pkg_mcp", "purpose": "packaged test mcp",
        "transport": "http", "url": "http://x/rpc"}), encoding="utf-8")
    s = src / "skills" / "pkg_skill"; s.mkdir(parents=True)
    (s / "skill.yaml").write_text(yaml.safe_dump({
        "purpose": "packaged test skill", "adapter_class": "PkgSkill", "risk_level": "LOW",
        "input_schema": {"type": "object", "additionalProperties": True},
        "output_schema": {"type": "object", "additionalProperties": True}}), encoding="utf-8")
    (s / "adapter.py").write_text(
        "from acc.skills import Skill\n\n\n"
        "class PkgSkill(Skill):\n"
        "    async def invoke(self, args):\n"
        "        return {'ok': True}\n", encoding="utf-8")


def _install(tmp_path, monkeypatch) -> Path:
    src = tmp_path / "src"; _make_pack(src)
    pkg = tmp_path / "verify-test-0.1.0.accpkg"; build(src, pkg)
    root = tmp_path / "install"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    res = install(pkg, registry=Registry(root=root))
    for d in ("empty_mcps", "empty_skills"):
        (tmp_path / d).mkdir()
    monkeypatch.setenv("ACC_MCPS_ROOT", str(tmp_path / "empty_mcps"))
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(tmp_path / "empty_skills"))
    return res.install_path


def _tamper(install_path: Path) -> None:
    with (install_path / "skills" / "pkg_skill" / "adapter.py").open("a", encoding="utf-8") as f:
        f.write("# injected\n")
    with (install_path / "mcps" / "pkg_mcp" / "mcp.yaml").open("a", encoding="utf-8") as f:
        f.write("# injected\n")


# --- mode --------------------------------------------------------------------


def test_default_mode_is_report(monkeypatch):
    assert cv.mode() == "report"
    monkeypatch.setenv(cv.MODE_ENV, "ENFORCE")
    assert cv.mode() == "enforce"
    monkeypatch.setenv(cv.MODE_ENV, "bogus")
    assert cv.mode() == "report"


# --- packs -------------------------------------------------------------------


def test_v2_pack_capabilities_verify(tmp_path, monkeypatch):
    path = _install(tmp_path, monkeypatch)
    result = cv.check("skill", "pkg_skill", path / "skills" / "pkg_skill")
    assert result.ok
    assert "pack @acc/verify-test@0.1.0 signed manifest" in result.source


def test_tampered_pack_refused_in_enforce_with_the_message(tmp_path, monkeypatch, caplog):
    path = _install(tmp_path, monkeypatch)
    expected = tree_digest(path / "skills" / "pkg_skill")
    _tamper(path)
    monkeypatch.setenv(cv.MODE_ENV, "enforce")
    with caplog.at_level(logging.ERROR, logger="acc.capability_verify"):
        skills = SkillRegistry(); skills.load_from()
        mcps = MCPRegistry(); mcps.load_from()
    assert "pkg_skill" not in skills.list_skill_ids()
    assert "pkg_mcp" not in mcps.list_server_ids()
    text = caplog.text
    assert "REFUSED skill 'pkg_skill'" in text
    assert f"expected sha256 {expected}" in text
    assert f"actual sha256 {tree_digest(path / 'skills' / 'pkg_skill')}" in text
    assert "source: pack @acc/verify-test@0.1.0 signed manifest" in text
    assert "reinstall the pack" in text
    assert "REFUSED mcp 'pkg_mcp'" in text


def test_tampered_pack_loads_in_report_mode_and_logs(tmp_path, monkeypatch, caplog):
    path = _install(tmp_path, monkeypatch)
    _tamper(path)
    with caplog.at_level(logging.WARNING, logger="acc.capability_verify"):
        skills = SkillRegistry(); skills.load_from()
    assert "pkg_skill" in skills.list_skill_ids()
    assert "[report] skill 'pkg_skill'" in caplog.text
    assert "digest mismatch" in caplog.text


def test_v1_pack_has_no_digest(tmp_path, monkeypatch):
    path = _install(tmp_path, monkeypatch)
    manifest = path / "accpkg.yaml"
    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    doc["schema_version"] = 1
    for ref in doc["skills"] + doc["mcps"]:
        ref.pop("sha256")
    manifest.write_text(yaml.safe_dump(doc), encoding="utf-8")
    result = cv.check("skill", "pkg_skill", path / "skills" / "pkg_skill")
    assert result.status == cv.MISSING_DIGEST
    assert "schema v1, no per-capability digest" in result.message()
    monkeypatch.setenv(cv.MODE_ENV, "enforce")
    assert cv.gate("skill", "pkg_skill", path / "skills" / "pkg_skill") is False


def test_cache_rehashes_after_a_change(tmp_path, monkeypatch):
    path = _install(tmp_path, monkeypatch)
    cap = path / "skills" / "pkg_skill"
    assert cv.check("skill", "pkg_skill", cap).ok
    (cap / "adapter.py").write_text("tampered = True\n", encoding="utf-8")
    assert cv.check("skill", "pkg_skill", cap).status == cv.MISMATCH


# --- built-in manifest -------------------------------------------------------


def test_committed_builtin_manifest_is_current():
    """If this fails, a skill or MCP changed: run
    ``python -m acc.pkg.builtin_capabilities --write`` and commit the file."""
    assert builtin_capabilities._main(["--check"]) == 0


def test_every_in_tree_capability_verifies(monkeypatch):
    monkeypatch.setenv(cv.MODE_ENV, "enforce")
    skills = SkillRegistry(); n_skills = skills.load_from(_REPO / "skills")
    mcps = MCPRegistry(); n_mcps = mcps.load_from(_REPO / "mcps")
    builtin = builtin_capabilities.load()
    assert builtin.trust == "unsigned"
    for name in builtin.skills:
        assert cv.check("skill", name, _REPO / "skills" / name).ok, name
    for name in builtin.mcps:
        assert cv.check("mcp", name, _REPO / "mcps" / name).ok, name
    assert n_skills > 0 and n_mcps > 0


def test_generate_lists_base_and_skips_template(tmp_path):
    for name in ("_base", "real", "TEMPLATE"):
        d = tmp_path / "skills" / name; d.mkdir(parents=True)
        (d / "skill.yaml").write_text(f"purpose: {name}\n", encoding="utf-8")
    (tmp_path / "skills" / "no_marker").mkdir()
    doc = builtin_capabilities.generate(tmp_path / "skills", tmp_path / "mcps")
    assert sorted(doc["skills"]) == ["_base", "real"]
    assert doc["mcps"] == {}


def test_builtin_tamper_refused_in_enforce(tmp_path, monkeypatch, caplog):
    root = tmp_path / "skills"
    d = root / "echo2"; d.mkdir(parents=True)
    (d / "skill.yaml").write_text("purpose: x\n", encoding="utf-8")
    data = tmp_path / "builtin.yaml"
    data.write_text(builtin_capabilities.render(
        builtin_capabilities.generate(root, tmp_path / "mcps")), encoding="utf-8")
    monkeypatch.setattr(builtin_capabilities, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(builtin_capabilities, "DATA_FILE", "builtin.yaml")
    assert cv.check("skill", "echo2", d).ok
    (d / "skill.yaml").write_text("purpose: tampered\n", encoding="utf-8")
    result = cv.check("skill", "echo2", d)
    assert result.status == cv.MISMATCH
    assert "source: built-in manifest (unsigned" in result.message()


def test_unknown_in_tree_capability_has_no_digest(tmp_path):
    d = tmp_path / "skills" / "not_shipped"; d.mkdir(parents=True)
    (d / "skill.yaml").write_text("purpose: x\n", encoding="utf-8")
    result = cv.check("skill", "not_shipped", d)
    assert result.status == cv.MISSING_DIGEST
    assert "expected sha256 <none>" in result.message()


def test_signature_required_when_a_key_is_configured(tmp_path, monkeypatch):
    data = tmp_path / builtin_capabilities.DATA_FILE
    data.write_text("skills: {echo: " + "a" * 64 + "}\n", encoding="utf-8")
    monkeypatch.setenv(builtin_capabilities.PUBKEY_ENV, str(tmp_path / "cosign.pub"))
    loaded = builtin_capabilities.load(data)
    assert loaded.trust == "rejected"
    assert "does not exist" in loaded.detail
    assert loaded.expected("skill", "echo") == ""
