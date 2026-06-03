"""Tests for the ``.accpkg`` manifest schema v1 (Stage 0 slice 1).

Coverage matches the proposal's verification list in
``openspec/changes/20260603-acc-pkg-pilot/tasks.md`` section 1.1:

* name + version + semver-constraint validators
* refusal on core_baseline skill/MCP leakage
* refusal on duplicate role/skill/mcp names
* content_sha256 shape
* JSON Schema snapshot matches Pydantic's output (drift catch)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from acc.pkg.manifest import (
    AccPkgManifest,
    CORE_BASELINE_MCPS,
    CORE_BASELINE_SKILLS,
    Dependency,
    McpRef,
    RoleRef,
    SignedDepEntry,
    SkillRef,
    emit_json_schema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_manifest(**overrides):
    """Smallest valid manifest payload, with field overrides."""
    base = {
        "name": "@acc/coding-roles",
        "version": "0.1.0",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Top-level happy path
# ---------------------------------------------------------------------------


def test_minimal_manifest_parses():
    m = AccPkgManifest(**_minimal_manifest())
    assert m.schema_version == 1
    assert m.name == "@acc/coding-roles"
    assert m.version == "0.1.0"
    assert m.roles == []
    assert m.skills == []
    assert m.mcps == []
    assert m.depends_on == []
    assert m.signed_dep_closure == []
    assert m.content_sha256 == ""


def test_full_manifest_parses():
    m = AccPkgManifest(
        name="@acc/research-roles",
        version="1.2.3",
        description="Six research specialists.",
        depends_on=[
            Dependency(name="@acc/skills-pandas-toolkit", version="^1.4"),
            Dependency(name="@acc/mcp-finance-data", version=">=2.0 <3.0"),
        ],
        roles=[
            RoleRef(name="research_critic", path="roles/research_critic/role.yaml"),
            RoleRef(name="research_planner", path="roles/research_planner/role.yaml"),
        ],
        skills=[
            SkillRef(name="arxiv_search", tier="bundle_in_role", path="skills/arxiv_search/"),
        ],
        mcps=[
            McpRef(name="semantic_scholar_extended", tier="bundle_in_role", path="mcps/semantic_scholar_extended/"),
        ],
        content_sha256="a" * 64,
    )
    assert len(m.roles) == 2
    assert m.depends_on[1].version == ">=2.0 <3.0"
    assert m.content_sha256 == "a" * 64


# ---------------------------------------------------------------------------
# Name + version validators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "no-scope",            # missing @scope/
        "@/missing-scope",     # empty scope
        "@scope/",             # empty name
        "@SCOPE/name",         # uppercase scope
        "@scope/Name",         # uppercase name char (uppercase NOT allowed)
        "@scope/name space",   # whitespace
        "@scope/@nested",      # nested @
    ],
)
def test_invalid_package_name_refused(bad_name):
    with pytest.raises(ValidationError):
        AccPkgManifest(**_minimal_manifest(name=bad_name))


@pytest.mark.parametrize(
    "good_name",
    [
        "@acc/coding-roles",
        "@acc/coding_agent",          # underscore in name (yes — matches existing role naming)
        "@a/b",                        # absolute minimum
        "@scope-with-hyphen/name",
        "@scope/name-with-hyphens",
    ],
)
def test_valid_package_names_accepted(good_name):
    m = AccPkgManifest(**_minimal_manifest(name=good_name))
    assert m.name == good_name


@pytest.mark.parametrize(
    "bad_version",
    [
        "1.2",                # missing patch
        "v1.2.3",             # leading v
        "1.2.3+build",        # build metadata disallowed (content sha256 is the build seam)
        "^1.2.3",             # constraint syntax, not exact
        "1.2.3.4",            # too many parts
        "",                   # empty
    ],
)
def test_invalid_exact_version_refused(bad_version):
    with pytest.raises(ValidationError):
        AccPkgManifest(**_minimal_manifest(version=bad_version))


@pytest.mark.parametrize(
    "good_version",
    ["0.0.1", "1.2.3", "10.20.30", "1.2.3-alpha", "1.2.3-rc.4"],
)
def test_valid_exact_versions_accepted(good_version):
    m = AccPkgManifest(**_minimal_manifest(version=good_version))
    assert m.version == good_version


# ---------------------------------------------------------------------------
# Dependency constraint validator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good_constraint",
    ["1.2.3", "^1.2", "^1.2.3", "~1.2", "~1.2.3", ">=1.0", ">=1.0 <2.0", "<2.0", ">1.0"],
)
def test_valid_dependency_constraint_accepted(good_constraint):
    d = Dependency(name="@acc/foo", version=good_constraint)
    assert d.version == good_constraint


@pytest.mark.parametrize(
    "bad_constraint",
    [
        "",
        "not-a-version",
        "^^1.2",                # double prefix
        ">=1.0 <2.0 <3.0",      # too many range parts
        "1.2.3.4",              # too many digits
    ],
)
def test_invalid_dependency_constraint_refused(bad_constraint):
    with pytest.raises(ValidationError):
        Dependency(name="@acc/foo", version=bad_constraint)


def test_dependency_name_must_be_scoped():
    with pytest.raises(ValidationError):
        Dependency(name="unscoped", version="1.0.0")


# ---------------------------------------------------------------------------
# Tier policy: core_baseline must not appear in a package
# ---------------------------------------------------------------------------


def test_core_baseline_skill_in_package_refused():
    # Pick one we know is baseline to keep the test robust against the set
    # growing later.
    baseline = next(iter(CORE_BASELINE_SKILLS))
    with pytest.raises(ValidationError, match="core_baseline"):
        AccPkgManifest(
            **_minimal_manifest(
                skills=[
                    {"name": baseline, "tier": "bundle_in_role", "path": f"skills/{baseline}/"}
                ],
            )
        )


def test_core_baseline_mcp_in_package_refused():
    baseline = next(iter(CORE_BASELINE_MCPS))
    with pytest.raises(ValidationError, match="core_baseline"):
        AccPkgManifest(
            **_minimal_manifest(
                mcps=[
                    {"name": baseline, "tier": "bundle_in_role", "path": f"mcps/{baseline}/"}
                ],
            )
        )


def test_packaged_tier_literal_enforced():
    # Only bundle_in_role and own_pack are allowed on packaged skills.
    with pytest.raises(ValidationError):
        SkillRef(name="custom_skill", tier="core_baseline", path="skills/x/")
    with pytest.raises(ValidationError):
        SkillRef(name="custom_skill", tier="invented_tier", path="skills/x/")


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_duplicate_role_name_refused():
    with pytest.raises(ValidationError, match="duplicate role"):
        AccPkgManifest(
            **_minimal_manifest(
                roles=[
                    {"name": "r1", "path": "roles/r1/role.yaml"},
                    {"name": "r1", "path": "roles/r1-other/role.yaml"},
                ],
            )
        )


def test_duplicate_skill_name_refused():
    with pytest.raises(ValidationError, match="duplicate skill"):
        AccPkgManifest(
            **_minimal_manifest(
                skills=[
                    {"name": "s1", "tier": "bundle_in_role", "path": "skills/s1/"},
                    {"name": "s1", "tier": "own_pack", "path": "skills/s1-other/"},
                ],
            )
        )


def test_duplicate_mcp_name_refused():
    with pytest.raises(ValidationError, match="duplicate mcp"):
        AccPkgManifest(
            **_minimal_manifest(
                mcps=[
                    {"name": "m1", "tier": "bundle_in_role", "path": "mcps/m1/"},
                    {"name": "m1", "tier": "bundle_in_role", "path": "mcps/m1-other/"},
                ],
            )
        )


def test_duplicate_dependency_refused():
    with pytest.raises(ValidationError, match="duplicate dependency"):
        AccPkgManifest(
            **_minimal_manifest(
                depends_on=[
                    {"name": "@acc/foo", "version": "^1.0"},
                    {"name": "@acc/foo", "version": "^2.0"},
                ],
            )
        )


# ---------------------------------------------------------------------------
# content_sha256 shape
# ---------------------------------------------------------------------------


def test_empty_content_sha256_allowed_in_source():
    # Source manifests (before `acc-pkg build` stamps the hash) leave the
    # field empty.  Built manifests stamp it.
    m = AccPkgManifest(**_minimal_manifest(content_sha256=""))
    assert m.content_sha256 == ""


def test_valid_content_sha256_accepted():
    m = AccPkgManifest(**_minimal_manifest(content_sha256="0" * 64))
    assert m.content_sha256 == "0" * 64


@pytest.mark.parametrize(
    "bad_sha",
    [
        "0" * 63,             # too short
        "0" * 65,             # too long
        "g" * 64,             # non-hex char
        "0123",               # nowhere near right
    ],
)
def test_invalid_content_sha256_refused(bad_sha):
    with pytest.raises(ValidationError):
        AccPkgManifest(**_minimal_manifest(content_sha256=bad_sha))


def test_signed_dep_entry_sha256_length_enforced():
    with pytest.raises(ValidationError):
        SignedDepEntry(name="@acc/foo", version="1.0.0", sha256="short")


# ---------------------------------------------------------------------------
# Strict (extra="forbid") behaviour
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_refused():
    with pytest.raises(ValidationError):
        AccPkgManifest(**_minimal_manifest(unknown_field="surprise"))


def test_unknown_dependency_field_refused():
    with pytest.raises(ValidationError):
        AccPkgManifest(
            **_minimal_manifest(
                depends_on=[{"name": "@acc/foo", "version": "^1.0", "rogue": True}],
            )
        )


# ---------------------------------------------------------------------------
# JSON Schema snapshot drift check
# ---------------------------------------------------------------------------


def test_committed_json_schema_matches_pydantic_output():
    """If this fails, regenerate via ``python -m acc.pkg.manifest --emit-schema``."""
    committed_path = Path(__file__).resolve().parents[2] / "acc" / "pkg" / "schema" / "accpkg-v1.json"
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    current = emit_json_schema()
    assert committed == current, (
        "JSON Schema snapshot drift — regenerate with "
        "`python -m acc.pkg.manifest --emit-schema > acc/pkg/schema/accpkg-v1.json`"
    )
