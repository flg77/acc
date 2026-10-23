"""061 — per-capability digests in the pack manifest (schema v2).

`acc-pkg build` stamps a tree digest on every SkillRef / McpRef and writes a
schema v2 manifest; the reader accepts v1 and v2.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acc.pkg.build import build, tree_digest
from acc.pkg.install import install
from acc.pkg.manifest import AccPkgManifest
from acc.pkg.registry import Registry

_SHA = "a" * 64


def _make_pack(src: Path, *, schema_version: int = 1, **extra) -> Path:
    src.mkdir(parents=True)
    doc = {
        "schema_version": schema_version, "name": "@acc/digest-test", "version": "0.1.0",
        "skills": [{"name": "pkg_skill", "tier": "bundle_in_role", "path": "skills/pkg_skill/"}],
        "mcps": [{"name": "pkg_mcp", "tier": "bundle_in_role", "path": "mcps/pkg_mcp/"}],
    }
    doc.update(extra)
    (src / "accpkg.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    s = src / "skills" / "pkg_skill"; s.mkdir(parents=True)
    (s / "skill.yaml").write_text("purpose: x\n", encoding="utf-8")
    (s / "adapter.py").write_text("X = 1\n", encoding="utf-8")
    m = src / "mcps" / "pkg_mcp"; m.mkdir(parents=True)
    (m / "mcp.yaml").write_text("server_id: pkg_mcp\n", encoding="utf-8")
    return src


# --- manifest v1 / v2 --------------------------------------------------------


def test_v1_manifest_without_digests_still_parses():
    m = AccPkgManifest(name="@acc/x", version="0.1.0", skills=[
        {"name": "s", "tier": "bundle_in_role", "path": "skills/s/"}])
    assert m.schema_version == 1
    assert m.skills[0].sha256 == ""


def test_v2_manifest_with_digests_round_trips():
    m = AccPkgManifest(
        schema_version=2, name="@acc/x", version="0.1.0", content_sha256=_SHA,
        skills=[{"name": "s", "tier": "bundle_in_role", "path": "skills/s/", "sha256": _SHA}],
        mcps=[{"name": "m", "tier": "own_pack", "path": "mcps/m/", "sha256": _SHA}],
    )
    again = AccPkgManifest.model_validate(yaml.safe_load(yaml.safe_dump(m.model_dump(mode="json"))))
    assert again == m
    assert again.mcps[0].sha256 == _SHA


def test_v1_manifest_refuses_a_per_capability_digest():
    with pytest.raises(ValidationError, match="schema_version 2"):
        AccPkgManifest(name="@acc/x", version="0.1.0", skills=[
            {"name": "s", "tier": "bundle_in_role", "path": "skills/s/", "sha256": _SHA}])


def test_built_v2_manifest_needs_every_digest():
    with pytest.raises(ValidationError, match="every skill and MCP"):
        AccPkgManifest(
            schema_version=2, name="@acc/x", version="0.1.0", content_sha256=_SHA,
            skills=[{"name": "s", "tier": "bundle_in_role", "path": "skills/s/"}])


def test_malformed_digest_refused():
    with pytest.raises(ValidationError, match="64 hex"):
        AccPkgManifest(schema_version=2, name="@acc/x", version="0.1.0", skills=[
            {"name": "s", "tier": "bundle_in_role", "path": "skills/s/", "sha256": "abc"}])


# --- tree digest + build ------------------------------------------------------


def test_tree_digest_ignores_bytecode_and_is_location_independent(tmp_path):
    a = tmp_path / "a" / "cap"; a.mkdir(parents=True)
    (a / "x.py").write_text("print(1)\n", encoding="utf-8")
    b = tmp_path / "elsewhere" / "cap"; b.mkdir(parents=True)
    (b / "x.py").write_text("print(1)\n", encoding="utf-8")
    (b / "__pycache__").mkdir()
    (b / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    assert tree_digest(a) == tree_digest(b)
    (b / "x.py").write_text("print(2)\n", encoding="utf-8")
    assert tree_digest(a) != tree_digest(b)


def test_build_stamps_v2_and_per_ref_digests(tmp_path):
    src = _make_pack(tmp_path / "src")
    result = build(src, tmp_path / "out.accpkg")
    m = result.manifest
    assert m.schema_version == 2
    assert m.skills[0].sha256 == tree_digest(src / "skills" / "pkg_skill")
    assert m.mcps[0].sha256 == tree_digest(src / "mcps" / "pkg_mcp")
    with tarfile.open(result.output_path, "r:gz") as tar:
        stamped = yaml.safe_load(tar.extractfile("accpkg.yaml").read())
    assert stamped["schema_version"] == 2
    assert stamped["skills"][0]["sha256"] == m.skills[0].sha256


def test_build_refuses_a_pre_declared_ref_digest(tmp_path):
    src = _make_pack(tmp_path / "src", schema_version=2, skills=[
        {"name": "pkg_skill", "tier": "bundle_in_role", "path": "skills/pkg_skill/", "sha256": _SHA}])
    with pytest.raises(ValueError, match="pre-declare skill 'pkg_skill' sha256"):
        build(src, tmp_path / "out.accpkg")


def test_build_refuses_a_ref_without_a_directory(tmp_path):
    src = _make_pack(tmp_path / "src", mcps=[
        {"name": "ghost", "tier": "bundle_in_role", "path": "mcps/ghost/"}])
    with pytest.raises(ValueError, match="mcp 'ghost': path is not a directory"):
        build(src, tmp_path / "out.accpkg")


def test_installed_digest_matches_the_unpacked_tree(tmp_path):
    src = _make_pack(tmp_path / "src")
    pkg = build(src, tmp_path / "out.accpkg").output_path
    res = install(pkg, registry=Registry(root=tmp_path / "root"))
    on_disk = AccPkgManifest.model_validate(
        yaml.safe_load((res.install_path / "accpkg.yaml").read_text(encoding="utf-8")))
    assert on_disk.schema_version == 2
    assert on_disk.skills[0].sha256 == tree_digest(res.install_path / "skills" / "pkg_skill")
