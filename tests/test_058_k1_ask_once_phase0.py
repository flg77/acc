"""058 K1 -- "Ask once, then get things done", Phase 0.

Three things, each measured before anything is loosened:

* **The ask counter.** Every task reports how often it interrupted a person:
  each call that reached the oversight queue, how it ended, and how long the
  agent waited.  Without the number "fewer asks" cannot be shown to anyone.
* **Proposals have no deadline** (operator 2026-10-15, 058 Q4: "drop").  A
  proposal row stays answerable until someone decides it -- with Redis the row
  used to vanish after twice the gate timeout and its payload after an hour, so
  a late approval silently did nothing.
* **The WebGUI offers only modes the agent knows.**
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

from acc.capability_dispatch import (
    ParsedInvocation,
    ask_stats,
    dispatch_invocations,
)
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
    def __init__(self, manifests: dict) -> None:
        self._skill_registry = _Registry(manifests)
        self._mcp_registry = None
        self.calls: list[str] = []

    async def invoke_skill(self, skill_id: str, args: dict, role) -> dict:
        self.calls.append(skill_id)
        return {"ok": True}


def _inv(target: str, **args) -> ParsedInvocation:
    return ParsedInvocation(kind="skill", target=target, args=args, raw=f"[SKILL: {target}]")


async def _answer_all(queue: HumanOversightQueue, n: int, *, approve: bool) -> None:
    """Answer the next *n* rows as they arrive, like an operator at the panel."""
    done = 0
    while done < n:
        await asyncio.sleep(0.02)
        for item in await queue.pending():
            if approve:
                await queue.approve(item.oversight_id, "op")
            else:
                await queue.reject(item.oversight_id, "op", reason="no")
            done += 1


class _FakeRedis:
    """Sync Redis with the TTL semantics the queue relies on (``ex=``, EXPIRE)."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set] = {}
        self.lists: dict[str, list] = {}
        self.ttl: dict[str, int] = {}

    def set(self, key, value, ex=None):
        self.kv[key] = value
        if ex is None:
            self.ttl.pop(key, None)
        else:
            self.ttl[key] = int(ex)

    def setex(self, key, ttl, value):
        self.set(key, value, ex=ttl)

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.ttl.pop(k, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def expire(self, key, ttl):
        self.ttl[key] = int(ttl)

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def lrem(self, key, count, value):
        self.lists[key] = [v for v in self.lists.get(key, []) if v != value]

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start:end + 1]

    def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:end + 1]


# ===========================================================================
# The ask counter
# ===========================================================================


