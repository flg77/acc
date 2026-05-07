"""Arbiter PlanExecutor cluster-dispatch wiring (D1).

PR #26-#30 delivered the wire protocol + estimator + TUI panel +
slash commands for sub-agent clustering, but the arbiter agent
itself did NOT pass ``role_resolver`` / ``skill_resolver`` into
:class:`acc.plan.PlanExecutor`.  Without those callbacks, every
PLAN step falls back to legacy single-agent dispatch — the cluster
fan-out path is unreachable in production.

These tests pin the resolver wiring so the regression cannot return
silently.  We do not boot a full ``Agent`` here (that requires a live
NATS + LanceDB); instead we exercise the resolver builder in
isolation against a fake registry shape.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from acc.agent import Agent


# ---------------------------------------------------------------------------
# Fake registry — duck-typed to ``SkillRegistry.list_skill_ids``.
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, ids: list[str]) -> None:
        self._ids = list(ids)

    def list_skill_ids(self) -> list[str]:
        return list(self._ids)


def _make_arbiter_stub(
    *,
    skill_registry: Any | None,
    roles_root: Path,
):
    """Build an Agent-shaped object with just enough state for the
    resolver builder, bypassing the full ``__init__`` (no Redis,
    LanceDB, NATS).  ``Agent._build_cluster_resolvers`` only reads
    ``self._skill_registry`` so this is sufficient.
    """
    stub = Agent.__new__(Agent)
    stub._skill_registry = skill_registry  # type: ignore[attr-defined]
    return stub


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_role_resolver_returns_role_definition_for_known_role(
    monkeypatch, tmp_path,
):
    """The role_resolver loads a real RoleLoader against the configured
    roles root.  Pin behaviour against the bundled ``coding_agent``
    role so a refactor of RoleLoader's load path surfaces here."""
    repo_root = Path(__file__).resolve().parent.parent
    roles_root = repo_root / "roles"
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))

    stub = _make_arbiter_stub(skill_registry=None, roles_root=roles_root)
    resolve_role, _resolve_skills = stub._build_cluster_resolvers()

    rd = resolve_role("coding_agent")
    assert rd is not None
    assert rd.purpose                                  # populated
    assert "CODE_GENERATE" in (rd.task_types or [])
    assert getattr(rd, "max_parallel_tasks", 1) >= 1


def test_role_resolver_returns_none_for_unknown_role(
    monkeypatch, tmp_path,
):
    """Unknown role_id resolves to None (not raise) — the executor's
    `_maybe_build_cluster` falls back to single-agent dispatch."""
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("ACC_ROLES_ROOT", str(repo_root / "roles"))

    stub = _make_arbiter_stub(skill_registry=None, roles_root=repo_root)
    resolve_role, _ = stub._build_cluster_resolvers()
    assert resolve_role("totally-invented-role") is None


def test_skill_resolver_intersects_allowed_with_live_registry(
    monkeypatch,
):
    """The operator-visible skill list = role.allowed_skills ∩
    registry.list_skill_ids().  A skill that's whitelisted but not
    actually loaded must NOT appear in the resolver's output — the
    cluster panel's skill_in_use column would otherwise show a value
    that the agent cannot invoke."""
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("ACC_ROLES_ROOT", str(repo_root / "roles"))

    # coding_agent's allowed_skills today is ["echo"].  Registry that
    # also exposes a non-allowed skill must not leak it.
    registry = _FakeRegistry(["echo", "shell_exec_pretend_unsafe"])
    stub = _make_arbiter_stub(skill_registry=registry, roles_root=repo_root)
    _, resolve_skills = stub._build_cluster_resolvers()

    assert resolve_skills("coding_agent") == ["echo"]


def test_skill_resolver_empty_when_role_has_no_allowed_skills(monkeypatch):
    """A role with empty ``allowed_skills`` is fail-closed: the
    resolver returns an empty list even when the registry has skills."""
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("ACC_ROLES_ROOT", str(repo_root / "roles"))

    # `arbiter` role has no allowed_skills declared in role.yaml.
    registry = _FakeRegistry(["echo"])
    stub = _make_arbiter_stub(skill_registry=registry, roles_root=repo_root)
    _, resolve_skills = stub._build_cluster_resolvers()
    assert resolve_skills("arbiter") == []


def test_skill_resolver_falls_back_when_registry_is_none(monkeypatch):
    """When the skill registry hasn't initialised (e.g. early arbiter
    startup), the resolver returns the role's declared allowed_skills
    rather than an empty list.  The eventual A-017 invocation gate
    still enforces real-skill-only at dispatch time."""
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("ACC_ROLES_ROOT", str(repo_root / "roles"))

    stub = _make_arbiter_stub(skill_registry=None, roles_root=repo_root)
    _, resolve_skills = stub._build_cluster_resolvers()
    result = resolve_skills("coding_agent")
    assert "echo" in result


def test_role_resolver_swallows_loader_exceptions(monkeypatch, tmp_path):
    """A malformed role directory returns None instead of crashing
    the arbiter.  Cluster dispatch then degrades to single-agent —
    the right operational call: a buggy role file should never
    break the entire bus.
    """
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path))
    bad = tmp_path / "broken_role"
    bad.mkdir()
    (bad / "role.yaml").write_text("role_definition: {not: valid yaml: here}",
                                    encoding="utf-8")

    stub = _make_arbiter_stub(skill_registry=None, roles_root=tmp_path)
    resolve_role, _ = stub._build_cluster_resolvers()
    # Either None or a default-constructed RoleDefinitionConfig is
    # acceptable; the contract is "do not raise".
    rd = resolve_role("broken_role")
    assert rd is None or rd is not None  # tautology: assert no exception
