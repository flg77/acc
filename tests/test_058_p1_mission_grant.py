"""058 Phase 1 -- "ask once": the mission grant and the soft delete.

Under ASK_ONCE the first system-access call of a thread asks one question --
the mission card.  Allowed, the thread's later in-scope calls run without
asking (an AUTO_APPROVED row names the mission as the approver).  The ceiling,
the role's own grants, CRITICAL calls, acting on the operator's behalf and
every destructive call that cannot be backed up are still exactly as before.
A workspace ``rm`` under a mission is backed up first (058 Q1); closing the
mission reports it, and purge is the operator's final allowance.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from acc import mission as M
from acc.capability_dispatch import ParsedInvocation, ask_stats, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.oversight import HumanOversightQueue


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Manifest:
    def __init__(self, risk_level: str, **flags) -> None:
        self.risk_level = risk_level
        self.purpose = "stub"
        for k, v in flags.items():
            setattr(self, k, v)


class _Registry:
    def __init__(self, manifests: dict) -> None:
        self._m = manifests

    def manifest(self, skill_id: str):
        return self._m.get(skill_id)


class _Core:
    """Runs ``rm`` for real (so the soft delete is observable); everything
    else is recorded."""

    def __init__(self, manifests: dict, guard=None) -> None:
        self._skill_registry = _Registry(manifests)
        self._mcp_registry = None
        self._capability_guard = guard
        self.calls: list[tuple[str, dict]] = []

    async def invoke_skill(self, skill_id: str, args: dict, role) -> dict:
        self.calls.append((skill_id, dict(args)))
        argv = args.get("argv") or []
        if skill_id == "shell_exec" and argv[:1] == ["rm"]:
            import shutil
            from pathlib import Path
            cwd = Path(args.get("cwd") or ".")
            for a in argv[1:]:
                if a.startswith("-"):
                    continue
                p = cwd / a
                if p.is_dir():
                    shutil.rmtree(p)
                elif p.exists():
                    p.unlink()
        return {"returncode": 0}


_MANIFESTS = {
    "shell_exec": _Manifest("HIGH", system_access=True),
    "fs_write": _Manifest("HIGH", system_access=True),
    "telegram_send": _Manifest("MEDIUM", acts_on_behalf=True),
    "nuke": _Manifest("CRITICAL", system_access=True),
}
_ROLE = RoleDefinitionConfig(
    domain_id="coding_agent",
    allowed_skills=list(_MANIFESTS), max_skill_risk_level="CRITICAL",
)


def _inv(target: str, **args) -> ParsedInvocation:
    return ParsedInvocation(kind="skill", target=target, args=args, raw=f"[SKILL: {target}]")


async def _answer(queue: HumanOversightQueue, answers: list[str], log: list) -> None:
    """Answer rows as they arrive with the next answer key ("" = plain approve,
    "reject" = reject); records each row's question."""
    i = 0
    while i < len(answers):
        await asyncio.sleep(0.01)
        for item in await queue.pending():
            key = answers[i]
            log.append(item.question)
            if key == "reject":
                await queue.reject(item.oversight_id, "op", reason="no")
            else:
                await queue.approve(item.oversight_id, "op", answer=key)
            i += 1
            if i >= len(answers):
                break


async def _run(invs, *, queue, core=None, mode="ASK_ONCE", threads=("s-1", "t-1"),
               workspace="", answers=(), ceiling="", role=_ROLE):
    core = core or _Core(_MANIFESTS)
    log: list = []
    answering = asyncio.create_task(_answer(queue, list(answers), log))
    try:
        outcomes = await asyncio.wait_for(dispatch_invocations(
            invs, core, role, oversight_queue=queue, task_id=threads[-1],
            operating_mode=mode, thread_ids=list(threads), workspace=workspace,
            requester_ceiling=ceiling,
        ), timeout=10)
    finally:
        answering.cancel()
    return outcomes, core, log


def _queue(timeout_s: int = 5) -> HumanOversightQueue:
    return HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=timeout_s)


# ===========================================================================
# The mission card
# ===========================================================================


@pytest.mark.asyncio
async def test_ask_once_asks_one_question_for_three_shell_calls(tmp_path):
    queue = _queue()
    outcomes, core, log = await _run(
        [_inv("shell_exec", argv=["pytest", "-q"], cwd=str(tmp_path)) for _ in range(3)],
        queue=queue, workspace=str(tmp_path), answers=[M.ANSWER_MISSION],
    )

    assert [c[0] for c in core.calls] == ["shell_exec"] * 3
    assert len(log) == 1 and log[0]["text"].startswith("Ask once")
    stats = ask_stats(outcomes)
    assert stats["asks"] == 1 and stats["by_mission"] == 2
    missions = await M.MissionStore.for_queue(queue).all()
    assert len(missions) == 1
    m = missions[0]
    assert m.used_calls == 3 and m.roles_used == ["coding_agent"] and m.approver == "op"
    autos = [i for i in await queue.recent_decisions() if i.status == "AUTO_APPROVED"]
    assert len(autos) == 2 and all(i.approver_id == f"policy:mission:{m.mission_id}" for i in autos)