@pytest.mark.asyncio
async def test_three_shell_calls_in_auto_are_three_asks():
    """The friction 058 is about: every system-access call is a question."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _Core({"shell_exec": _Manifest("HIGH", system_access=True)})
    role = RoleDefinitionConfig(allowed_skills=["shell_exec"], max_skill_risk_level="HIGH")

    answering = asyncio.create_task(_answer_all(queue, 3, approve=True))
    outcomes = await dispatch_invocations(
        [_inv("shell_exec", command=f"echo {i}") for i in range(3)], core, role,
        oversight_queue=queue, task_id="t-1",
    )
    await answering

    assert core.calls == ["shell_exec"] * 3
    assert all(o.asked and o.ask_status == "APPROVED" for o in outcomes)
    stats = ask_stats(outcomes)
    assert stats["asks"] == 3
    assert stats["approved"] == 3
    assert stats["rejected"] == stats["expired"] == stats["refused"] == 0
    assert stats["calls"] == 3
    assert stats["wait_s"] >= 0.0


@pytest.mark.asyncio
async def test_a_low_risk_call_is_not_an_ask():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _Core({"code_review": _Manifest("LOW")})
    role = RoleDefinitionConfig(allowed_skills=["code_review"])

    outcomes = await dispatch_invocations(
        [_inv("code_review")], core, role, oversight_queue=queue, task_id="t-2",
    )

    assert outcomes[0].ok and not outcomes[0].asked
    assert ask_stats(outcomes) == {
        "calls": 1, "asks": 0, "approved": 0, "rejected": 0, "expired": 0,
        "failed": 0, "refused": 0, "wait_s": 0.0, "by_mission": 0, "by_grant": 0,
    }


@pytest.mark.asyncio
async def test_a_rejected_ask_is_counted_as_rejected():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _Core({"shell_exec": _Manifest("HIGH", system_access=True)})
    role = RoleDefinitionConfig(allowed_skills=["shell_exec"], max_skill_risk_level="HIGH")

    answering = asyncio.create_task(_answer_all(queue, 1, approve=False))
    outcomes = await dispatch_invocations(
        [_inv("shell_exec", command="ls")], core, role, oversight_queue=queue, task_id="t-3",
    )
    await answering

    assert core.calls == []
    stats = ask_stats(outcomes)
    assert stats["asks"] == 1 and stats["rejected"] == 1 and stats["approved"] == 0


@pytest.mark.asyncio
async def test_an_unanswered_ask_is_counted_as_expired():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=0)
    core = _Core({"shell_exec": _Manifest("HIGH", system_access=True)})
    role = RoleDefinitionConfig(allowed_skills=["shell_exec"], max_skill_risk_level="HIGH")

    outcomes = await dispatch_invocations(
        [_inv("shell_exec", command="ls")], core, role, oversight_queue=queue, task_id="t-4",
    )

    stats = ask_stats(outcomes)
    assert stats["asks"] == 1 and stats["expired"] == 1


@pytest.mark.asyncio
async def test_a_refusal_above_the_ceiling_is_not_an_ask():
    """D-014: above the ceiling a call is refused, never asked -- counted apart."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _Core({"shell_exec": _Manifest("HIGH", system_access=True)})
    role = RoleDefinitionConfig(allowed_skills=["shell_exec"], max_skill_risk_level="HIGH")

    outcomes = await dispatch_invocations(
        [_inv("shell_exec", command="ls")], core, role, oversight_queue=queue,
        task_id="t-5", requester_ceiling="LOW",
    )

    stats = ask_stats(outcomes)
    assert stats["asks"] == 0 and stats["refused"] == 1
    assert await queue.pending() == []


def test_no_outcomes_is_an_empty_count():
    assert ask_stats([])["calls"] == 0


# ===========================================================================
# Proposals have no deadline (058 Q4)
# ===========================================================================


@pytest.mark.asyncio
async def test_a_proposal_row_has_no_deadline():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=300)
    oid = await queue.submit("p-1", "HIGH", "PROPOSE_INFUSE coding_agent", "assistant",
                             deadline=False)
    item = await queue._load(oid)
    assert item.timeout_ms == 0


@pytest.mark.asyncio
async def test_expire_timed_out_leaves_a_proposal_alone():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=0)
    proposal = await queue.submit("p-1", "HIGH", "PROPOSE_INFUSE", "assistant", deadline=False)
    gate = await queue.submit("t-1", "HIGH", "shell_exec", "coding_agent")
    time.sleep(0.01)

    expired = await queue.expire_timed_out()

    assert expired == [gate]
    assert [i.oversight_id for i in await queue.pending()] == [proposal]


@pytest.mark.asyncio
async def test_with_redis_a_proposal_row_does_not_expire_but_a_gate_row_does():
    redis = _FakeRedis()
    queue = HumanOversightQueue(redis_client=redis, collective_id="t", timeout_s=300)
    proposal = await queue.submit("p-1", "HIGH", "PROPOSE_INFUSE", "assistant", deadline=False)
    gate = await queue.submit("t-1", "HIGH", "shell_exec", "coding_agent")

    assert f"acc:t:oversight:{proposal}" not in redis.ttl, "a proposal row must not expire"
    assert redis.ttl[f"acc:t:oversight:{gate}"] == 600
    # The open set that lists proposals carries no TTL either, so a proposal is
    # still listed after a burst of gates has come and gone.
    assert all(k not in redis.ttl for k in redis.sets if k.endswith(":open"))
    assert {i.oversight_id for i in await queue.pending()} == {proposal, gate}


@pytest.mark.asyncio
async def test_with_redis_a_decided_proposal_leaves_the_open_set():
    redis = _FakeRedis()
    queue = HumanOversightQueue(redis_client=redis, collective_id="t", timeout_s=300)
    proposal = await queue.submit("p-1", "HIGH", "PROPOSE_INFUSE", "assistant", deadline=False)

    await queue.approve(proposal, "op")

    assert await queue.pending() == []
    assert f"acc:t:oversight:{proposal}" in redis.ttl, "a decided row is history and ages out"


