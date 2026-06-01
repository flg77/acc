"""Tests for OpenSpec `20260531-role-perception-profiles` Phase 1.

Covers four areas:

1. **Schema** — `RoleDefinitionConfig.perception_profile` defaults to "none"
   and accepts every Literal value; bogus values are rejected.
2. **Workspace renderer** — `_render_workspace` surfaces workspace path,
   allowed skills, allowed MCPs, sibling workers.
3. **Dispatcher** — `render_for_role` picks the right renderer per profile;
   "none" returns empty string; unknown profiles fall back to control.
4. **Marker validation** — workspace profile validates `[USE_SKILL:...]` /
   `[USE_MCP:...]` markers against role.allowed_skills/allowed_mcps.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from acc.config import RoleDefinitionConfig
from acc.perception import (
    PerceptionSnapshot,
    _render_control,
    _render_workspace,
    render_currently_available_block,
    render_for_role,
    validate_marker,
    validate_marker_target,
)


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------


class TestPerceptionProfileSchema:
    def test_default_none(self) -> None:
        r = RoleDefinitionConfig()
        assert r.perception_profile == "none"

    @pytest.mark.parametrize(
        "value",
        [
            "none", "control", "workspace", "domain",
            "reviewer", "output", "customer", "queue",
        ],
    )
    def test_accepts_all_literal_values(self, value: str) -> None:
        r = RoleDefinitionConfig(perception_profile=value)
        assert r.perception_profile == value

    def test_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            RoleDefinitionConfig(perception_profile="bogus")


# ---------------------------------------------------------------------------
# 2. Workspace renderer
# ---------------------------------------------------------------------------


class TestWorkspaceRenderer:
    def _snap(self) -> PerceptionSnapshot:
        return PerceptionSnapshot(
            roster={"coding_agent": ["coding-1", "coding-2"]},
            available_roles=[
                {"kind": "role", "name": "coding_agent", "summary": "code"},
                {"kind": "skill", "name": "fs_write", "summary": "write files"},
            ],
            available_mcps=[
                {"kind": "mcp", "name": "github",
                 "summary": "github access",
                 "metadata": {"risk_level": "MEDIUM"}},
            ],
        )

    def test_surfaces_workspace_path(self, monkeypatch) -> None:
        monkeypatch.setenv("ACC_WORKSPACE_BASE", "/work/project-x")
        role = SimpleNamespace(
            role_label="coding_agent",
            perception_profile="workspace",
            allowed_skills=[],
            allowed_mcps=[],
        )
        out = _render_workspace(self._snap(), role)
        assert "/work/project-x" in out
        assert "Your workspace" in out

    def test_surfaces_allowed_skills(self, monkeypatch) -> None:
        monkeypatch.delenv("ACC_WORKSPACE_BASE", raising=False)
        monkeypatch.delenv("ACC_WORKSPACE_DIR", raising=False)
        role = SimpleNamespace(
            role_label="coding_agent",
            perception_profile="workspace",
            # Both in the snap's catalog (intersected); fs_write is the
            # one in catalog so it must appear.
            allowed_skills=["fs_write"],
            allowed_mcps=[],
        )
        out = _render_workspace(self._snap(), role)
        assert "Your allowed skills" in out
        assert "fs_write" in out
        assert "USE_SKILL" in out

    def test_skills_fall_back_when_catalog_empty(self, monkeypatch) -> None:
        # When the catalog surfaces no skills (orchestrator running
        # without a SkillRegistry), the renderer lists role.allowed_skills
        # verbatim so the LLM still has something to work with.
        monkeypatch.delenv("ACC_WORKSPACE_BASE", raising=False)
        snap = PerceptionSnapshot(
            roster={"coding_agent": ["c-1"]},
            available_roles=[
                {"kind": "role", "name": "coding_agent"},
            ],
            available_skills=[],
        )
        role = SimpleNamespace(
            role_label="coding_agent",
            perception_profile="workspace",
            allowed_skills=["fs_write", "fs_read"],
            allowed_mcps=[],
        )
        out = _render_workspace(snap, role)
        assert "fs_write" in out
        assert "fs_read" in out

    def test_skills_intersected_with_available_skills(self, monkeypatch) -> None:
        # When the catalog DOES surface skills, the renderer shows only
        # the intersection — protects against role.yaml drifting ahead
        # of the live skill registry.
        monkeypatch.delenv("ACC_WORKSPACE_BASE", raising=False)
        snap = PerceptionSnapshot(
            roster={"coding_agent": ["c-1"]},
            available_skills=[
                {"kind": "skill", "name": "fs_write", "summary": "write files"},
            ],
        )
        role = SimpleNamespace(
            role_label="coding_agent",
            perception_profile="workspace",
            # fs_read is in role.yaml but NOT in the live registry —
            # must be hidden so the LLM doesn't try to call it.
            allowed_skills=["fs_write", "fs_read"],
            allowed_mcps=[],
        )
        out = _render_workspace(snap, role)
        assert "fs_write" in out
        assert "fs_read" not in out

    def test_surfaces_allowed_mcps_with_risk(self, monkeypatch) -> None:
        monkeypatch.delenv("ACC_WORKSPACE_BASE", raising=False)
        role = SimpleNamespace(
            role_label="coding_agent",
            perception_profile="workspace",
            allowed_skills=[],
            allowed_mcps=["github"],
        )
        out = _render_workspace(self._snap(), role)
        assert "Your allowed MCPs" in out
        assert "github" in out
        assert "MEDIUM" in out

    def test_surfaces_sibling_workers(self, monkeypatch) -> None:
        role = SimpleNamespace(
            role_label="coding_agent",
            perception_profile="workspace",
            allowed_skills=[],
            allowed_mcps=[],
        )
        out = _render_workspace(self._snap(), role)
        assert "Sibling workers" in out
        assert "coding-1" in out and "coding-2" in out

    def test_workspace_block_smaller_than_control(self) -> None:
        snap = self._snap()
        role = SimpleNamespace(
            role_label="coding_agent",
            perception_profile="workspace",
            allowed_skills=["fs_write"],
            allowed_mcps=["github"],
        )
        ws = _render_workspace(snap, role)
        ctrl = _render_control(snap, role)
        # Workspace profile is meant to be a cheaper view.
        assert len(ws) < len(ctrl)


# ---------------------------------------------------------------------------
# 3. Dispatcher
# ---------------------------------------------------------------------------


class TestRenderForRole:
    def test_none_returns_empty(self) -> None:
        role = SimpleNamespace(perception_profile="none", role_label="x")
        out = render_for_role(PerceptionSnapshot(), role)
        assert out == ""

    def test_control_calls_control_renderer(self) -> None:
        role = SimpleNamespace(
            perception_profile="control",
            role_label="assistant",
            allowed_skills=[],
            allowed_mcps=[],
        )
        snap = PerceptionSnapshot(roster={"assistant": ["a-1"]})
        out = render_for_role(snap, role)
        assert "Currently available" in out
        assert "Running agents" in out
        assert "MUST appear above" in out

    def test_workspace_calls_workspace_renderer(self) -> None:
        role = SimpleNamespace(
            perception_profile="workspace",
            role_label="coding_agent",
            allowed_skills=["fs_write"],
            allowed_mcps=[],
        )
        snap = PerceptionSnapshot(roster={"coding_agent": ["c-1"]})
        out = render_for_role(snap, role)
        assert "Currently available" in out
        # Workspace renderer does NOT emit the catalog-spawn instruction.
        assert "PROPOSE_SPAWN" not in out
        assert "Your allowed skills" in out

    def test_unknown_profile_falls_back_to_control(self) -> None:
        role = SimpleNamespace(
            perception_profile="domain",  # not implemented in Phase 1
            role_label="r",
            allowed_skills=[],
            allowed_mcps=[],
        )
        snap = PerceptionSnapshot(roster={"r": ["r-1"]})
        out = render_for_role(snap, role)
        assert "Running agents" in out  # control-style block

    def test_backward_compat_wrapper_matches_control(self) -> None:
        snap = PerceptionSnapshot(roster={"assistant": ["a-1"]})
        legacy = render_currently_available_block(snap)
        ctrl = _render_control(snap, None)
        assert legacy == ctrl


# ---------------------------------------------------------------------------
# 4. Marker validation per profile
# ---------------------------------------------------------------------------


def _marker(kind: str, target: str) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, target_role=target)


class TestValidateMarker:
    def test_control_rejects_hallucinated(self) -> None:
        snap = PerceptionSnapshot(
            roster={"assistant": ["a-1"]},
            available_roles=[{"kind": "role", "name": "coding_agent"}],
        )
        assert validate_marker(
            "control", snap, _marker("PROPOSE_SPAWN", "coding_agent")
        )
        assert not validate_marker(
            "control", snap, _marker("PROPOSE_SPAWN", "worker-pool")
        )

    def test_workspace_use_skill_against_allowed(self) -> None:
        snap = PerceptionSnapshot()
        role = SimpleNamespace(allowed_skills=["fs_write"], allowed_mcps=[])
        assert validate_marker(
            "workspace", snap, _marker("USE_SKILL", "fs_write"), role=role
        )
        assert not validate_marker(
            "workspace", snap, _marker("USE_SKILL", "rm_rf"), role=role
        )

    def test_workspace_use_mcp_against_allowed(self) -> None:
        snap = PerceptionSnapshot()
        role = SimpleNamespace(allowed_skills=[], allowed_mcps=["github"])
        assert validate_marker(
            "workspace", snap, _marker("USE_MCP", "github"), role=role
        )
        assert not validate_marker(
            "workspace", snap, _marker("USE_MCP", "evil"), role=role
        )

    def test_workspace_other_markers_pass_through(self) -> None:
        # Phase 1: workspace roles emitting unrelated marker kinds aren't
        # validated against the snapshot — accept and let downstream
        # governance handle.
        snap = PerceptionSnapshot()
        role = SimpleNamespace(allowed_skills=[], allowed_mcps=[])
        assert validate_marker(
            "workspace", snap, _marker("PROPOSE_SPAWN", "anything"), role=role
        )

    def test_legacy_validate_marker_target_unchanged(self) -> None:
        snap = PerceptionSnapshot(
            roster={"assistant": ["a-1"]},
            available_roles=[{"kind": "role", "name": "coding_agent"}],
        )
        assert validate_marker_target(snap, "assistant")
        assert validate_marker_target(snap, "coding_agent")
        assert not validate_marker_target(snap, "worker-pool")