@pytest.mark.asyncio
async def test_just_this_call_creates_no_mission(tmp_path):
    queue = _queue()
    outcomes, _, log = await _run(
        [_inv("shell_exec", argv=["ls"], cwd=str(tmp_path)) for _ in range(2)],
        queue=queue, workspace=str(tmp_path), answers=[M.ANSWER_ONCE, M.ANSWER_ONCE],
    )
    assert ask_stats(outcomes)["asks"] == 2 and len(log) == 2
    assert await M.MissionStore.for_queue(queue).all() == []


@pytest.mark.asyncio
async def test_a_follow_up_in_the_same_session_is_covered(tmp_path):
    queue = _queue()
    await _run([_inv("shell_exec", argv=["ls"], cwd=str(tmp_path))], queue=queue,
               workspace=str(tmp_path), answers=[M.ANSWER_MISSION], threads=("s-1", "t-1"))
    # A follow-up prompt: same session, new task.  A hand-off: same task id.
    follow, _, log1 = await _run([_inv("shell_exec", argv=["ls"], cwd=str(tmp_path))],
                                 queue=queue, workspace=str(tmp_path), threads=("s-1", "t-2"))
    handoff, _, log2 = await _run([_inv("shell_exec", argv=["ls"], cwd=str(tmp_path))],
                                  queue=queue, workspace=str(tmp_path), threads=("t-1",),
                                  mode="AUTO")
    assert ask_stats(follow)["by_mission"] == 1 and log1 == []
    assert ask_stats(handoff)["by_mission"] == 1 and log2 == []


@pytest.mark.asyncio
async def test_another_thread_is_not_covered(tmp_path):
    queue = _queue()
    await _run([_inv("shell_exec", argv=["ls"])], queue=queue, workspace=str(tmp_path),
               answers=[M.ANSWER_MISSION], threads=("s-1",))
    outcomes, _, log = await _run([_inv("shell_exec", argv=["ls"])], queue=queue,
                                  workspace=str(tmp_path), answers=[M.ANSWER_ONCE],
                                  threads=("s-2",))
    assert ask_stats(outcomes)["asks"] == 1 and log[0]["text"].startswith("Ask once")


# ===========================================================================
# What a mission does not change
# ===========================================================================


async def _with_mission(queue, tmp_path):
    await _run([_inv("shell_exec", argv=["ls"])], queue=queue, workspace=str(tmp_path),
               answers=[M.ANSWER_MISSION])


@pytest.mark.asyncio
async def test_acting_on_your_behalf_is_still_asked(tmp_path):
    queue = _queue()
    await _with_mission(queue, tmp_path)
    outcomes, _, log = await _run([_inv("telegram_send", text="hi")], queue=queue,
                                  workspace=str(tmp_path), answers=[""])
    assert ask_stats(outcomes)["asks"] == 1 and not log[0]


@pytest.mark.asyncio
async def test_a_critical_call_is_still_asked(tmp_path):
    queue = _queue()
    await _with_mission(queue, tmp_path)
    outcomes, _, _ = await _run([_inv("nuke")], queue=queue, workspace=str(tmp_path), answers=[""])
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_the_ceiling_still_refuses(tmp_path):
    queue = _queue()
    await _with_mission(queue, tmp_path)
    outcomes, core, _ = await _run([_inv("shell_exec", argv=["ls"])], queue=queue,
                                   workspace=str(tmp_path), ceiling="LOW")
    assert core.calls == [] and ask_stats(outcomes)["refused"] == 1


@pytest.mark.asyncio
async def test_a_mission_never_widens_a_role_grant(tmp_path):
    """A call outside the role's grants is an escalation question, as before."""
    class _Guard:
        def check_skill_invocation(self, role, manifest):
            return SimpleNamespace(allowed=False, reason="'shell_exec' not in role.allowed_skills")

    queue = _queue()
    await _with_mission(queue, tmp_path)
    outcomes, _, _ = await _run([_inv("shell_exec", argv=["ls"])], queue=queue,
                                workspace=str(tmp_path), core=_Core(_MANIFESTS, guard=_Guard()),
                                answers=[""])
    assert ask_stats(outcomes)["asks"] == 1 and ask_stats(outcomes)["by_mission"] == 0


