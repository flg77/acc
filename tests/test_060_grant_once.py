"""060 -- grant once, enforced by the sandbox (OpenSpec
``20261018-grant-once-allowances``): "always allow" on the question, ssh hosts
granted once or by the plan, host grants in the OpenShell network policy,
ssh_exec inside the sandbox."""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from acc import hosts
from acc.capability_dispatch import ParsedInvocation, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.grants import ANSWER_ALWAYS, ANSWER_ONCE, GrantStore, host_target
from acc.mission import MissionStore
from acc.oversight import HumanOversightQueue
from acc.sandbox import policy as SP
from acc.sandbox.runner import SandboxConfig

ROLE = "engineering"


class _M:
    def __init__(self, risk_level="HIGH", **flags):
        self.risk_level = risk_level
        self.purpose = "stub"
        for k, v in flags.items():
            setattr(self, k, v)


class _Registry:
    def __init__(self, manifests):
        self._m = manifests

    def manifest(self, skill_id):
        return self._m.get(skill_id)


class _Core:
    """Records each call, and which ssh host the dispatcher approved for it."""

    def __init__(self):
        self._skill_registry = _Registry({
            "shell_exec": _M(system_access=True), "ssh_exec": _M(system_access=True),
        })
        self._mcp_registry = None
        self.calls: list[tuple[str, dict, bool]] = []

    async def invoke_skill(self, skill_id, args, role):
        self.calls.append((skill_id, args, hosts.allowed(str(args.get("host") or ""))))
        return {"ok": True}


def _inv(target, **args):
    return ParsedInvocation(kind="skill", target=target, args=args, raw=f"[SKILL: {target}]")


def _role():
    return RoleDefinitionConfig(domain_id=ROLE, allowed_skills=["shell_exec", "ssh_exec"],
                                max_skill_risk_level="HIGH")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ACC_OVERSIGHT_HEADLESS", raising=False)
    monkeypatch.delenv("ACC_SSH_HOST_ALLOWLIST", raising=False)
    monkeypatch.delenv("ACC_SANDBOX_NAME", raising=False)
    # No posture finding should block a grant in these unit tests.
    import acc.capability_dispatch as cd
    monkeypatch.setattr(cd, "_posture_blocking", lambda role, core: [])


async def _pending_one(queue):
    for _ in range(100):
        items = await queue.pending()
        if items:
            return items[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no oversight row was submitted")


async def _answer(queue, answer, approver="tui:operator"):
    item = await _pending_one(queue)
    assert await queue.approve(item.oversight_id, approver, answer=answer)
    return item


def _queue():
    return HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)


# ---------------------------------------------------------------------------
# "always allow" on the question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_always_allow_gives_a_grant_and_the_next_call_is_not_asked():
    queue, core = _queue(), _Core()
    task = asyncio.create_task(dispatch_invocations([_inv("shell_exec", cmd="ls")], core, _role(),
                                                    oversight_queue=queue, operating_mode="AUTO"))
    item = await _answer(queue, ANSWER_ALWAYS)
    assert item.question["options"][1]["label"] == f"always allow for {ROLE}"
    [out] = await task
    assert out.ok
    g = await GrantStore.for_queue(queue).covering(ROLE, "skill:shell_exec")
    assert g is not None and g.approved_by == "tui:operator"
    [out2] = await dispatch_invocations([_inv("shell_exec", cmd="ls")], core, _role(),
                                        oversight_queue=queue, operating_mode="AUTO")
    assert out2.ok and await queue.pending() == []


@pytest.mark.asyncio
async def test_allow_once_gives_no_grant():
    queue, core = _queue(), _Core()
    task = asyncio.create_task(dispatch_invocations([_inv("shell_exec", cmd="ls")], core, _role(),
                                                    oversight_queue=queue, operating_mode="AUTO"))
    await _answer(queue, ANSWER_ONCE)
    await task
    assert await GrantStore.for_queue(queue).all() == []


@pytest.mark.asyncio
async def test_always_allow_needs_a_named_approver():
    queue, core = _queue(), _Core()
    task = asyncio.create_task(dispatch_invocations([_inv("shell_exec", cmd="ls")], core, _role(),
                                                    oversight_queue=queue, operating_mode="AUTO"))
    await _answer(queue, ANSWER_ALWAYS, approver="tui:anonymous")
    [out] = await task
    assert out.ok and await GrantStore.for_queue(queue).all() == []


