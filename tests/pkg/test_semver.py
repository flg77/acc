"""Tests for the minimal semver constraint matcher (Stage 0 slice 5)."""

from __future__ import annotations

import pytest

from acc.pkg._semver import version_satisfies


# ---------------------------------------------------------------------------
# Exact
# ---------------------------------------------------------------------------


def test_exact_match():
    assert version_satisfies("1.2.3", "1.2.3")


def test_exact_mismatch_in_patch():
    assert not version_satisfies("1.2.4", "1.2.3")


def test_exact_constraint_rejects_prerelease():
    """Exact ``1.2.3`` does NOT match ``1.2.3-rc.1`` — pre-releases are
    lower precedence per semver.
    """
    assert not version_satisfies("1.2.3-rc.1", "1.2.3")


# ---------------------------------------------------------------------------
# Caret
# ---------------------------------------------------------------------------


def test_caret_minor_patch_changes_allowed():
    assert version_satisfies("1.2.4", "^1.2.3")
    assert version_satisfies("1.3.0", "^1.2.3")
    assert version_satisfies("1.9.9", "^1.2.3")


def test_caret_major_change_refused():
    assert not version_satisfies("2.0.0", "^1.2.3")


def test_caret_below_minimum_refused():
    assert not version_satisfies("1.2.2", "^1.2.3")


def test_caret_partial_constraint():
    assert version_satisfies("1.2.0", "^1.2")
    assert version_satisfies("1.9.9", "^1.2")
    assert not version_satisfies("2.0.0", "^1.2")


def test_caret_zero_major_is_minor_locked():
    """``^0.x.y`` allows only ``0.x.*`` per npm semantics."""
    assert version_satisfies("0.2.5", "^0.2.3")
    assert not version_satisfies("0.3.0", "^0.2.3")


def test_caret_zero_major_zero_minor_is_patch_locked():
    assert version_satisfies("0.0.3", "^0.0.3")
    assert not version_satisfies("0.0.4", "^0.0.3")


# ---------------------------------------------------------------------------
# Tilde
# ---------------------------------------------------------------------------


def test_tilde_allows_patch_changes_only():
    assert version_satisfies("1.2.4", "~1.2.3")
    assert version_satisfies("1.2.99", "~1.2.3")
    assert not version_satisfies("1.3.0", "~1.2.3")


def test_tilde_partial():
    assert version_satisfies("1.2.0", "~1.2")
    assert version_satisfies("1.2.5", "~1.2")
    assert not version_satisfies("1.3.0", "~1.2")


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version,constraint,expected",
    [
        ("1.2.3", ">=1.2.3", True),
        ("1.2.2", ">=1.2.3", False),
        ("2.0.0", ">=1.2.3", True),
        ("1.2.3", "<=1.2.3", True),
        ("1.2.4", "<=1.2.3", False),
        ("1.2.3", ">1.2.3", False),
        ("1.2.4", ">1.2.3", True),
        ("1.2.2", "<1.2.3", True),
        ("1.2.3", "<1.2.3", False),
    ],
)
def test_inequality_bounds(version, constraint, expected):
    assert version_satisfies(version, constraint) is expected


# ---------------------------------------------------------------------------
# Range (space-separated)
# ---------------------------------------------------------------------------


def test_range_both_bounds_must_match():
    assert version_satisfies("1.5.0", ">=1.2.3 <2.0.0")
    assert not version_satisfies("1.0.0", ">=1.2.3 <2.0.0")  # below lower
    assert not version_satisfies("2.0.0", ">=1.2.3 <2.0.0")  # at upper


def test_empty_constraint_raises():
    with pytest.raises(ValueError):
        version_satisfies("1.2.3", "")


def test_bad_version_raises():
    with pytest.raises(ValueError):
        version_satisfies("not-semver", "1.2.3")
