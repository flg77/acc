"""Unit tests for container/production/package-analysis.md completeness.

No container runtime required.

Rules enforced:
  PKG-001  package-analysis.md exists in production/
  PKG-002  Every dependency from pyproject.toml has an entry in the analysis
  PKG-003  pydantic is marked as pip-only (RHEL ships v1, ACC requires v2)
  PKG-004  nats-py is marked as not available via RPM
  PKG-005  lancedb is marked as not available via RPM
  PKG-006  The document mentions at least one RPM-available package
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
PRODUCTION_DIR = REPO_ROOT / "container" / "production"
ANALYSIS_PATH = PRODUCTION_DIR / "package-analysis.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def analysis_content() -> str:
    assert ANALYSIS_PATH.exists(), f"package-analysis.md not found at {ANALYSIS_PATH}"
    return ANALYSIS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject_deps() -> list[str]:
    """Return the list of package names from pyproject.toml dependencies."""
    with PYPROJECT_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    deps_raw = data["project"]["dependencies"]
    # Extract just the package name (before any version specifier)
    names = []
    for dep in deps_raw:
        # e.g. "nats-py>=2.7,<3.0" → "nats-py"
        name = re.split(r"[>=<!~\s\[]", dep)[0].strip()
        names.append(name)
    return names


def test_pkg_001_analysis_exists() -> None:
    """PKG-001: package-analysis.md must exist in container/production/."""
    assert ANALYSIS_PATH.exists(), (
        "package-analysis.md is missing from container/production/. "
        "This document is required to track RPM vs pip decision for each dependency."
    )


def test_pkg_002_all_deps_covered(analysis_content: str, pyproject_deps: list[str]) -> None:
    """PKG-002: Every pyproject.toml dependency must appear in the analysis."""
    missing = []
    for dep in pyproject_deps:
        # Normalise: treat - and _ as interchangeable (pip package naming convention)
        # Use re.sub to avoid the double-replacement bug from chaining .replace() calls:
        #   dep.lower().replace("-", "[-_]").replace("_", "[-_]")
        # would corrupt already-substituted "[-_]" patterns.
        normalised = re.sub(r'[-_]', r'[-_]', dep.lower())
        if not re.search(normalised, analysis_content, re.IGNORECASE):
            missing.append(dep)
    assert not missing, (
        f"The following dependencies from pyproject.toml are not mentioned in "
        f"package-analysis.md: {missing}. Update the analysis document."
    )


def test_pkg_003_pydantic_pip_only(analysis_content: str) -> None:
    """PKG-003: pydantic must be marked as pip-only (RHEL ships v1, ACC requires v2)."""
    # The document should explicitly note pydantic and pip
    pydantic_section = re.search(r"pydantic.*", analysis_content, re.IGNORECASE)
    assert pydantic_section, "package-analysis.md must document pydantic"
    # Should indicate it must be pip-installed (version mismatch with RHEL)
    context = analysis_content[
        max(0, pydantic_section.start() - 100):pydantic_section.end() + 300
    ]
    assert "pip" in context.lower() or "v2" in context.lower(), (
        "package-analysis.md must note that pydantic requires pip installation "
        "(RHEL ships v1; ACC requires v2)."
    )


def test_pkg_004_nats_py_not_available_as_rpm(analysis_content: str) -> None:
    """PKG-004: nats-py must be documented as not available via RPM."""
    assert "nats" in analysis_content.lower(), (
        "package-analysis.md must document nats-py availability."
    )
    # Find the nats section and check it's flagged as pip-only
    nats_match = re.search(r"nats.py.*", analysis_content, re.IGNORECASE)
    assert nats_match, "nats-py must have an entry in the analysis"
    context = analysis_content[nats_match.start():nats_match.end() + 200]
    assert any(marker in context for marker in ["pip only", "pip-only", "No", "❌"]), (
        "nats-py must be marked as pip-only (not available as RPM from RHEL/EPEL)."
    )


def test_pkg_005_lancedb_not_available_as_rpm(analysis_content: str) -> None:
    """PKG-005: lancedb must be documented as not available via RPM."""
    assert "lancedb" in analysis_content.lower(), (
        "package-analysis.md must document lancedb availability."
    )
    lancedb_match = re.search(r"lancedb.*", analysis_content, re.IGNORECASE)
    assert lancedb_match, "lancedb must have an entry in the analysis"
    context = analysis_content[lancedb_match.start():lancedb_match.end() + 200]
    assert any(marker in context for marker in ["pip only", "pip-only", "No", "❌"]), (
        "lancedb must be marked as pip-only (not available as RPM from RHEL/EPEL)."
    )


def test_pkg_006_at_least_one_rpm_package(analysis_content: str) -> None:
    """PKG-006: At least one package must be documented as installable via RPM."""
    # Should contain references to RPM-available packages
    rpm_indicators = ["✅ Yes", "RPM", "microdnf", "python3-pyyaml", "python3-cryptography"]
    assert any(indicator in analysis_content for indicator in rpm_indicators), (
        "package-analysis.md must document at least one package available as RPM "
        "(e.g., pyyaml, cryptography)."
    )
