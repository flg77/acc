"""033 WS-G Part 1 — ``get_allowed_installed_capabilities`` intersection.

The Nucleus pane wants the operator-meaningful overlap between what a
role MAY reach (``allowed_skills`` / ``allowed_mcps``) and what the
running process actually loaded (the skill + MCP registries).  These
tests pin the pure intersection helper: it returns the sorted overlap,
tolerates None / missing fields, and never reaches the filesystem.
"""

from __future__ import annotations

from acc.capability_index import get_allowed_installed_capabilities


class _FakeRole:
    def __init__(self, skills, mcps):
        self.allowed_skills = skills
        self.allowed_mcps = mcps


class _FakeSkillRegistry:
    """Mimics SkillRegistry's ``list_skill_ids()`` surface."""

    def __init__(self, ids):
        self._ids = list(ids)

    def list_skill_ids(self):
        return sorted(self._ids)


class _FakeMCPRegistry:
    """Mimics MCPRegistry's ``list_server_ids()`` surface."""

    def __init__(self, ids):
        self._ids = list(ids)

    def list_server_ids(self):
        return sorted(self._ids)


# ---------------------------------------------------------------------------
# Core intersection
# ---------------------------------------------------------------------------


def test_intersection_role_allows_ab_registry_has_a():
    """Role allows [a, b]; only [a] installed → returns [a] (the spec case)."""
    role = _FakeRole(skills=["a", "b"], mcps=["a", "b"])
    skills, mcps = get_allowed_installed_capabilities(
        role,
        _FakeSkillRegistry(["a"]),
        _FakeMCPRegistry(["a"]),
    )
    assert skills == ["a"]
    assert mcps == ["a"]


def test_intersection_is_sorted():
    role = _FakeRole(skills=["zeta", "alpha", "mid"], mcps=["z", "a"])
    skills, mcps = get_allowed_installed_capabilities(
        role,
        _FakeSkillRegistry(["mid", "alpha", "zeta", "extra"]),
        _FakeMCPRegistry(["a", "z", "unused"]),
    )
    assert skills == ["alpha", "mid", "zeta"]
    assert mcps == ["a", "z"]


def test_installed_but_not_allowed_is_excluded():
    """A capability the registry has but the role does NOT grant is dropped."""
    role = _FakeRole(skills=["a"], mcps=[])
    skills, mcps = get_allowed_installed_capabilities(
        role,
        _FakeSkillRegistry(["a", "b", "c"]),
        _FakeMCPRegistry(["x"]),
    )
    assert skills == ["a"]
    assert mcps == []


def test_allowed_but_not_installed_is_excluded():
    """A capability the role grants but the registry lacks is dropped."""
    role = _FakeRole(skills=["a", "ghost"], mcps=["m", "ghost_mcp"])
    skills, mcps = get_allowed_installed_capabilities(
        role,
        _FakeSkillRegistry(["a"]),
        _FakeMCPRegistry(["m"]),
    )
    assert skills == ["a"]
    assert mcps == ["m"]


# ---------------------------------------------------------------------------
# Tolerance / edge cases
# ---------------------------------------------------------------------------


def test_none_role_yields_empty():
    skills, mcps = get_allowed_installed_capabilities(
        None, _FakeSkillRegistry(["a"]), _FakeMCPRegistry(["m"])
    )
    assert skills == []
    assert mcps == []


def test_none_registries_yield_empty():
    role = _FakeRole(skills=["a"], mcps=["m"])
    skills, mcps = get_allowed_installed_capabilities(role, None, None)
    assert skills == []
    assert mcps == []


def test_role_missing_fields_yields_empty():
    class _Bare:
        pass

    skills, mcps = get_allowed_installed_capabilities(
        _Bare(), _FakeSkillRegistry(["a"]), _FakeMCPRegistry(["m"])
    )
    assert skills == []
    assert mcps == []


def test_registry_manifests_fallback():
    """A registry exposing only ``manifests()`` (a dict) still aligns —
    the helper falls back to the dict keys when the list accessor is
    absent."""

    class _ManifestsOnly:
        def manifests(self):
            return {"a": object(), "b": object()}

    role = _FakeRole(skills=["a", "z"], mcps=["b"])
    skills, mcps = get_allowed_installed_capabilities(
        role, _ManifestsOnly(), _ManifestsOnly()
    )
    assert skills == ["a"]
    assert mcps == ["b"]
