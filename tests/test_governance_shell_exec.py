"""Tests for OpenSpec `20260603-capability-pool` Phase 1.2 — the
shell_exec governance gate.

The Cat-A A-017 pipeline must:
  1. Reject when the role doesn't allowlist ``shell_exec``.
  2. Reject when ``execute_shell`` is missing from ``allowed_actions``.
  3. Reject when ``max_skill_risk_level`` is below HIGH.
  4. Accept when all three pass.  The HIGH-risk manifest causes the
     gate to surface ``needs_oversight`` so the operator approves
     each call on the Compliance pane.
"""

from __future__ import annotations

import pytest

from acc.config import RoleDefinitionConfig
from acc.governance_capabilities import CapabilityGuard
from acc.skills.registry import SkillRegistry


@pytest.fixture(scope="module")
def reg() -> SkillRegistry:
    r = SkillRegistry()
    r.load_from("skills")
    return r


@pytest.fixture(scope="module")
def shell_manifest(reg: SkillRegistry):
    m = reg.manifests().get("shell_exec")
    assert m is not None
    return m


def _role(**kw) -> RoleDefinitionConfig:
    base = {"purpose": "x", "persona": "concise", "task_types": ["TEST"]}
    base.update(kw)
    return RoleDefinitionConfig(**base)


class TestShellExecGate:
    def test_rejects_when_not_allowlisted(self, shell_manifest) -> None:
        role = _role(
            allowed_skills=[],
            allowed_actions=["execute_shell"],
            max_skill_risk_level="HIGH",
        )
        verdict = CapabilityGuard(enforce=True).check_skill_invocation(role, shell_manifest)
        assert not verdict.allowed
        assert "allowlist" in verdict.reason.lower() or \
               "allowed" in verdict.reason.lower()

    def test_rejects_when_action_missing(self, shell_manifest) -> None:
        role = _role(
            allowed_skills=["shell_exec"],
            allowed_actions=[],
            max_skill_risk_level="HIGH",
        )
        verdict = CapabilityGuard(enforce=True).check_skill_invocation(role, shell_manifest)
        assert not verdict.allowed
        assert "execute_shell" in verdict.reason

    def test_rejects_when_risk_ceiling_too_low(self, shell_manifest) -> None:
        role = _role(
            allowed_skills=["shell_exec"],
            allowed_actions=["execute_shell"],
            max_skill_risk_level="MEDIUM",
        )
        verdict = CapabilityGuard(enforce=True).check_skill_invocation(role, shell_manifest)
        assert not verdict.allowed
        # Risk gate fires.
        assert "risk" in verdict.reason.lower()

    def test_accepts_when_all_three_pass(self, shell_manifest) -> None:
        role = _role(
            allowed_skills=["shell_exec"],
            allowed_actions=["execute_shell"],
            max_skill_risk_level="HIGH",
        )
        verdict = CapabilityGuard(enforce=True).check_skill_invocation(role, shell_manifest)
        assert verdict.allowed, verdict.reason
