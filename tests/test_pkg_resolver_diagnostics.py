"""Proposal 032 Finding D — caret-range correctness + self-diagnosing resolver.

The live coding-pack failure was `no catalog advertises @acc/workspace-roles
matching '^1.0'` against an index that DOES publish 1.0.2 — proving the opaque
message conflated three causes. These tests pin (1) that the caret math is
correct (so the failure is NOT a semver bug) and (2) that the new diagnostic
distinguishes egress failure / absent package / unsatisfied constraint.
"""

from acc.pkg._semver import version_satisfies
from acc.pkg.catalog import _format_resolution_failure


def test_caret_one_zero_matches_patch_excludes_next_major():
    assert version_satisfies("1.0.0", "^1.0") is True
    assert version_satisfies("1.0.2", "^1.0") is True
    assert version_satisfies("1.9.9", "^1.0") is True
    assert version_satisfies("2.0.0", "^1.0") is False
    assert version_satisfies("0.9.9", "^1.0") is False


def test_diag_fetch_failure_surfaces_egress_not_absence():
    msg = _format_resolution_failure(
        "@acc/workspace-roles",
        "^1.0",
        consulted=["acc-canonical"],
        fetch_failures={
            "acc-canonical": "GET https://flg77.github.io/acc-ecosystem/index.json: "
            "<urlopen error timed out>"
        },
        versions_seen=[],
    )
    assert "could not be fetched" in msg
    assert "egress" in msg
    assert "acc-canonical" in msg
    assert "index.json" in msg
    # Must NOT claim the package is absent when we simply could not reach it.
    assert "not present" not in msg


def test_diag_absent_package_when_catalogs_reachable():
    msg = _format_resolution_failure(
        "@acc/workspace-roles",
        "^1.0",
        consulted=["acc-canonical"],
        fetch_failures={},
        versions_seen=[],
    )
    assert "not present in any reachable catalog" in msg
    assert "acc-canonical" in msg


def test_diag_published_but_unsatisfied_lists_versions():
    msg = _format_resolution_failure(
        "@acc/workspace-roles",
        "^3.0",
        consulted=["acc-canonical"],
        fetch_failures={},
        versions_seen=["1.0.2", "2.0.0"],
    )
    assert "IS published at 1.0.2, 2.0.0" in msg
    assert "^3.0" in msg