@pytest.mark.asyncio
async def test_a_cwd_outside_the_workspace_is_asked(tmp_path):
    queue = _queue()
    ws = tmp_path / "ws"
    ws.mkdir()
    await _with_mission(queue, ws)
    outcomes, _, _ = await _run([_inv("shell_exec", argv=["ls"], cwd=str(tmp_path))],
                                queue=queue, workspace=str(ws), answers=[""])
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_ask_me_each_step_ignores_the_mission(tmp_path):
    queue = _queue()
    await _with_mission(queue, tmp_path)
    outcomes, _, _ = await _run([_inv("shell_exec", argv=["ls"])], queue=queue,
                                workspace=str(tmp_path), mode="ASK_PERMISSIONS", answers=[""])
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_an_exhausted_mission_asks_again(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_MISSION_MAX_CALLS", "1")
    queue = _queue()
    await _with_mission(queue, tmp_path)
    outcomes, _, log = await _run([_inv("shell_exec", argv=["ls"])], queue=queue,
                                  workspace=str(tmp_path), answers=[M.ANSWER_ONCE])
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_a_revoked_mission_asks_again(tmp_path):
    queue = _queue()
    await _with_mission(queue, tmp_path)
    store = M.MissionStore.for_queue(queue)
    m = (await store.all())[0]
    await store.end(m.mission_id, status=M.STATUS_REVOKED, by="op")
    outcomes, _, _ = await _run([_inv("shell_exec", argv=["ls"])], queue=queue,
                                workspace=str(tmp_path), answers=[M.ANSWER_ONCE])
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
async def test_plain_auto_raises_no_mission_card(tmp_path):
    queue = _queue()
    _, _, log = await _run([_inv("shell_exec", argv=["ls"])], queue=queue,
                           workspace=str(tmp_path), mode="AUTO", answers=[""])
    # AUTO asks per call, never the mission card.  Since 060 that per-call
    # question offers "always allow" (a grant given once).
    (q,) = log
    labels = [o["label"] for o in q["options"]]
    assert "allow the mission" not in labels
    assert labels[0] == "allow once" and labels[1].startswith("always allow")


# ===========================================================================
# Soft delete (058 Q1)
# ===========================================================================


@pytest.mark.asyncio
async def test_a_workspace_rm_is_backed_up_then_runs_without_asking(tmp_path):
    queue = _queue()
    (tmp_path / "old.txt").write_text("keep me")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "a.o").write_text("obj")
    await _with_mission(queue, tmp_path)

    outcomes, core, log = await _run(
        [_inv("shell_exec", argv=["rm", "-rf", "old.txt", "build"], cwd=str(tmp_path))],
        queue=queue, workspace=str(tmp_path),
    )

    assert log == [] and outcomes[0].ok
    assert not (tmp_path / "old.txt").exists() and not (tmp_path / "build").exists()
    m = (await M.MissionStore.for_queue(queue).all())[0]
    assert {e["path"] for e in m.quarantine} == {"old.txt", "build"}
    kept = {e["path"]: tmp_path / e["kept_at"] for e in m.quarantine}
    assert kept["old.txt"].read_text() == "keep me"
    assert (kept["build"] / "a.o").exists()
    assert any("backed up" in line for line in M.report(m))


@pytest.mark.asyncio
async def test_restore_and_purge_are_the_final_allowance(tmp_path):
    queue = _queue()
    (tmp_path / "old.txt").write_text("keep me")
    await _with_mission(queue, tmp_path)
    await _run([_inv("shell_exec", argv=["rm", "old.txt"], cwd=str(tmp_path))],
               queue=queue, workspace=str(tmp_path))
    m = (await M.MissionStore.for_queue(queue).all())[0]

    assert M.restore(m) == ["old.txt"]
    assert (tmp_path / "old.txt").read_text() == "keep me"
    assert M.purge(m) == 1
    assert not (tmp_path / M.QUARANTINE_DIR / m.mission_id).exists()


@pytest.mark.asyncio
async def test_a_destructive_call_that_cannot_be_backed_up_is_still_asked(tmp_path):
    queue = _queue()
    await _with_mission(queue, tmp_path)
    outcomes, _, log = await _run(
        [_inv("shell_exec", argv=["git", "reset", "--hard"], cwd=str(tmp_path))],
        queue=queue, workspace=str(tmp_path), answers=["1"],
    )
    assert ask_stats(outcomes)["asks"] == 1 and log[0]["destructive"] is True


