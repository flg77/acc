"""Tests for the skill/MCP tier-classification script (Stage 0 slice 2).

Hermetic — builds synthetic role.yaml files in a tmp_path so the
test is stable as the real ``roles/`` tree evolves.  The real-tree
output is exercised by the slice-2 manual smoke (`tools/skill_mcp_tiers.yaml`
is committed) and by the pilot roundtrip test in slice 10.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# tools/ is not a package; import via path manipulation.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import classify_skills_mcps as cls  # noqa: E402

from acc.pkg.manifest import CORE_BASELINE_MCPS, CORE_BASELINE_SKILLS  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: synthetic roles tree
# ---------------------------------------------------------------------------


def _write_role(
    roles_dir: Path,
    name: str,
    *,
    skills: list[str] | None = None,
    default_skills: list[str] | None = None,
    mcps: list[str] | None = None,
    default_mcps: list[str] | None = None,
) -> None:
    role_dir = roles_dir / name
    role_dir.mkdir(parents=True)
    payload = {
        "role_definition": {
            "purpose": "test",
            "allowed_skills": skills or [],
            "default_skills": default_skills or [],
            "allowed_mcps": mcps or [],
            "default_mcps": default_mcps or [],
        }
    }
    (role_dir / "role.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


@pytest.fixture
def synthetic_roles(tmp_path: Path) -> Path:
    """Three movable roles + one CONTROL role with deterministic refs."""
    roles = tmp_path / "roles"
    roles.mkdir()

    # Movable roles
    _write_role(
        roles,
        "movable_a",
        skills=["shared_skill", "lone_skill_a", "shell_exec"],  # shell_exec = baseline
        mcps=["shared_mcp", "arxiv"],                            # arxiv = baseline
    )
    _write_role(
        roles,
        "movable_b",
        skills=["shared_skill"],                                 # shared with A
        mcps=["shared_mcp", "lone_mcp_b"],
    )
    _write_role(
        roles,
        "movable_c",
        skills=["shared_skill", "lone_skill_c"],                 # shared with A+B
        mcps=[],
    )

    # CONTROL role — must NOT influence classification.
    _write_role(
        roles,
        "assistant",
        skills=["control_only_skill"],
        mcps=["control_only_mcp"],
    )

    # Non-role dirs to skip
    (roles / "_base").mkdir()
    (roles / "TEMPLATE").mkdir()

    return roles


# ---------------------------------------------------------------------------
# scan_roles — what it includes + excludes
# ---------------------------------------------------------------------------


def test_scan_includes_only_movable_roles(synthetic_roles):
    skill_usage, mcp_usage = cls.scan_roles(synthetic_roles)
    # CONTROL-only refs must NOT appear
    assert "control_only_skill" not in skill_usage
    assert "control_only_mcp" not in mcp_usage


def test_scan_unions_allowed_and_default(tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    _write_role(
        roles,
        "movable_x",
        skills=["only_in_allowed"],
        default_skills=["only_in_default"],
    )
    skill_usage, _ = cls.scan_roles(roles)
    assert set(skill_usage) == {"only_in_allowed", "only_in_default"}


def test_scan_skips_non_role_dirs(synthetic_roles):
    # _base + TEMPLATE were created but have no role.yaml; should not crash
    # and should not appear anywhere.
    skill_usage, mcp_usage = cls.scan_roles(synthetic_roles)
    assert "_base" not in skill_usage.values()
    assert "TEMPLATE" not in mcp_usage.values()


def test_scan_handles_missing_role_yaml(tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "empty_dir").mkdir()  # no role.yaml — should be skipped
    _write_role(roles, "movable_a", skills=["s1"])
    skill_usage, _ = cls.scan_roles(roles)
    assert "s1" in skill_usage


def test_scan_raises_on_malformed_role_yaml(tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    role_dir = roles / "bad"
    role_dir.mkdir()
    (role_dir / "role.yaml").write_text(
        "role_definition:\n  allowed_skills: not_a_list\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="must be a list"):
        cls.scan_roles(roles)


# ---------------------------------------------------------------------------
# classify — the tier rules
# ---------------------------------------------------------------------------


def test_classify_baseline_always_baseline(synthetic_roles):
    skill_usage, mcp_usage = cls.scan_roles(synthetic_roles)
    skills = cls.classify(skill_usage, CORE_BASELINE_SKILLS, "@acc/skills")
    by_name = {c.name: c for c in skills}
    assert by_name["shell_exec"].tier == "core_baseline"
    assert by_name["shell_exec"].suggested_pack is None
    # used_by is empty for baseline (we don't surface in YAML either)
    # but the dataclass still records movable users
    assert "movable_a" in by_name["shell_exec"].used_by


def test_classify_own_pack_threshold(synthetic_roles):
    skill_usage, _ = cls.scan_roles(synthetic_roles)
    skills = cls.classify(skill_usage, CORE_BASELINE_SKILLS, "@acc/skills")
    by_name = {c.name: c for c in skills}
    # shared_skill used by 3 movable roles → own_pack
    assert by_name["shared_skill"].tier == "own_pack"
    assert by_name["shared_skill"].used_by == ("movable_a", "movable_b", "movable_c")
    assert by_name["shared_skill"].suggested_pack == "@acc/skills-shared"


def test_classify_bundle_in_role_single_user(synthetic_roles):
    skill_usage, _ = cls.scan_roles(synthetic_roles)
    skills = cls.classify(skill_usage, CORE_BASELINE_SKILLS, "@acc/skills")
    by_name = {c.name: c for c in skills}
    assert by_name["lone_skill_a"].tier == "bundle_in_role"
    assert by_name["lone_skill_a"].used_by == ("movable_a",)
    assert by_name["lone_skill_a"].suggested_pack is None


def test_classify_mcps_use_mcp_baseline_set(synthetic_roles):
    _, mcp_usage = cls.scan_roles(synthetic_roles)
    mcps = cls.classify(mcp_usage, CORE_BASELINE_MCPS, "@acc/mcp")
    by_name = {c.name: c for c in mcps}
    assert by_name["arxiv"].tier == "core_baseline"
    assert by_name["shared_mcp"].tier == "own_pack"
    assert by_name["lone_mcp_b"].tier == "bundle_in_role"


def test_classify_output_sorted(synthetic_roles):
    skill_usage, _ = cls.scan_roles(synthetic_roles)
    skills = cls.classify(skill_usage, CORE_BASELINE_SKILLS, "@acc/skills")
    names = [c.name for c in skills]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# Render — YAML shape
# ---------------------------------------------------------------------------


def test_render_yaml_round_trips(synthetic_roles):
    skill_usage, mcp_usage = cls.scan_roles(synthetic_roles)
    skills = cls.classify(skill_usage, CORE_BASELINE_SKILLS, "@acc/skills")
    mcps = cls.classify(mcp_usage, CORE_BASELINE_MCPS, "@acc/mcp")
    text = cls.render_yaml(skills, mcps)
    parsed = yaml.safe_load(text)
    assert set(parsed) == {"skills", "mcps"}
    # Baseline entries omit used_by in the YAML (always-true info would
    # be noise).
    shell_exec_entry = next(s for s in parsed["skills"] if s["name"] == "shell_exec")
    assert "used_by" not in shell_exec_entry
    assert shell_exec_entry["tier"] == "core_baseline"
    # own_pack entries carry used_by + suggested_pack.
    shared = next(s for s in parsed["skills"] if s["name"] == "shared_skill")
    assert shared["used_by"] == ["movable_a", "movable_b", "movable_c"]
    assert shared["suggested_pack"] == "@acc/skills-shared"


# ---------------------------------------------------------------------------
# CLI entry point + committed tiers.yaml sanity
# ---------------------------------------------------------------------------


def test_cli_writes_file(tmp_path, synthetic_roles):
    out = tmp_path / "tiers.yaml"
    rc = cls.main(["--roles-dir", str(synthetic_roles), "--output", str(out)])
    assert rc == 0
    assert out.is_file()
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "skills" in data and "mcps" in data


def test_cli_stdout_mode(tmp_path, synthetic_roles, capsys):
    rc = cls.main(["--roles-dir", str(synthetic_roles), "--stdout"])
    assert rc == 0
    captured = capsys.readouterr()
    data = yaml.safe_load(captured.out)
    assert "skills" in data


def test_cli_missing_roles_dir_exits_1(tmp_path):
    rc = cls.main(["--roles-dir", str(tmp_path / "nope")])
    assert rc == 1


def test_committed_tiers_yaml_covers_every_movable_skill():
    """tools/skill_mcp_tiers.yaml must include every skill referenced by
    any movable role.  Regenerate via ``python tools/classify_skills_mcps.py``
    if a new movable role is added that references a new skill/MCP.
    """
    committed = _REPO_ROOT / "tools" / "skill_mcp_tiers.yaml"
    if not committed.is_file():
        pytest.skip("tools/skill_mcp_tiers.yaml not committed yet")
    skill_usage, mcp_usage = cls.scan_roles(_REPO_ROOT / "roles")
    data = yaml.safe_load(committed.read_text(encoding="utf-8"))
    committed_skills = {s["name"] for s in data["skills"]}
    committed_mcps = {m["name"] for m in data["mcps"]}
    missing_skills = set(skill_usage) - committed_skills
    missing_mcps = set(mcp_usage) - committed_mcps
    assert not missing_skills, (
        f"tools/skill_mcp_tiers.yaml is stale — missing skills: "
        f"{sorted(missing_skills)}. Regenerate with "
        "`python tools/classify_skills_mcps.py`."
    )
    assert not missing_mcps, (
        f"tools/skill_mcp_tiers.yaml is stale — missing mcps: "
        f"{sorted(missing_mcps)}."
    )
