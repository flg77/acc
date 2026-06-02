"""Tests for the ``ssh_exec`` skill.

Smoke-grade — exercises the host allowlist + argv hygiene + manifest
risk gating.  We never actually open an SSH connection; the local
``ssh`` binary either fails-fast (no key) or is mocked via
``ACC_SSH_HOST_ALLOWLIST`` + a missing identity_file path so the
adapter raises before subprocess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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
def manifest(reg: SkillRegistry):
    m = reg.manifest("ssh_exec")
    assert m is not None
    return m


def _role(**kw) -> RoleDefinitionConfig:
    base = {"purpose": "x", "persona": "concise", "task_types": ["TEST"]}
    base.update(kw)
    return RoleDefinitionConfig(**base)


def _invoke(reg: SkillRegistry, args: dict) -> dict:
    skill = reg.get("ssh_exec")
    return asyncio.run(skill.invoke(args))


class TestManifest:
    def test_high_risk(self, manifest) -> None:
        assert manifest.risk_level == "HIGH"

    def test_requires_execute_ssh(self, manifest) -> None:
        assert "execute_ssh" in manifest.requires_actions


class TestGovernanceGate:
    def test_rejects_without_action(self, manifest) -> None:
        role = _role(
            allowed_skills=["ssh_exec"],
            allowed_actions=[],
            max_skill_risk_level="HIGH",
        )
        verdict = CapabilityGuard(enforce=True).check_skill_invocation(role, manifest)
        assert not verdict.allowed
        assert "execute_ssh" in verdict.reason

    def test_rejects_with_low_risk_ceiling(self, manifest) -> None:
        role = _role(
            allowed_skills=["ssh_exec"],
            allowed_actions=["execute_ssh"],
            max_skill_risk_level="MEDIUM",
        )
        verdict = CapabilityGuard(enforce=True).check_skill_invocation(role, manifest)
        assert not verdict.allowed
        assert "risk" in verdict.reason.lower()

    def test_accepts_when_all_three_pass(self, manifest) -> None:
        role = _role(
            allowed_skills=["ssh_exec"],
            allowed_actions=["execute_ssh"],
            max_skill_risk_level="HIGH",
        )
        verdict = CapabilityGuard(enforce=True).check_skill_invocation(role, manifest)
        assert verdict.allowed, verdict.reason


class TestHostAllowlist:
    def test_localhost_always_allowed(self, reg: SkillRegistry, monkeypatch) -> None:
        # No allowlist + non-existent identity → adapter rejects on identity,
        # NOT on host check (proving localhost passed the host gate).
        monkeypatch.delenv("ACC_SSH_HOST_ALLOWLIST", raising=False)
        with pytest.raises(ValueError, match="identity file"):
            _invoke(reg, {
                "host": "localhost",
                "argv": ["echo", "hi"],
                "identity_file": "/nonexistent/key",
            })

    def test_unallowlisted_host_rejected(self, reg: SkillRegistry, monkeypatch) -> None:
        monkeypatch.setenv("ACC_SSH_HOST_ALLOWLIST", "build1.example.com")
        with pytest.raises(ValueError, match="(?i)allowlist"):
            _invoke(reg, {
                "host": "evil.example.com",
                "argv": ["echo", "hi"],
            })

    def test_allowlisted_host_passes_gate(
        self, reg: SkillRegistry, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ACC_SSH_HOST_ALLOWLIST", "build1.example.com")
        # Push past host gate, fail on identity_file gate.
        with pytest.raises(ValueError, match="identity file"):
            _invoke(reg, {
                "host": "build1.example.com",
                "argv": ["echo", "hi"],
                "identity_file": "/nonexistent/key",
            })


class TestArgvHygiene:
    def test_rejects_empty_argv(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError):
            _invoke(reg, {"host": "localhost", "argv": []})

    def test_rejects_non_string_argv(self, reg: SkillRegistry) -> None:
        with pytest.raises(ValueError):
            _invoke(reg, {"host": "localhost", "argv": [1, 2]})
