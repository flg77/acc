"""058 Phase 3 -- the OpenShell sandbox is mandatory, and confinement replaces
the system-access question.

* No sandbox, no code execution: ``shell_exec`` / ``python_exec`` refuse when
  the agent has no sandbox (``ACC_SANDBOX_REQUIRED``, default on, 058 Q2).
* An exec call that runs in the agent's sandbox is not asked about as system
  access under AUTO / ASK_ONCE.  CRITICAL, ASK_PERMISSIONS and destructive
  commands are unchanged: the workspace inside the sandbox is the real one.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.capability_dispatch import ParsedInvocation, ask_stats, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.oversight import HumanOversightQueue
from acc.sandbox.runner import SandboxUnavailable, maybe_run_sandboxed, sandbox_required


def test_the_sandbox_is_required_by_default():
    assert sandbox_required({}) is True
    assert sandbox_required({"ACC_SANDBOX_REQUIRED": "0"}) is False


def test_no_sandbox_means_no_execution():
    with pytest.raises(SandboxUnavailable, match="needs an OpenShell sandbox"):
        maybe_run_sandboxed(["echo", "hi"], env={})


def test_a_developer_box_may_opt_out():
    assert maybe_run_sandboxed(["echo", "hi"], env={"ACC_SANDBOX_REQUIRED": "0"}) is None


@pytest.mark.asyncio
async def test_shell_exec_refuses_without_a_sandbox(monkeypatch):
    from skills.shell_exec.adapter import ShellExecSkill

    monkeypatch.setenv("ACC_SANDBOX_REQUIRED", "1")
    monkeypatch.delenv("ACC_SANDBOX_NAME", raising=False)
    skill = ShellExecSkill.__new__(ShellExecSkill)
    with pytest.raises(SandboxUnavailable):
        await skill.invoke({"argv": ["echo", "hi"]})


# ---------------------------------------------------------------------------
# Confinement instead of consent
# ---------------------------------------------------------------------------


class _M:
    def __init__(self, risk: str, **flags) -> None:
        self.risk_level = risk
        self.purpose = "stub"
        for k, v in flags.items():
            setattr(self, k, v)


class _Core:
    def __init__(self) -> None:
        manifests = {"shell_exec": _M("HIGH", system_access=True),
                     "python_exec": _M("HIGH", system_access=True),
                     "fs_write": _M("HIGH", system_access=True),
                     "nuke": _M("CRITICAL", system_access=True)}
        self._skill_registry = type("R", (), {"manifest": lambda _s, k: manifests.get(k)})()
        self._mcp_registry = None
        self.calls: list[str] = []

    async def invoke_skill(self, skill_id, args, role):
        self.calls.append(skill_id)
        return {"returncode": 0}


_ROLE = RoleDefinitionConfig(domain_id="coding_agent",
                             allowed_skills=["shell_exec", "python_exec", "fs_write", "nuke"],
                             max_skill_risk_level="CRITICAL")


async def _dispatch(invs, mode, *, approve: bool = True):
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=2)

    async def answer():
        while True:
            await asyncio.sleep(0.01)
            for item in await queue.pending():
                if approve:
                    await queue.approve(item.oversight_id, "op", answer="2" if item.question else "")
                else:
                    await queue.reject(item.oversight_id, "op", reason="no")

    task = asyncio.create_task(answer())
    try:
        return await dispatch_invocations(invs, _Core(), _ROLE, oversight_queue=queue,
                                          task_id="t-1", operating_mode=mode,
                                          thread_ids=["t-1"], workspace="")
    finally:
        task.cancel()


def _inv(target, **args):
    return ParsedInvocation(kind="skill", target=target, args=args, raw=target)


@pytest.fixture
def sandboxed(monkeypatch):
    monkeypatch.setenv("ACC_SANDBOX_NAME", "acc-coding-agent")
    monkeypatch.setenv("ACC_SANDBOX_REQUIRED", "1")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["AUTO", "ASK_ONCE"])
async def test_sandboxed_exec_is_not_asked(sandboxed, mode):
    outcomes = await _dispatch([_inv("shell_exec", argv=["pytest"]),
                                _inv("python_exec", code="print(1)")], mode)
    assert ask_stats(outcomes)["asks"] == 0 and all(o.ok for o in outcomes)


@pytest.mark.asyncio
async def test_fs_write_runs_in_the_agent_and_is_still_asked(sandboxed):
    outcomes = await _dispatch([_inv("fs_write", path="a.py", content="x")], "AUTO")
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_a_destructive_command_in_the_sandbox_is_still_asked(sandboxed):
    outcomes = await _dispatch([_inv("shell_exec", argv=["git", "reset", "--hard"])], "AUTO")
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_critical_is_still_asked_in_the_sandbox(sandboxed):
    outcomes = await _dispatch([_inv("nuke")], "AUTO")
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_ask_me_each_step_still_asks_in_the_sandbox(sandboxed):
    outcomes = await _dispatch([_inv("shell_exec", argv=["ls"])], "ASK_PERMISSIONS")
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_without_a_sandbox_exec_is_asked_as_before(monkeypatch):
    monkeypatch.delenv("ACC_SANDBOX_NAME", raising=False)
    outcomes = await _dispatch([_inv("shell_exec", argv=["ls"])], "AUTO")
    assert ask_stats(outcomes)["asks"] == 1