@pytest.mark.asyncio
async def test_a_destructive_call_is_still_asked_despite_a_grant():
    queue, core = _queue(), _Core()
    await GrantStore.for_queue(queue).accept(role=ROLE, target="skill:shell_exec", approved_by="op")
    task = asyncio.create_task(dispatch_invocations([_inv("shell_exec", cmd="rm -rf build")], core, _role(),
                                                    oversight_queue=queue, operating_mode="AUTO"))
    item = await _pending_one(queue)
    assert item.question["destructive"] is True
    await queue.approve(item.oversight_id, "tui:operator")
    await task


# ---------------------------------------------------------------------------
# ssh hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_host_is_asked_and_always_allow_puts_it_on_the_list():
    queue, core = _queue(), _Core()
    task = asyncio.create_task(dispatch_invocations(
        [_inv("ssh_exec", host="lab1.example.internal", cmd="uptime")], core, _role(),
        oversight_queue=queue, operating_mode="AUTO"))
    item = await _answer(queue, ANSWER_ALWAYS)
    assert "ssh to lab1.example.internal" in item.question["text"]
    [out] = await task
    assert out.ok and core.calls[-1][2] is True           # the adapter saw the approval
    assert await GrantStore.for_queue(queue).covering(ROLE, "host:lab1.example.internal")
    # The host grant also answers the call's system-access question: nothing asked.
    [out2] = await dispatch_invocations([_inv("ssh_exec", host="lab1.example.internal", cmd="uptime")],
                                        core, _role(), oversight_queue=queue, operating_mode="AUTO")
    assert out2.ok and await queue.pending() == [] and core.calls[-1][2] is True


@pytest.mark.asyncio
async def test_a_host_in_the_approved_plan_is_not_asked():
    queue, core = _queue(), _Core()
    store = MissionStore.for_queue(queue)
    await store.create(["t-1"], principal="op", approver="op", workspace="/ws",
                       plan={"steps": [{"role": "devops_engineer"}], "hosts": ["lab2.example.internal"]})
    [out] = await dispatch_invocations(
        [_inv("ssh_exec", host="lab2.example.internal", cmd="uptime")], core, _role(),
        oversight_queue=queue, operating_mode="ASK_ONCE", thread_ids=["t-1"], task_id="t-1")
    assert out.ok and await queue.pending() == [] and core.calls[-1][2] is True


@pytest.mark.asyncio
async def test_without_a_queue_an_unknown_host_is_refused():
    core = _Core()
    [out] = await dispatch_invocations([_inv("ssh_exec", host="lab3", cmd="uptime")], core, _role(),
                                       oversight_queue=None, operating_mode="AUTO")
    assert not out.ok and "host allowance list" in out.error and core.calls == []


def test_the_adapter_never_takes_the_models_word_for_a_host(monkeypatch):
    assert not hosts.allowed("lab9")
    token = hosts.approve("lab9")
    try:
        assert hosts.allowed("lab9") and not hosts.allowed("lab10")
    finally:
        hosts.reset(token)
    assert not hosts.allowed("lab9")
    monkeypatch.setenv("ACC_SSH_HOST_ALLOWLIST", "seed1, seed2")
    assert hosts.allowed("seed2") and hosts.allowed("localhost")


def test_the_ssh_adapter_refuses_an_unapproved_host():
    from skills.ssh_exec.adapter import SshExecSkill

    with pytest.raises(ValueError, match="host allowance list"):
        asyncio.run(SshExecSkill.__new__(SshExecSkill).invoke({"host": "lab9", "cmd": "uptime"}))


def test_the_ssh_adapter_runs_inside_the_sandbox(monkeypatch):
    import skills.ssh_exec.adapter as A

    seen = {}

    def fake(argv, *, timeout_s=30, env=None):
        seen["argv"] = argv
        return {"returncode": 0, "stdout": "up", "stderr": "", "duration_s": 0.1}

    monkeypatch.setattr(A, "maybe_run_sandboxed", fake)
    monkeypatch.setenv("ACC_SANDBOX_NAME", "acc-coding-agent")
    token = hosts.approve("lab1")
    try:
        out = asyncio.run(A.SshExecSkill.__new__(A.SshExecSkill).invoke(
            {"host": "lab1", "cmd": "uptime", "identity_file": "/sandbox/.ssh/id_rsa"}))
    finally:
        hosts.reset(token)
    assert out["host"] == "lab1" and out["stdout"] == "up"
    assert seen["argv"][0] == "ssh" and "/sandbox/.ssh/id_rsa" in seen["argv"]


