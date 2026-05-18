"""Loader for the canonical NATS NKey permission matrix.

Proposal 013 (Phase 0c).  The matrix itself lives in
``acc/nats_permissions.yaml`` — the single source of truth shared by
the operator's Go ``nats.conf`` renderer (via ``//go:embed``) and the
standalone Python CLI (``scripts/acc-nkeys``).

This module is the thin Python consumer: it parses the YAML once and
exposes the role → {publish, subscribe} mapping plus a NATS subject
matcher used by the contract test (``tests/test_nats_permissions.py``)
to prove every subject helper in ``acc/signals.py`` is covered.

Keeping the parsing here — rather than inline in every caller — means
the YAML schema is validated in exactly one place.
"""

from __future__ import annotations

import functools
from pathlib import Path

_MATRIX_PATH = Path(__file__).with_name("nats_permissions.yaml")


@functools.lru_cache(maxsize=1)
def load_permission_matrix() -> dict[str, dict[str, list[str]]]:
    """Return the role → {"publish": [...], "subscribe": [...]} map.

    Parsed once and cached.  Raises ``FileNotFoundError`` if the
    canonical YAML is missing (a packaging error) and ``ValueError``
    if its shape is wrong.
    """
    import yaml  # noqa: PLC0415 — optional-ish dep, deferred

    if not _MATRIX_PATH.is_file():
        raise FileNotFoundError(
            f"NATS permission matrix not found at {_MATRIX_PATH} — "
            "packaging error (acc/nats_permissions.yaml must ship "
            "with the acc package)"
        )
    raw = yaml.safe_load(_MATRIX_PATH.read_text(encoding="utf-8")) or {}
    roles = raw.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError(
            "nats_permissions.yaml: top-level `roles:` mapping is "
            "missing or empty"
        )
    matrix: dict[str, dict[str, list[str]]] = {}
    for role, perms in roles.items():
        if not isinstance(perms, dict):
            raise ValueError(
                f"nats_permissions.yaml: role {role!r} is not a mapping"
            )
        pub = perms.get("publish", []) or []
        sub = perms.get("subscribe", []) or []
        if not isinstance(pub, list) or not isinstance(sub, list):
            raise ValueError(
                f"nats_permissions.yaml: role {role!r} publish/subscribe "
                "must be lists"
            )
        matrix[str(role)] = {
            "publish": [str(s) for s in pub],
            "subscribe": [str(s) for s in sub],
        }
    return matrix


def subject_matches(glob: str, subject: str) -> bool:
    """Return True if NATS subject *subject* matches wildcard *glob*.

    Implements NATS subject-matching semantics:

    * tokens are dot-separated;
    * ``*`` matches exactly one token;
    * ``>`` matches one or more trailing tokens (only valid last).

    A literal ``*`` token in *subject* (as produced by the
    ``subject_*_all`` wildcard helpers in ``acc/signals.py``) is
    treated as an ordinary token — so e.g. ``acc.*.queue.*`` matches
    the helper output ``acc.sol-01.queue.*``.
    """
    g_tokens = glob.split(".")
    s_tokens = subject.split(".")
    for i, gt in enumerate(g_tokens):
        if gt == ">":
            # `>` consumes the rest — needs at least one token left.
            return i < len(s_tokens)
        if i >= len(s_tokens):
            return False
        if gt == "*":
            continue
        if gt != s_tokens[i]:
            return False
    return len(g_tokens) == len(s_tokens)


def subject_covered(subject: str) -> bool:
    """Return True if *subject* is matched by at least one publish or
    subscribe glob of at least one role in the matrix.

    Used by the contract test to assert no ``acc/signals.py`` subject
    is left unauthorized (which would fail closed in production)."""
    matrix = load_permission_matrix()
    for perms in matrix.values():
        for glob in (*perms["publish"], *perms["subscribe"]):
            if subject_matches(glob, subject):
                return True
    return False
