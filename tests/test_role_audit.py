"""Tests for `acc-cli role audit` content-drift linter (proposal 006)."""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.cli.role_cmd import (
    LINT_CODES,
    _first_h1,
    _heading_purpose_overlap,
    audit_role,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_role(
    roots: Path,
    name: str,
    *,
    purpose: str = "OK one-liner",
    task_types: list[str] | None = None,
    role_md: str | None = "# OK heading\nbody",
    omit_yaml: bool = False,
) -> Path:
    role_dir = roots / name
    role_dir.mkdir(parents=True, exist_ok=True)
    if not omit_yaml:
        # None → default; explicit [] means "no task_types".
        tt = ["pilot_test"] if task_types is None else task_types
        tt_yaml = "\n".join(f"    - {t}" for t in tt) or "    []"
        (role_dir / "role.yaml").write_text(
            "role_definition:\n"
            f"  purpose: {purpose!r}\n"
            "  persona: 'concise'\n"
            f"  task_types:\n{tt_yaml}\n"
            "  version: '0.1.0'\n",
            encoding="utf-8",
        )
    if role_md is not None:
        (role_dir / "role.md").write_text(role_md, encoding="utf-8")
    return role_dir


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_first_h1_strips_hash():
    assert _first_h1("# Heading text\nbody") == "Heading text"


def test_first_h1_skips_h2():
    assert _first_h1("## Sub\n\n# Real H1\n") == "Real H1"


def test_first_h1_empty_when_no_heading():
    assert _first_h1("no heading at all") == ""


def test_overlap_detects_shared_word():
    """Heading and purpose share a meaningful word — overlap."""
    assert _heading_purpose_overlap(
        "Code review agent",
        "Generate, review, and test code artefacts.",
    )


def test_overlap_detects_substring_match():
    """A heading word that's a substring of a purpose word counts
    as overlap (catches simple morphology: `research` →
    `researcher`)."""
    assert _heading_purpose_overlap(
        "Researcher",
        "Conduct research on a topic.",
    )


def test_overlap_ignores_stopwords():
    """`role` and `agent` are stopwords — overlap on them alone
    doesn't count."""
    assert not _heading_purpose_overlap("The agent", "Role for synthesis")


def test_overlap_zero_when_disjoint():
    assert not _heading_purpose_overlap("Translator", "Synthesise research findings.")


# ---------------------------------------------------------------------------
# LINT codes
# ---------------------------------------------------------------------------


def test_lint001_yaml_missing(tmp_path):
    """role.yaml missing → LINT001."""
    role_dir = tmp_path / "ghost"
    role_dir.mkdir()
    findings = audit_role(tmp_path, "ghost")
    codes = [c for c, _ in findings]
    assert "LINT001" in codes


def test_lint002_empty_purpose(tmp_path):
    """role.yaml purpose is empty → LINT002."""
    _write_role(tmp_path, "p_empty", purpose="")
    findings = audit_role(tmp_path, "p_empty")
    assert "LINT002" in [c for c, _ in findings]


def test_lint003_purpose_too_long(tmp_path):
    """role.yaml purpose > 200 chars → LINT003."""
    _write_role(tmp_path, "p_long", purpose="x" * 250)
    findings = audit_role(tmp_path, "p_long")
    assert "LINT003" in [c for c, _ in findings]


def test_lint004_md_missing_with_tasks(tmp_path):
    """role.md missing + task_types declared → LINT004."""
    _write_role(
        tmp_path,
        "nomd",
        role_md=None,
        task_types=["pilot_test"],
    )
    findings = audit_role(tmp_path, "nomd")
    assert "LINT004" in [c for c, _ in findings]


def test_lint004_silent_when_no_tasks(tmp_path):
    """role.md missing + no task_types → no LINT004 (it's a stub
    role legitimately)."""
    _write_role(tmp_path, "stub", role_md=None, task_types=[])
    findings = audit_role(tmp_path, "stub")
    assert "LINT004" not in [c for c, _ in findings]


def test_lint005_drifted_heading(tmp_path):
    """role.md H1 has no overlap with role.yaml purpose →
    LINT005."""
    _write_role(
        tmp_path, "drift",
        purpose="Synthesise research findings.",
        role_md="# Translator\nbody",
    )
    findings = audit_role(tmp_path, "drift")
    assert "LINT005" in [c for c, _ in findings]


def test_lint005_quiet_on_matching_heading(tmp_path):
    """Matching H1 → no LINT005 (no false positive)."""
    _write_role(
        tmp_path, "match",
        purpose="Generate, review, and test code artefacts.",
        role_md="# Code review and testing agent\nbody",
    )
    findings = audit_role(tmp_path, "match")
    assert "LINT005" not in [c for c, _ in findings]


def test_clean_role_produces_no_findings(tmp_path):
    """A well-authored role produces zero findings."""
    _write_role(
        tmp_path,
        "good",
        purpose="Generate, review, and test code artefacts.",
        role_md="# Generate review and test code\nbody",
    )
    assert audit_role(tmp_path, "good") == []


def test_lint_codes_table_covers_every_emit():
    """Every emitted code MUST be in LINT_CODES (no orphan codes)."""
    # The codes the helper actually emits, derived from its source.
    emitted = {"LINT001", "LINT002", "LINT003", "LINT004", "LINT005"}
    assert emitted <= set(LINT_CODES.keys())