# ---------------------------------------------------------------------------
# OpenShell network policy
# ---------------------------------------------------------------------------


def test_host_targets_render_to_policy_update_specs():
    assert host_target("lab1") == "host:lab1" and host_target("lab1", 2222) == "host:lab1:2222"
    assert SP.parse_host_target("host:lab1:2222") == ("lab1", 2222)
    assert SP.parse_host_target("skill:shell_exec") is None
    cfg = SandboxConfig(enabled=True, sandbox_name="sb", gateway="openshell")
    argv = SP.update_argv(cfg, add=["lab1:22"], remove=["old:22"])
    assert argv == ["openshell", "policy", "update", "sb", "--add-endpoint", "lab1:22",
                    "--binary", "/usr/bin/ssh", "--remove-endpoint", "old:22", "--wait"]


def test_allow_hosts_calls_openshell_only_with_a_sandbox(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("env", {}).get("OPENSHELL_GATEWAY")))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(SP.subprocess, "run", fake_run)
    assert SP.allow_hosts(["host:lab1"], env={}) is False and calls == []
    env = {"ACC_SANDBOX_NAME": "sb", "OPENSHELL_GATEWAY": "openshell"}
    assert SP.allow_hosts(["host:lab1", "skill:x"], env=env) is True
    (argv, gw), = calls
    assert argv[:4] == ["openshell", "policy", "update", "sb"] and "lab1:22" in argv and gw == "openshell"


@pytest.mark.asyncio
async def test_always_allow_is_for_the_role_itself_not_its_domain():
    """The grant names the calling role (the core's label), so it does not
    cover every role that shares the domain_id."""
    queue, core = _queue(), _Core()
    core._role_label = "devops_engineer"
    task = asyncio.create_task(dispatch_invocations([_inv("shell_exec", cmd="ls")], core, _role(),
                                                    oversight_queue=queue, operating_mode="AUTO"))
    item = await _answer(queue, ANSWER_ALWAYS)
    assert item.role_id == "devops_engineer"
    await task
    store = GrantStore.for_queue(queue)
    assert await store.covering("devops_engineer", "skill:shell_exec") is not None
    assert await store.covering(ROLE, "skill:shell_exec") is None


# ---------------------------------------------------------------------------
# Review fixes (2026-10-19)
# ---------------------------------------------------------------------------


@pytest.fixture
def policy_calls(monkeypatch):
    """Record every sandbox policy change instead of calling openshell."""
    calls = []
    monkeypatch.setattr(SP, "_APPLIED", set())
    monkeypatch.setattr(SP, "allow_hosts", lambda t, env=None: calls.append(("add", list(t))) or True)
    monkeypatch.setattr(SP, "remove_hosts", lambda t, env=None: calls.append(("remove", list(t))) or True)
    return calls


@pytest.mark.asyncio
async def test_allow_once_puts_the_host_in_the_sandbox_for_that_call_only(policy_calls):
    queue, core = _queue(), _Core()
    task = asyncio.create_task(dispatch_invocations(
        [_inv("ssh_exec", host="lab4", cmd="uptime")], core, _role(),
        oversight_queue=queue, operating_mode="AUTO"))
    await _answer(queue, ANSWER_ONCE)
    [out] = await task
    assert out.ok
    assert policy_calls == [("add", ["host:lab4"]), ("remove", ["host:lab4"])]


@pytest.mark.asyncio
async def test_a_plan_host_and_a_granted_host_reach_the_sandbox_once(policy_calls):
    queue, core = _queue(), _Core()
    await GrantStore.for_queue(queue).accept(role=ROLE, target="host:lab5", approved_by="op")
    for _ in range(2):
        [out] = await dispatch_invocations([_inv("ssh_exec", host="lab5", cmd="uptime")], core, _role(),
                                           oversight_queue=queue, operating_mode="AUTO")
        assert out.ok
    assert policy_calls == [("add", ["host:lab5"])]      # cached; never released


