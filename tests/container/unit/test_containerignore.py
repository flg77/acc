"""Unit tests for .containerignore completeness.

No container runtime required.

Rules enforced:
  IGNORE-001  .containerignore exists in production/
  IGNORE-002  tests/ directory is excluded
  IGNORE-003  .git is excluded
  IGNORE-004  __pycache__ / .pyc files are excluded
  IGNORE-005  .env files are excluded (secrets must not enter build context)
  IGNORE-006  docs/ is excluded (not needed in container)
  IGNORE-007  The production container/ directory itself is excluded
              (prevents recursive context inclusion)
"""

from __future__ import annotations

from pathlib import Path

import pytest

PRODUCTION_DIR = Path(__file__).parent.parent.parent.parent / "container" / "production"
CONTAINERIGNORE_PATH = PRODUCTION_DIR / ".containerignore"


@pytest.fixture(scope="module")
def containerignore_lines() -> list[str]:
    assert CONTAINERIGNORE_PATH.exists(), (
        f".containerignore not found at {CONTAINERIGNORE_PATH}"
    )
    return [
        line.strip()
        for line in CONTAINERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_ignore_001_file_exists() -> None:
    """IGNORE-001: .containerignore must exist in container/production/."""
    assert CONTAINERIGNORE_PATH.exists(), (
        ".containerignore is missing from container/production/. "
        "Without it, the entire repository is sent as build context."
    )


def test_ignore_002_tests_excluded(containerignore_lines: list[str]) -> None:
    """IGNORE-002: tests/ must be excluded from the build context."""
    assert any("tests" in line for line in containerignore_lines), (
        ".containerignore must exclude tests/ — test fixtures should not be in the image."
    )


def test_ignore_003_git_excluded(containerignore_lines: list[str]) -> None:
    """IGNORE-003: .git must be excluded."""
    assert any(line in (".git", ".git/") for line in containerignore_lines), (
        ".containerignore must exclude .git — version control data should not be in the image."
    )


def test_ignore_004_pycache_excluded(containerignore_lines: list[str]) -> None:
    """IGNORE-004: __pycache__ and .pyc files must be excluded."""
    patterns = [line for line in containerignore_lines]
    has_pycache = any("__pycache__" in p for p in patterns)
    has_pyc = any(".pyc" in p for p in patterns)
    assert has_pycache, ".containerignore must exclude __pycache__"
    assert has_pyc, ".containerignore must exclude *.pyc"


def test_ignore_005_env_files_excluded(containerignore_lines: list[str]) -> None:
    """IGNORE-005: .env files must be excluded to prevent secrets in the build context."""
    assert any(".env" in line for line in containerignore_lines), (
        ".containerignore must exclude .env files. "
        "Secrets must never enter the container build context."
    )


def test_ignore_006_docs_excluded(containerignore_lines: list[str]) -> None:
    """IGNORE-006: docs/ should be excluded from build context."""
    assert any("docs" in line for line in containerignore_lines), (
        ".containerignore should exclude docs/ — documentation is not needed in the runtime image."
    )


def test_ignore_007_container_dir_excluded(containerignore_lines: list[str]) -> None:
    """IGNORE-007: container/ itself should be excluded to prevent recursive inclusion."""
    assert any("container" in line for line in containerignore_lines), (
        ".containerignore should exclude container/ to avoid including Containerfiles "
        "and other container/ contents in the build context."
    )
