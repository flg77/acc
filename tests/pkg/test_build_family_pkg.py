"""Tests for the family-pkg builder (Stage 2.2)."""

from __future__ import annotations

import gzip
import hashlib
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import build_family_pkg as bf  # noqa: E402

from acc.pkg.build import MANIFEST_NAME  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic repo fixture — three roles in two families, plus tiers
# ---------------------------------------------------------------------------


def _seed_role(
    root: Path, name: str, *,
    skills: list[str] | None = None,
    mcps: list[str] | None = None,
) -> None:
    role_dir = root / "roles" / name
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "role.yaml").write_text(yaml.safe_dump({
        "role_definition": {
            "purpose": f"synthetic role {name}",
            "allowed_skills": skills or [],
            "allowed_mcps": mcps or [],
        },
    }), encoding="utf-8")


def _seed_skill(root: Path, name: str) -> None:
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.yaml").write_text(f"name: {name}\n", encoding="utf-8")


def _seed_mcp(root: Path, name: str) -> None:
    d = root / "mcps" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "mcp.yaml").write_text(f"name: {name}\n", encoding="utf-8")


@pytest.fixture
def synthetic_repo(tmp_path):
    repo = tmp_path / "repo"
    # Family: workspace_test
    _seed_role(repo, "role_a", skills=["shared_skill"], mcps=["lone_mcp"])
    _seed_role(repo, "role_b", skills=["shared_skill", "lone_skill"])
    _seed_skill(repo, "shared_skill")
    _seed_skill(repo, "lone_skill")
    _seed_mcp(repo, "lone_mcp")
    # Tier YAML
    tiers = repo / "tools" / "skill_mcp_tiers.yaml"
    tiers.parent.mkdir(parents=True)
    tiers.write_text(yaml.safe_dump({
        "skills": [
            {"name": "shared_skill", "tier": "bundle_in_role"},
            {"name": "lone_skill", "tier": "bundle_in_role"},
        ],
        "mcps": [
            {"name": "lone_mcp", "tier": "bundle_in_role"},
        ],
    }), encoding="utf-8")
    return repo


@pytest.fixture
def family_manifest():
    return bf.FamilyManifest(
        name="@acc/test-family-roles",
        roles=("role_a", "role_b"),
        description="synthetic test family",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_family_produces_accpkg(synthetic_repo, family_manifest, tmp_path):
    out_path = tmp_path / "fam.accpkg"
    result = bf.build_family(
        "test-family",
        version="1.0.0",
        repo_root=synthetic_repo,
        output=out_path,
        manifest_override=family_manifest,
    )
    assert result.is_file()
    assert result == out_path


def test_family_pack_contains_both_roles(synthetic_repo, family_manifest, tmp_path):
    out_path = tmp_path / "fam.accpkg"
    bf.build_family(
        "test-family",
        repo_root=synthetic_repo,
        output=out_path,
        manifest_override=family_manifest,
    )
    with gzip.open(out_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r:") as tar:
            names = tar.getnames()
    assert "roles/role_a/role.yaml" in names
    assert "roles/role_b/role.yaml" in names


def test_family_pack_dedupes_shared_skill(synthetic_repo, family_manifest, tmp_path):
    """``shared_skill`` is used by both roles — bundled once."""
    out_path = tmp_path / "fam.accpkg"
    bf.build_family(
        "test-family",
        repo_root=synthetic_repo,
        output=out_path,
        manifest_override=family_manifest,
    )
    with gzip.open(out_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r:") as tar:
            names = tar.getnames()
    # The skill file appears exactly once
    matches = [n for n in names if "skills/shared_skill/" in n]
    assert len(matches) == 1


def test_manifest_lists_all_roles_skills_mcps(synthetic_repo, family_manifest, tmp_path):
    out_path = tmp_path / "fam.accpkg"
    bf.build_family(
        "test-family",
        repo_root=synthetic_repo,
        output=out_path,
        manifest_override=family_manifest,
    )
    with gzip.open(out_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r:") as tar:
            manifest_bytes = tar.extractfile(MANIFEST_NAME).read()
    manifest = yaml.safe_load(manifest_bytes)
    role_names = sorted(r["name"] for r in manifest["roles"])
    assert role_names == ["role_a", "role_b"]
    skill_names = sorted(s["name"] for s in manifest["skills"])
    assert skill_names == ["lone_skill", "shared_skill"]
    mcp_names = sorted(m["name"] for m in manifest["mcps"])
    assert mcp_names == ["lone_mcp"]
    assert manifest["name"] == "@acc/test-family-roles"
    assert manifest["version"] == "1.0.0"


def test_byte_deterministic(synthetic_repo, family_manifest, tmp_path):
    """Same input → same bytes."""
    out1 = tmp_path / "a.accpkg"
    out2 = tmp_path / "b.accpkg"
    bf.build_family("test-family", repo_root=synthetic_repo,
                    output=out1, manifest_override=family_manifest)
    bf.build_family("test-family", repo_root=synthetic_repo,
                    output=out2, manifest_override=family_manifest)
    assert hashlib.sha256(out1.read_bytes()).hexdigest() == \
           hashlib.sha256(out2.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------


def test_missing_role_refused(synthetic_repo, tmp_path):
    bad = bf.FamilyManifest(
        name="@acc/bad",
        roles=("role_a", "role_ghost"),
        description="x",
    )
    with pytest.raises(SystemExit, match="not found"):
        bf.build_family(
            "bad",
            repo_root=synthetic_repo,
            output=tmp_path / "x.accpkg",
            manifest_override=bad,
        )


def test_unclassified_skill_refused(synthetic_repo, tmp_path):
    """A role using a skill missing from the tier YAML fails fast."""
    _seed_role(synthetic_repo, "role_c", skills=["unknown_skill"])
    bad = bf.FamilyManifest(
        name="@acc/bad",
        roles=("role_a", "role_c"),
        description="x",
    )
    with pytest.raises(SystemExit, match="not classified"):
        bf.build_family(
            "bad",
            repo_root=synthetic_repo,
            output=tmp_path / "x.accpkg",
            manifest_override=bad,
        )


def test_unknown_family_key_refused(synthetic_repo, tmp_path):
    with pytest.raises(SystemExit, match="unknown family"):
        bf.build_family(
            "ghost-family",
            repo_root=synthetic_repo,
            output=tmp_path / "x.accpkg",
        )


# ---------------------------------------------------------------------------
# Post-cutover: families are defined in the spearhead, not here
# ---------------------------------------------------------------------------


def test_default_families_is_empty_post_cutover():
    # The movable-role sources + canonical family manifests live in
    # flg77/acc-ecosystem-spearhead now, not this repo.
    assert bf.DEFAULT_FAMILIES == {}


def test_cli_requires_manifest_without_a_family():
    # With DEFAULT_FAMILIES empty, the builder must be driven by --manifest.
    with pytest.raises(SystemExit):
        bf.main([])