@pytest.mark.asyncio
async def test_ask_me_each_step_ignores_grants():
    queue, core = _queue(), _Core()
    store = GrantStore.for_queue(queue)
    await store.accept(role=ROLE, target="skill:shell_exec", approved_by="op")
    await store.accept(role=ROLE, target="host:lab6", approved_by="op")
    task = asyncio.create_task(dispatch_invocations([_inv("shell_exec", cmd="ls")], core, _role(),
                                                    oversight_queue=queue, operating_mode="ASK_PERMISSIONS"))
    item = await _answer(queue, ANSWER_ONCE)
    await task
    task = asyncio.create_task(dispatch_invocations([_inv("ssh_exec", host="lab6", cmd="uptime")], core,
                                                    _role(), oversight_queue=queue,
                                                    operating_mode="ASK_PERMISSIONS"))
    item = await _answer(queue, ANSWER_ONCE)
    assert "ssh to lab6" in item.question["text"]
    await task


@pytest.mark.asyncio
async def test_a_blocking_posture_finding_gets_no_host_grant(monkeypatch):
    import acc.capability_dispatch as cd
    monkeypatch.setattr(cd, "_posture_blocking", lambda role, core: [object()])
    queue, core = _queue(), _Core()
    await GrantStore.for_queue(queue).accept(role=ROLE, target="host:lab7", approved_by="op")
    task = asyncio.create_task(dispatch_invocations([_inv("ssh_exec", host="lab7", cmd="uptime")], core,
                                                    _role(), oversight_queue=queue, operating_mode="AUTO"))
    await _answer(queue, ANSWER_ALWAYS)      # asked although granted; "always" gives nothing new
    await task
    assert len([g for g in await GrantStore.for_queue(queue).all() if g.target == "host:lab7"]) == 1


@pytest.mark.asyncio
async def test_a_port_that_is_not_a_port_is_refused_not_a_crash():
    queue, core = _queue(), _Core()
    outs = await dispatch_invocations(
        [_inv("ssh_exec", host="lab8", port="ssh", cmd="uptime"), _inv("ssh_exec", host="localhost", cmd="id")],
        core, _role(), oversight_queue=None, operating_mode="AUTO")
    assert not outs[0].ok and "not a TCP port" in outs[0].error
    assert len(outs) == 2


@pytest.mark.asyncio
async def test_the_ask_once_mission_card_offers_always_allow(tmp_path):
    from acc import mission as M
    queue, core = _queue(), _Core()
    task = asyncio.create_task(dispatch_invocations(
        [_inv("shell_exec", cmd="ls")], core, _role(), oversight_queue=queue,
        operating_mode="ASK_ONCE", thread_ids=["t-9"], task_id="t-9", workspace=str(tmp_path)))
    item = await _answer(queue, M.ANSWER_ALWAYS)
    assert item.question["options"][3]["label"].startswith("always allow")
    [out] = await task
    assert out.ok
    assert await GrantStore.for_queue(queue).covering(ROLE, "skill:shell_exec") is not None
    assert await MissionStore.for_queue(queue).all() == []


def test_rm_double_dash_backs_up_a_file_whose_name_starts_with_a_dash(tmp_path):
    from acc import mission as M
    (tmp_path / "-draft.md").write_text("x")
    (tmp_path / "keep").write_text("y")
    targets = M.soft_delete_targets("skill", "shell_exec", {"argv": ["rm", "-f", "--", "-draft.md"]}, str(tmp_path))
    assert targets == [(tmp_path / "-draft.md").resolve()]


@pytest.mark.asyncio
async def test_a_sandboxed_rm_is_asked_not_soft_deleted(monkeypatch, tmp_path):
    import acc.capability_dispatch as cd
    monkeypatch.setattr(cd, "_sandbox_enabled", lambda: True)
    (tmp_path / "old.log").write_text("x")
    queue, core = _queue(), _Core()
    await MissionStore.for_queue(queue).create(["t-1"], principal="op", approver="op", workspace=str(tmp_path))
    task = asyncio.create_task(dispatch_invocations(
        [_inv("shell_exec", argv=["rm", "old.log"])], core, _role(), oversight_queue=queue,
        operating_mode="ASK_ONCE", thread_ids=["t-1"], task_id="t-1", workspace=str(tmp_path)))
    item = await _pending_one(queue)
    assert item.question["destructive"] is True
    await queue.approve(item.oversight_id, "tui:operator")
    await task
    assert not (tmp_path / ".acc-quarantine").exists()
