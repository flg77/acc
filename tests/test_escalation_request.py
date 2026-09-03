"""`20260902-assistant-autonomy-prompt-pane-approvals` 1.2b -- escalation.

An invocation the enforcing A-017 / A-018 guard would refuse on the ROLE's
side becomes a question ("allow for this task?") when an oversight queue is
present.  On APPROVE the role is widened for that one call only; every other
outcome still refuses, and a manifest's own sandbox is never escalated.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.capability_dispatch import ParsedInvocation, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.governance_capabilities import CapabilityGuard
from acc.mcp.manifest import MCPManifest
from acc.oversight import HumanOversightQueue
from acc.skills.manifest import SkillManifest

SHELL = SkillManifest(
    skill_id="shell_exec", purpose="Run a process", adapter_class="ShellExecSkill",
    risk_level="HIGH", requires_actions=["execute_shell"], system_access=True,
)
GWS = MCPManifest(
    server_id="google_workspace", purpose="Office", url="http://x/rpc",
    risk_level="MEDIUM", denied_tools=["gmail_send", "drive_delete"],
)


class _Registry:
    def __init__(self, by_id):
        self._by_id = by_id

    def manifest(self, key):
        return self._by_id.get(key)


class _Core:
    def __init__(self, *, enforce=True):
        self._skill_registry = _Registry({"shell_exec": SHELL})
        self._mcp_registry = _Registry({"google_workspace": GWS})
        self._capability_guard = CapabilityGuard(enforce=enforce)
        self.skill_roles: list[RoleDefinitionConfig] = []
        self.mcp_calls: list[tuple[str, str]] = []

    async def invoke_skill(self, skill_id, args, role):
        self.skill_roles.append(role)
        return {"ran": skill_id}

    async def invoke_mcp_tool(self, server_id, tool_name, args, role):
        self.mcp_calls.append((server_id, tool_name))
        return {"ran": tool_name}


def _inv(kind: str, target: str) -> ParsedInvocation:
    return ParsedInvocation(kind=kind, target=target, args={}, raw=f"[{kind.upper()}: {target} {{}}]")


def _queue() -> HumanOversightQueue:
    return HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)


async def _decide(queue: HumanOversightQueue, approve: bool, seen: dict) -> None:
    await asyncio.sleep(0.05)
    (item,) = await queue.pending()
    seen.update(summary=item.summary, risk=item.risk_level)
    if approve:
        await queue.approve(item.oversight_id, "tui:anonymous")
    else:
        await queue.reject(item.oversight_id, "tui:anonymous", "no")


@pytest.mark.asyncio
async def test_off_role_skill_is_asked_and_runs_with_a_one_call_grant():
    queue, core, seen = _queue(), _Core(), {}
    role = RoleDefinitionConfig(allowed_skills=["echo"])          # no shell_exec, MEDIUM ceiling
    task = asyncio.create_task(_decide(queue, True, seen))

    outcomes = await dispatch_invocations(
        [_inv("skill", "shell_exec")], core, role,
        oversight_queue=queue, task_id="t-1", operating_mode="AUTO",
    )
    await task

    assert outcomes[0].ok is True
    assert seen["summary"].startswith("ESCALATION skill shell_exec: Run a process")
    assert "not granted: skill 'shell_exec' not in role.allowed_skills" in seen["summary"]
    assert seen["risk"] == "HIGH"
    # One question, not two: the approved escalation covers the system-access gate.
    assert await queue.recent_decisions() and len(await queue.recent_decisions()) == 1

    (granted,) = core.skill_roles
    assert "shell_exec" in granted.allowed_skills
    assert "execute_shell" in granted.allowed_actions
    assert granted.max_skill_risk_level == "HIGH"
    # ...and only that call: the role definition itself is untouched.
    assert "shell_exec" not in role.allowed_skills
    assert role.max_skill_risk_level == "MEDIUM"
    assert "execute_shell" not in role.allowed_actions


@pytest.mark.asyncio
async def test_denied_escalation_refuses_and_never_runs():
    queue, core, seen = _queue(), _Core(), {}
    role = RoleDefinitionConfig(allowed_skills=["echo"])
    task = asyncio.create_task(_decide(queue, False, seen))

    outcomes = await dispatch_invocations(
        [_inv("skill", "shell_exec")], core, role,
        oversight_queue=queue, task_id="t-2", operating_mode="AUTO",
    )
    await task

    assert outcomes[0].ok is False and "reject" in outcomes[0].error.lower()
    assert core.skill_roles == []


@pytest.mark.asyncio
async def test_no_queue_means_the_refusal_stands():
    core = _Core()
    role = RoleDefinitionConfig(allowed_skills=["echo"])
    outcomes = await dispatch_invocations(
        [_inv("skill", "shell_exec")], core, role, oversight_queue=None, task_id="t-3",
    )
    # The stub adapter records the call; in production core.invoke_skill
    # re-runs A-017 and raises.  What matters here: no widening happened.
    assert outcomes[0].ok is True
    (passed,) = core.skill_roles
    assert passed is role


@pytest.mark.asyncio
async def test_observe_mode_guard_does_not_escalate():
    queue, core = _queue(), _Core(enforce=False)
    role = RoleDefinitionConfig(allowed_skills=["echo"])
    # Observe mode allows with a warning -> no escalation row; the
    # system-access category gate still asks (1.2), which we approve.
    seen: dict = {}
    task = asyncio.create_task(_decide(queue, True, seen))
    outcomes = await dispatch_invocations(
        [_inv("skill", "shell_exec")], core, role,
        oversight_queue=queue, task_id="t-4", operating_mode="AUTO",
    )
    await task
    assert outcomes[0].ok is True
    assert seen["summary"].startswith("SYSTEM-ACCESS ")
    (passed,) = core.skill_roles
    assert passed is role


@pytest.mark.asyncio
async def test_manifest_sandbox_is_never_escalated():
    """denied_tools is the capability's own sandbox, not the role's grant."""
    queue, core = _queue(), _Core()
    role = RoleDefinitionConfig(allowed_mcps=["google_workspace"])
    # drive_delete carries no gate-category marker, so the only thing that
    # could ask here is an escalation -- and the sandbox must not.
    outcomes = await dispatch_invocations(
        [_inv("mcp", "google_workspace.drive_delete")], core, role,
        oversight_queue=queue, task_id="t-5", operating_mode="AUTO",
    )
    assert await queue.pending() == [] and await queue.recent_decisions() == []
    # The refusal is left to the core (the stub records the call; the real
    # invoke_mcp_tool raises A-018).  What we pin: nothing was widened.
    assert outcomes[0].ok is True
    assert role.allowed_mcps == ["google_workspace"]


@pytest.mark.asyncio
async def test_mcp_server_grant_widens_server_list_for_one_call():
    queue, core, seen = _queue(), _Core(), {}
    role = RoleDefinitionConfig(allowed_mcps=[])
    task = asyncio.create_task(_decide(queue, True, seen))
    outcomes = await dispatch_invocations(
        [_inv("mcp", "google_workspace.drive_list")], core, role,
        oversight_queue=queue, task_id="t-6", operating_mode="AUTO",
    )
    await task
    assert outcomes[0].ok is True
    assert seen["summary"].startswith("ESCALATION mcp google_workspace.drive_list")
    assert core.mcp_calls == [("google_workspace", "drive_list")]
    assert role.allowed_mcps == []