@pytest.mark.asyncio
async def test_an_rm_outside_the_workspace_is_still_asked(tmp_path):
    queue = _queue()
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "outside.txt").write_text("x")
    await _with_mission(queue, ws)
    outcomes, _, log = await _run(
        [_inv("shell_exec", argv=["rm", str(tmp_path / "outside.txt")], cwd=str(ws))],
        queue=queue, workspace=str(ws), answers=["2"],
    )
    assert ask_stats(outcomes)["asks"] == 1 and (tmp_path / "outside.txt").exists()


def test_the_quarantine_itself_cannot_be_soft_deleted(tmp_path):
    (tmp_path / M.QUARANTINE_DIR).mkdir()
    assert M.soft_delete_targets("skill", "shell_exec",
                                 {"argv": ["rm", "-rf", M.QUARANTINE_DIR], "cwd": str(tmp_path)},
                                 str(tmp_path)) is None


def test_the_workspace_root_cannot_be_soft_deleted(tmp_path):
    assert M.soft_delete_targets("skill", "shell_exec",
                                 {"argv": ["rm", "-rf", "."], "cwd": str(tmp_path)},
                                 str(tmp_path)) is None


# ===========================================================================
# Modes, surfaces and the bus
# ===========================================================================


def test_ask_once_is_the_default_and_has_a_label():
    from acc.operating_modes import ALL_MODES, DEFAULT_SURFACE_MODE, label

    assert "ASK_ONCE" in ALL_MODES and DEFAULT_SURFACE_MODE == "ASK_ONCE"
    assert RoleDefinitionConfig().default_operating_mode == "ASK_ONCE"
    assert label("ASK_ONCE") == "Ask once" and label("ASK_PERMISSIONS") == "Ask me each step"


def test_ask_once_executes_routes_like_auto():
    from acc.assistant_proposal import DISPATCH_EXECUTE, PROPOSAL_ROUTE, decide_dispatch

    assert decide_dispatch("ASK_ONCE", PROPOSAL_ROUTE) == DISPATCH_EXECUTE


def test_slash_mission_parses():
    from acc import slash_commands as sc

    assert sc.parse("/mission").args == {"action": "list", "mission_id": ""}
    assert sc.parse("/mission purge m-1").args == {"action": "purge", "mission_id": "m-1"}
    assert sc.parse("/mission purge").kind == sc.KIND_INVALID
    assert sc.parse("/mode ASK_ONCE").args == {"mode": "ASK_ONCE"}


def test_mission_control_rides_on_the_oversight_subjects():
    from acc.signals import subject_mission_control

    subject = subject_mission_control("sol-01")
    assert subject.startswith("acc.sol-01.oversight.") and subject.count(".") == 3


def test_the_tui_snapshot_takes_missions_from_the_arbiter():
    from acc.tui.client import NATSObserver

    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    mission = {"mission_id": "m-1", "status": "ACTIVE"}
    obs._route_heartbeat("coding-1", {"role": "coding_agent", "missions": [mission]})
    assert obs._snapshot.missions == []
    obs._route_heartbeat("arbiter-1", {"role": "arbiter", "missions": [mission]})
    assert obs._snapshot.missions == [mission]


@pytest.mark.asyncio
async def test_mission_control_closes_and_purges(tmp_path):
    from acc.agent import Agent

    queue = _queue()
    (tmp_path / "old.txt").write_text("x")
    await _with_mission(queue, tmp_path)
    await _run([_inv("shell_exec", argv=["rm", "old.txt"], cwd=str(tmp_path))],
               queue=queue, workspace=str(tmp_path))
    store = M.MissionStore.for_queue(queue)
    m = (await store.all())[0]
    agent = SimpleNamespace(_oversight_queue=queue)
    handle = Agent._handle_mission_control.__get__(agent)

    await handle({"action": "purge", "mission_id": m.mission_id})
    assert (await store.get(m.mission_id)).quarantine, "an active mission is never purged"

    await handle({"action": "close", "mission_id": m.mission_id, "by": "op"})
    await handle({"action": "purge", "mission_id": m.mission_id, "by": "op"})
    m = await store.get(m.mission_id)
    assert m.status == M.STATUS_CLOSED and m.closed_by == "op" and m.quarantine == []
    assert not (tmp_path / M.QUARANTINE_DIR / m.mission_id).exists()


@pytest.mark.asyncio
async def test_missions_persist_in_redis_without_a_ttl(tmp_path):
    from tests.test_058_k1_ask_once_phase0 import _FakeRedis

    redis = _FakeRedis()
    store = M.MissionStore(redis, "t")
    m = await store.create(["s-1", "t-1"], principal="alice", approver="op", workspace=str(tmp_path))
    assert redis.ttl == {}
    assert (await store.for_threads(["t-1"])).mission_id == m.mission_id
    assert json.loads(redis.kv[f"acc:t:mission:{m.mission_id}"])["principal"] == "alice"
