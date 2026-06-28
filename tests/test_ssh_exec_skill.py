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
from acc.skills import SkillSchemaError
from acc.skills.registry import SkillRegistry, _validate


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


class TestCmdAlias:
    """``ssh_exec`` shares the ``{"cmd": "<string>"}`` alias with
    shell_exec (via ``acc.skills.resolve_argv``): a single command string
    is shlex-split into the remote argv, then shlex-quoted onto the ssh
    wire.  ``host`` stays separately required; argv/cmd are mutually
    exclusive.
    """

    def test_cmd_resolves_like_argv(
        self, reg: SkillRegistry, monkeypatch,
    ) -> None:
        # Allowlisted host + missing identity → the same "identity file"
        # rejection the argv form gives, proving the cmd string was
        # accepted and split into the remote argv (the adapter got past
        # argv resolution + the host gate).
        monkeypatch.setenv("ACC_SSH_HOST_ALLOWLIST", "build1.example.com")
        with pytest.raises(ValueError, match="identity file"):
            _invoke(reg, {
                "host": "build1.example.com",
                "cmd": "systemctl is-active sshd",
                "identity_file": "/nonexistent/key",
            })

    def test_schema_accepts_host_plus_cmd(self, manifest) -> None:
        # No raise == accepted.
        _validate(
            {"host": "localhost", "cmd": "echo hi"},
            manifest.input_schema,
            where="ssh_exec input",
        )

    def test_schema_rejects_both_forms(self, manifest) -> None:
        with pytest.raises(SkillSchemaError):
            _validate(
                {"host": "localhost", "argv": ["echo"], "cmd": "echo"},
                manifest.input_schema,
                where="ssh_exec input",
            )

    def test_schema_still_requires_host(self, manifest) -> None:
        with pytest.raises(SkillSchemaError):
            _validate(
                {"cmd": "echo hi"},
                manifest.input_schema,
                where="ssh_exec input",
            )

    def test_schema_rejects_neither_argv_nor_cmd(self, manifest) -> None:
        with pytest.raises(SkillSchemaError):
            _validate(
                {"host": "localhost"},
                manifest.input_schema,
                where="ssh_exec input",
            )
