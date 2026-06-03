"""Minimal semver constraint matching — Stage 0 slice 5 helper.

Supports the constraint forms ``acc/pkg/manifest.py`` accepts in
``Dependency.version``:

    exact:  ``1.2.3``
    caret:  ``^1.2.3`` (=> ``>=1.2.3 <2.0.0``)
    tilde:  ``~1.2.3`` (=> ``>=1.2.3 <1.3.0``)
    bound:  ``>=1.2.3``, ``<1.2.3``, ``>1.2``, ``<=1.2``
    range:  ``>=1.2.3 <2.0.0`` (two space-separated bounds)

Pre-release versions are accepted but compared lexicographically
after the numeric triple — sufficient for Stage 0; full pre-release
ordering per semver.org §11 lands when the package format proposal
formalises the spec.

We avoid pulling in ``packaging`` or ``semver`` as a dependency — the
single function ``version_satisfies`` is the entire surface and is
~50 LOC.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)
_PARTIAL_RE = re.compile(
    r"^(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?$"
)


def _parse_version(v: str) -> tuple[int, int, int, str]:
    m = _VERSION_RE.match(v)
    if not m:
        raise ValueError(f"not an exact semver: {v!r}")
    return (
        int(m["major"]),
        int(m["minor"]),
        int(m["patch"]),
        m["pre"] or "",
    )


def _parse_partial(v: str) -> tuple[int, int, int]:
    """``1`` → (1,0,0); ``1.2`` → (1,2,0); ``1.2.3`` → (1,2,3)."""
    m = _PARTIAL_RE.match(v)
    if not m:
        raise ValueError(f"not a partial version: {v!r}")
    return (
        int(m["major"]),
        int(m["minor"] or 0),
        int(m["patch"] or 0),
    )


def _cmp_triple(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return (a > b) - (a < b)


def _cmp_pre(a_pre: str, b_pre: str) -> int:
    """Pre-release ordering — semver.org §11 approximated.

    Per spec: a version WITH pre-release has lower precedence than the
    same version WITHOUT.  Among two pre-releases, dot-separated
    identifiers compare numerically when both numeric, else
    lexicographically.
    """
    if a_pre == b_pre:
        return 0
    if not a_pre:
        return 1   # no-pre > has-pre
    if not b_pre:
        return -1
    a_parts = a_pre.split(".")
    b_parts = b_pre.split(".")
    for ap, bp in zip(a_parts, b_parts):
        if ap.isdigit() and bp.isdigit():
            ai, bi = int(ap), int(bp)
            if ai != bi:
                return (ai > bi) - (ai < bi)
        else:
            if ap != bp:
                return (ap > bp) - (ap < bp)
    return (len(a_parts) > len(b_parts)) - (len(a_parts) < len(b_parts))


def _check_one_bound(version: str, bound: str) -> bool:
    """``bound`` is one constraint atom: ``1.2.3``, ``^1.2.3``, etc."""
    v = _parse_version(version)
    v_triple = v[:3]
    v_pre = v[3]

    if bound.startswith("^"):
        target = _parse_partial(bound[1:])
        # caret allows: same major (when major > 0) or same minor (major == 0)
        if target[0] == 0:
            # ^0.x.y → >=0.x.y <0.(x+1).0   ; ^0.0.x → >=0.0.x <0.0.(x+1)
            if target[1] == 0:
                upper = (0, 0, target[2] + 1)
            else:
                upper = (0, target[1] + 1, 0)
        else:
            upper = (target[0] + 1, 0, 0)
        return target <= v_triple < upper

    if bound.startswith("~"):
        target = _parse_partial(bound[1:])
        upper = (target[0], target[1] + 1, 0)
        return target <= v_triple < upper

    if bound.startswith(">="):
        target = _parse_partial(bound[2:])
        return v_triple >= target

    if bound.startswith("<="):
        target = _parse_partial(bound[2:])
        return v_triple <= target

    if bound.startswith(">"):
        target = _parse_partial(bound[1:])
        return v_triple > target

    if bound.startswith("<"):
        target = _parse_partial(bound[1:])
        return v_triple < target

    # Exact match (and pre-release ordering matters here).
    target = _parse_partial(bound)
    if v_triple != target:
        return False
    # If the constraint specifies an exact pre-release and version
    # carries one, they must match.  An exact constraint with no
    # pre-release matches only the same triple without pre-release.
    return v_pre == ""


def version_satisfies(version: str, constraint: str) -> bool:
    """Return True iff ``version`` (exact semver) matches ``constraint``.

    ``constraint`` may be a single bound or a space-separated pair
    (e.g. ``">=1.2.3 <2.0.0"``).  Both bounds must match.
    """
    parts = constraint.split()
    if not parts:
        raise ValueError("empty constraint")
    return all(_check_one_bound(version, p) for p in parts)
