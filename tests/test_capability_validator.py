"""Proposal 033 WS-A — capability validator.

The 2026-06-16 TUI review showed the assistant surfacing ``SkillNotFound``
for capabilities it was granted.  These tests pin the validator that
catches a role referencing a skill/MCP nothing provides, and a package
shipping a malformed capability manifest, BEFORE a ``.accpkg`` is built.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from acc.capability_validator import (
    ERROR,
    WARNING,
    PackageValidationError,
    ValidationFinding,
    format_findings,
    has_errors,
    validate_package_tree,
    validate_role_capabilities,
    validate_roles_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _role(**kw):
    """A duck-typed role config exposing only the capability lists."""
    return SimpleNamespace(
        allowed_skills=kw.get("allowed_skills", []),
        default_skills=kw.get("default_skills", []),
        allowed_mcps=kw.get("allowed_mcps", []),
        default_mcps=kw.get("default_mcps", []),
    )


# ---------------------------------------------------------------------------
# Pure reference check
# ---------------------------------------------------------------------------


def test_clean_role_yields_no_findings():
    role = _role(allowed_skills=["echo"], default_skills=["echo"], allowed_mcps=["arxiv"])
    findings = validate_role_capabilities(
        "tidy", role, available_skills={"echo"}, available_mcps={"arxiv"}
    )
    assert findings == []
    assert not has_errors(findings)


def test_unresolved_skill_is_flagged():
    role = _role(allowed_skills=["nope_skill"])
    findings = validate_role_capabilities(
        "r", role, available_skills={"echo"}, available_mcps=set()
    )
    assert len(findings) == 1
    assert findings[0].severity == ERROR
    assert findings[0].code == "skill_unresolved"
    assert "nope_skill" in findings[0].message


def test_unresolved_mcp_is_flagged():
    role = _role(allowed_mcps=["ghost_server"])
    findings = validate_role_capabilities(
        "r", role, available_skills=set(), available_mcps={"arxiv"}
    )
    assert [f.code for f in findings] == ["mcp_unresolved"]


def test_default_skill_not_in_allowed_is_error():
    # default not contained in allowed is a self-contradiction → ERROR even
    # when the unresolved severity is downgraded to WARNING.
    role = _role(allowed_skills=["echo"], default_skills=["other"])
    findings = validate_role_capabilities(
        "r",
        role,
        available_skills={"echo", "other"},
        available_mcps=set(),
        unresolved_severity=WARNING,
    )
    codes = {(f.code, f.severity) for f in findings}
    assert ("default_skill_not_allowed", ERROR) in codes


def test_default_mcp_not_in_allowed_is_error():
    role = _role(allowed_mcps=["arxiv"], default_mcps=["wikipedia"])
    findings = validate_role_capabilities(
        "r", role, available_skills=set(), available_mcps={"arxiv", "wikipedia"}
    )
    assert any(
        f.code == "default_mcp_not_allowed" and f.severity == ERROR for f in findings
    )


def test_unresolved_severity_is_configurable():
    role = _role(allowed_skills=["nope"])
    warn = validate_role_capabilities(
        "r", role, available_skills=set(), available_mcps=set(),
        unresolved_severity=WARNING,
    )
    assert warn[0].severity == WARNING
    assert not has_errors(warn)


def test_finding_str_is_readable():
    f = ValidationFinding(ERROR, "skill_unresolved", "role:x", "boom")
    assert str(f) == "[ERROR] role:x: boom"
    assert format_findings([]) == "no findings"


# ---------------------------------------------------------------------------
# In-tree control roles must all resolve (CI guard + no-false-positive proof)
# ---------------------------------------------------------------------------


def test_in_tree_control_roles_resolve_clean():
    """Every shipped control role's caps must resolve against the in-tree
    skill/MCP set + core-baseline.  This both guards against future drift
    and proves the validator does not false-positive on the real
    assistant role (allowed_skills + os_basics + workspace + MCPs)."""
    findings = validate_roles_dir(
        REPO_ROOT / "roles",
        skills_root=REPO_ROOT / "skills",
        mcps_root=REPO_ROOT / "mcps",
    )
    errors = [f for f in findings if f.severity == ERROR]
    assert not errors, "in-tree roles have unresolved caps:\n" + format_findings(errors)


# ---------------------------------------------------------------------------
# Package tree validation
# ---------------------------------------------------------------------------


def _write_skill(skills_dir: Path, name: str, body: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True)
    (d / "skill.yaml").write_text(body, encoding="utf-8")


def test_package_tree_clean_skill_has_no_findings(tmp_path):
    _write_skill(tmp_path / "skills", "good", 'purpose: "t"\nadapter_class: "X"\n')
    findings = validate_package_tree(
        tmp_path,
        in_tree_skills_root=REPO_ROOT / "skills",
        in_tree_mcps_root=REPO_ROOT / "mcps",
    )
    assert not has_errors(findings), format_findings(findings)


def test_package_tree_flags_broken_skill_manifest(tmp_path):
    # Missing the required ``adapter_class`` → manifest validation fails.
    _write_skill(tmp_path / "skills", "bad", 'purpose: "t"\n')
    findings = validate_package_tree(
        tmp_path,
        in_tree_skills_root=REPO_ROOT / "skills",
        in_tree_mcps_root=REPO_ROOT / "mcps",
    )
    assert any(
        f.code == "skill_manifest_invalid" and f.severity == ERROR for f in findings
    ), format_findings(findings)


# ---------------------------------------------------------------------------
# build() gate
# ---------------------------------------------------------------------------

_SRC_MANIFEST = 'schema_version: 1\nname: "@test/x"\nversion: "0.1.0"\n'


def test_build_rejects_pack_with_broken_manifest(tmp_path):
    from acc.pkg.build import build

    (tmp_path / "accpkg.yaml").write_text(_SRC_MANIFEST, encoding="utf-8")
    _write_skill(tmp_path / "skills", "bad", 'purpose: "t"\n')  # no adapter_class

    with pytest.raises(PackageValidationError) as exc:
        build(tmp_path, tmp_path / "out.accpkg", validate=True)
    assert any(f.code == "skill_manifest_invalid" for f in exc.value.findings)
    assert not (tmp_path / "out.accpkg").exists()  # nothing written on failure


def test_build_allows_clean_pack(tmp_path):
    from acc.pkg.build import build

    (tmp_path / "accpkg.yaml").write_text(_SRC_MANIFEST, encoding="utf-8")
    _write_skill(tmp_path / "skills", "good", 'purpose: "t"\nadapter_class: "X"\n')

    result = build(tmp_path, tmp_path / "out.accpkg", validate=True)
    assert result.output_path.exists()


def test_build_validate_false_skips_gate(tmp_path):
    from acc.pkg.build import build

    (tmp_path / "accpkg.yaml").write_text(_SRC_MANIFEST, encoding="utf-8")
    _write_skill(tmp_path / "skills", "bad", 'purpose: "t"\n')  # would fail validation

    result = build(tmp_path, tmp_path / "out.accpkg", validate=False)
    assert result.output_path.exists()