def test_the_proposal_payload_cache_lives_as_long_as_the_row():
    """Approving a proposal an hour later must still dispatch it."""
    from types import SimpleNamespace

    from acc.agent import cache_proposal_payload

    redis = _FakeRedis()
    p = SimpleNamespace(kind="INFUSE", proposal_id="p-1", summary="infuse",
                        to_payload=lambda: {"kind": "INFUSE"})
    cache_proposal_payload(redis, "t", "ov-1", p)

    assert set(redis.kv) == {"acc:t:assistant_proposal:ov-1", "acc:t:assistant_proposal_meta:ov-1"}
    assert redis.ttl == {}, "neither key may expire while the row is open"


# ===========================================================================
# The WebGUI's mode list
# ===========================================================================


def test_the_webgui_offers_exactly_the_modes_the_agent_knows():
    from acc.operating_modes import ALL_MODES

    src = (Path(__file__).resolve().parents[1] / "webgui/src/pages/work/Prompt.tsx").read_text(
        encoding="utf-8",
    )
    match = re.search(r"const MODES = \[([^\]]*)\]", src)
    assert match, "Prompt.tsx no longer declares MODES"
    offered = re.findall(r'"([A-Z_]+)"', match.group(1))
    assert sorted(offered) == sorted(ALL_MODES)


# ===========================================================================
# The golden set measures asks
# ===========================================================================


def test_the_get_things_done_set_loads():
    from acc.golden_prompts import load_all

    names = {p.name for p in load_all()}
    assert {
        "getdone_fix_failing_test", "getdone_cli_flag", "getdone_ansible_role",
        "getdone_research_summary", "getdone_cross_role",
    } <= names


def test_asks_max_fails_a_task_that_asked_too_often():
    from acc.golden_prompts import ExpectsBlock, GoldenPrompt, evaluate

    prompt = GoldenPrompt(name="g", prompt="p", target_role="coding_agent",
                          expects=ExpectsBlock(asks_max=1))
    reply = {"output": "done", "asks": {"asks": 3, "approved": 3}}

    result = evaluate(prompt, reply, 10)

    assert not result.passed
    assert "asks: 3 exceed the budget of 1" in result.failures
    assert result.asks == {"asks": 3, "approved": 3}


def test_a_task_with_no_tool_asked_nobody_and_passes_asks_max():
    from acc.golden_prompts import ExpectsBlock, GoldenPrompt, evaluate

    prompt = GoldenPrompt(name="g", prompt="p", target_role="analyst",
                          expects=ExpectsBlock(asks_max=0))
    assert evaluate(prompt, {"output": "an answer"}, 10).passed


def test_the_reply_carries_asks_from_task_complete():
    from acc.channels.tui import _payload_to_response

    resp = _payload_to_response("t", {"output": "x", "asks": {"asks": 2}})
    assert resp.asks == {"asks": 2}
    assert _payload_to_response("t", {"output": "x"}).asks is None


@pytest.mark.asyncio
async def test_a_golden_run_sends_the_prompts_operating_mode(monkeypatch):
    """The YAML's mode was never sent: every run measured AUTO."""
    from acc.channels import tui as tui_mod
    from acc.channels.base import PromptResponse
    from acc.golden_prompts import GoldenPrompt, run_one

    sent: dict = {}

    class _Channel:
        def __init__(self, *a, **kw):
            pass

        async def send(self, **kw):
            sent.update(kw)
            return "task-1"

        async def receive(self, task_id, timeout):
            return PromptResponse(task_id=task_id, output="ok", asks={"asks": 1})

        async def close(self):
            pass

    monkeypatch.setattr(tui_mod, "TUIPromptChannel", _Channel)
    prompt = GoldenPrompt(name="g", prompt="p", target_role="coding_agent",
                          operating_mode="ASK_PERMISSIONS")

    result = await run_one(prompt, observer=None, collective_id="t")

    assert sent["operating_mode"] == "ASK_PERMISSIONS"
    assert result.asks == {"asks": 1}
