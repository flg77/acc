"""Tests for the in-place pilot ``.accpkg`` builder (Stage 0 slice 9)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import build_pilot_pkg as bp  # noqa: E402

from acc.pkg.build import MANIFEST_NAME  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic repo fixture — hermetic, no real roles/ involvement
# ---------------------------------------------------------------------------


def _seed_repo(
    root: Path,
    *,
    role_name: str = "movable_a",
    skills_refs: list[str] | None = None,
    mcps_refs: list[str] | None = None,
) -> None:
    """Build a tiny synthetic repo with roles/, skills/, mcps/, and a
    tier YAML covering the refs.
    """
    skills_refs = skills_refs or []
    mcps_refs = mcps_refs or []

    # Role
    role_dir = root / "roles" / role_name
    role_dir.mkdir(parents=True)
    (role_dir / "role.yaml").write_text(
        yaml.safe_dump({
            "role_definition": {
                "purpose": "test",
                "allowed_skills": skills_refs,
                "allowed_mcps": mcps_refs,
            }
        }),
        encoding="utf-8",
    )

    # Skill + MCP source dirs (each one a single file payload)
    for s in skills_refs:
        d = root / "skills" / s
        d.mkdir(parents=True, exist_ok=True)
        (d / "skill.yaml").write_text(f"name: {s}\n", encoding="utf-8")

    for m in mcps_refs:
        d = root / "mcps" / m
        d.mkdir(parents=True, exist_ok=True)
        (d / "mcp.yaml").write_text(f"name: {m}\n", encoding="utf-8")


def _write_tiers(
    root: Path,
    *,
    skills: dict[str, str],
    mcps: dict[str, str],
) -> Path:
    """Write a tiers YAML covering the given names."""
    tiers_path = root / "tools" / "skill_mcp_tiers.yaml"
    tiers_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "skills": [{"name": n, "tier": t} for n, t in skills.items()],
        "mcps": [{"name": n, "tier": t} for n, t in mcps.items()],
    }
    tiers_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tiers_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_pilot_build_emits_accpkg(tmp_path):
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["lone_skill"],
        mcps_refs=["lone_mcp"],
    )
    _write_tiers(
        tmp_path,
        skills={"lone_skill": "bundle_in_role"},
        mcps={"lone_mcp": "bundle_in_role"},
    )

    out = bp.build_pilot("movable_a", repo_root=tmp_path)
    assert out.is_file()
    assert out.name == "acc-movable-a-0.1.0.accpkg"


def test_pilot_build_source_files_untouched(tmp_path):
    """build_pilot_pkg must NOT mutate roles/, skills/, or mcps/."""
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["lone_skill"],
        mcps_refs=[],
    )
    _write_tiers(
        tmp_path,
        skills={"lone_skill": "bundle_in_role"},
        mcps={},
    )

    # Snapshot the source files
    role_before = (tmp_path / "roles" / "movable_a" / "role.yaml").read_bytes()
    skill_before = (tmp_path / "skills" / "lone_skill" / "skill.yaml").read_bytes()

    bp.build_pilot("movable_a", repo_root=tmp_path)

    assert (tmp_path / "roles" / "movable_a" / "role.yaml").read_bytes() == role_before
    assert (tmp_path / "skills" / "lone_skill" / "skill.yaml").read_bytes() == skill_before


def test_baseline_refs_excluded_from_bundle(tmp_path):
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["shell_exec", "lone_skill"],   # shell_exec is baseline
        mcps_refs=["arxiv", "lone_mcp"],            # arxiv is baseline
    )
    # Don't bother declaring baseline ones in tiers — the loader merges
    # the manifest module's baseline sets defensively.
    _write_tiers(
        tmp_path,
        skills={"lone_skill": "bundle_in_role"},
        mcps={"lone_mcp": "bundle_in_role"},
    )

    out = bp.build_pilot("movable_a", repo_root=tmp_path)

    # Open the built pkg and confirm bundled skills/mcps
    import gzip, tarfile
    with gzip.open(out, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        names = tar.getnames()
    assert "skills/lone_skill/skill.yaml" in names
    assert "mcps/lone_mcp/mcp.yaml" in names
    # Baseline ones MUST NOT have been bundled.
    assert not any(n.startswith("skills/shell_exec/") for n in names)
    assert not any(n.startswith("mcps/arxiv/") for n in names)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_pilot_build_is_byte_deterministic(tmp_path):
    """Building twice produces identical bytes — same precondition the
    underlying acc.pkg.build guarantees, end-to-end through the pilot
    assembler.
    """
    import hashlib
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["lone_skill"],
        mcps_refs=[],
    )
    _write_tiers(
        tmp_path,
        skills={"lone_skill": "bundle_in_role"},
        mcps={},
    )

    out1 = tmp_path / "first.accpkg"
    out2 = tmp_path / "second.accpkg"
    bp.build_pilot("movable_a", repo_root=tmp_path, output=out1)
    bp.build_pilot("movable_a", repo_root=tmp_path, output=out2)
    h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
    assert h1 == h2


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------


def test_missing_role_refused(tmp_path):
    _write_tiers(tmp_path, skills={}, mcps={})
    with pytest.raises(SystemExit, match="role not found"):
        bp.build_pilot("ghost", repo_root=tmp_path)


def test_missing_tiers_yaml_refused(tmp_path):
    _seed_repo(tmp_path, role_name="movable_a")
    # Don't write tiers
    with pytest.raises(SystemExit, match="tiers YAML"):
        bp.build_pilot("movable_a", repo_root=tmp_path)


def test_unclassified_skill_refused(tmp_path):
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["unknown_skill"],
    )
    _write_tiers(tmp_path, skills={}, mcps={})
    with pytest.raises(SystemExit, match="not classified"):
        bp.build_pilot("movable_a", repo_root=tmp_path)


def test_unclassified_mcp_refused(tmp_path):
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        mcps_refs=["unknown_mcp"],
    )
    _write_tiers(tmp_path, skills={}, mcps={})
    with pytest.raises(SystemExit, match="not classified"):
        bp.build_pilot("movable_a", repo_root=tmp_path)


def test_missing_skill_source_dir_refused(tmp_path):
    """Tier YAML mentions a skill but the source dir is gone."""
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["referenced_skill"],
    )
    # No skill source dir at all (override _seed_repo's auto-creation)
    import shutil
    shutil.rmtree(tmp_path / "skills" / "referenced_skill")
    _write_tiers(
        tmp_path,
        skills={"referenced_skill": "bundle_in_role"},
        mcps={},
    )
    with pytest.raises(SystemExit, match="skill source dir missing"):
        bp.build_pilot("movable_a", repo_root=tmp_path)


# ---------------------------------------------------------------------------
# Manifest shape from generator
# ---------------------------------------------------------------------------


def test_generated_manifest_strips_underscore_in_pkg_name(tmp_path):
    """``coding_agent`` → ``@acc/coding-agent``."""
    _seed_repo(
        tmp_path,
        role_name="my_role",
        skills_refs=["s1"],
    )
    _write_tiers(tmp_path, skills={"s1": "bundle_in_role"}, mcps={})

    out = bp.build_pilot("my_role", repo_root=tmp_path)

    # Pkg name in manifest
    import gzip, tarfile
    with gzip.open(out, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        manifest_bytes = tar.extractfile(MANIFEST_NAME).read()
    manifest = yaml.safe_load(manifest_bytes)
    assert manifest["name"] == "@acc/my-role"


def test_pycache_excluded_from_skill_copy(tmp_path):
    """``__pycache__`` should NOT leak into the package — would break
    byte-determinism across machines.
    """
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["lone_skill"],
    )
    # Add __pycache__ noise to the source dir
    pyc_dir = tmp_path / "skills" / "lone_skill" / "__pycache__"
    pyc_dir.mkdir(parents=True)
    (pyc_dir / "thing.cpython-311.pyc").write_bytes(b"BINARY")
    _write_tiers(tmp_path, skills={"lone_skill": "bundle_in_role"}, mcps={})

    out = bp.build_pilot("movable_a", repo_root=tmp_path)

    import gzip, tarfile
    with gzip.open(out, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        names = tar.getnames()
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith(".pyc") for n in names)


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------


def test_main_cli_happy(tmp_path, capsys):
    _seed_repo(
        tmp_path,
        role_name="movable_a",
        skills_refs=["s1"],
    )
    _write_tiers(tmp_path, skills={"s1": "bundle_in_role"}, mcps={})

    rc = bp.main([
        "movable_a",
        "--repo-root", str(tmp_path),
    ])
    assert rc == 0
    output_line = capsys.readouterr().out.strip().splitlines()[-1]
    assert "acc-movable-a-0.1.0.accpkg" in output_line


def test_main_cli_version_override(tmp_path):
    _seed_repo(tmp_path, role_name="movable_a", skills_refs=["s1"])
    _write_tiers(tmp_path, skills={"s1": "bundle_in_role"}, mcps={})
    out_path = tmp_path / "custom.accpkg"

    rc = bp.main([
        "movable_a",
        "--repo-root", str(tmp_path),
        "--version", "2.5.0",
        "--output", str(out_path),
    ])
    assert rc == 0
    assert out_path.is_file()


# ---------------------------------------------------------------------------
# Real-tree smoke (coding_agent) — best-effort, skipped on CI if dist/ exists
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Stage 2 cutover removed roles/coding_agent/ in-tree; "
                  "pilot single-role builder superseded by family-pack builder "
                  "(tools/build_family_pkg.py).")
def test_real_coding_agent_smoke():
    """Live test: build the real coding_agent pilot pack against the
    actual repo's roles/skills/mcps tree.  Confirms the script works
    end-to-end on the production source.
    """
    real_dist = _REPO_ROOT / "dist" / "acc-coding-agent-0.1.0.accpkg"
    out = bp.build_pilot("coding_agent")
    assert out.is_file()
    assert out == real_dist

    # Manifest must declare the role + the 3 expected non-baseline refs
    import gzip, tarfile
    with gzip.open(out, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        names = tar.getnames()
    assert any("roles/coding_agent/role.yaml" in n for n in names)
    # echo skill bundled; shell_exec + ssh_exec are baseline → excluded
    assert any("skills/echo/" in n for n in names)
    assert not any("skills/shell_exec/" in n for n in names)
    # echo_server + web_fetch bundled; arxiv + wikipedia baseline → excluded
    assert any("mcps/echo_server/" in n for n in names)
    assert any("mcps/web_fetch/" in n for n in names)
    assert not any("mcps/arxiv/" in n for n in names)
