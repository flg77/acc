"""`20260923-lessons-that-travel` Phase 6 — the inbox: envelope, steer vs
follow-up, receipts, the prompt block, and the NKey matrix."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acc.agent import Agent
from acc.agent_messages import STEERING_HEADING, AgentMessage, SteerRing, parse_message, steering_parts
from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.context_budget import KIND_NOTES, KIND_STEER, pack, standard_blocks


class _LLM:
    def __init__(self) -> None:
        self.users: list[str] = []

    async def complete(self, system, user, response_schema=None):
        self.users.append(user)
        return {"content": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}

    async def embed(self, text):
        return [0.0] * 384


class _Vec:
    def insert(self, *_a, **_k):
        pass

    def search(self, *_a, **_k):
        return []


class _Redis:
    def __init__(self) -> None:
        self.kv: dict = {}
        self.lists: dict = {}

    def set(self, k, v):
        self.kv[k] = v

    def get(self, k):
        return self.kv.get(k)

    def expire(self, k, ttl):
        pass

    def lpush(self, k, *vals):
        self.lists.setdefault(k, [])[0:0] = list(vals)

    def ltrim(self, k, a, b):
        pass


def _msg(**kw) -> AgentMessage:
    base = dict(collective_id="c", from_agent="acc-cli", to_agent="b1", body="also check the migration")
    base.update(kw)
    return AgentMessage(**base)


def _core() -> tuple[CognitiveCore, _LLM]:
    llm = _LLM()
    return CognitiveCore(agent_id="b1", collective_id="c", llm=llm, vector=_Vec(), redis_client=None,
                         role_label="reviewer"), llm


def _stub(*, core, redis=None, in_flight=0, handler=None):
    return SimpleNamespace(
        agent_id="b1", _cognitive_core=core, _redis=redis, _tasks_in_flight=in_flight,
        _local_task_handler=handler,
        config=SimpleNamespace(agent=SimpleNamespace(collective_id="c", role="reviewer")),
        _messages_journal_id=lambda: "messages-b1",
        _write_receipt=lambda cid, m, status, **extra: Agent._write_receipt(_holder[0], cid, m, status, **extra),
    )


_holder: list = []


def _stub_with_receipts(**kw):
    s = _stub(**kw)
    _holder[:] = [s]
    return s


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["x", {"to_agent": "b1"}, {"to_agent": "b1", "body": ""},
                                 {"to_agent": "b1", "body": "x", "delivery": "shout"}, {"body": "x"}])
def test_bad_envelopes_are_dropped(bad):
    assert parse_message(bad) is None


def test_follow_up_task_runs_as_the_sender_not_the_receiver():
    m = _msg(task_id="t-9", attribution={"requested_by": "cli:flg", "requester_tier": "operator",
                                         "requester_ceiling": "CRITICAL", "ignored": "x"})
    task = m.follow_up_task(target_role="reviewer")
    assert task["signal_type"] == "TASK_ASSIGN" and task["target_agent_id"] == "b1"
    assert task["content"] == "also check the migration" and task["parent_task_id"] == "t-9"
    assert task["requested_by"] == "cli:flg" and task["requester_ceiling"] == "CRITICAL"
    assert "ignored" not in task and task["task_id"].startswith("msg-")


# ---------------------------------------------------------------------------
# The block — right before the task, evicted last
# ---------------------------------------------------------------------------


def test_steering_renders_before_the_task_and_outlives_the_notes():
    heading, items = steering_parts([_msg()])
    assert heading == STEERING_HEADING
    blocks = standard_blocks(task="do x", notes_heading="MEMORY_NOTES:", notes_items=("- mine",),
                             steer_heading=heading, steer_items=tuple(items))
    full = pack(blocks, 10_000)
    assert full.text.index("MEMORY_NOTES:") < full.text.index(STEERING_HEADING) < full.text.index("do x")
    need = pack(standard_blocks(task="do x", steer_heading=heading, steer_items=tuple(items)), 10_000).est_tokens
    tight = pack(blocks, need + 3)
    assert tight.dropped.get(KIND_NOTES, 0) >= 1 and tight.dropped.get(KIND_STEER, 0) == 0
    assert "also check the migration" in tight.text


def test_no_steer_means_byte_identical():
    a = standard_blocks(task="t", notes_heading="N:", notes_items=("- n",))
    b = standard_blocks(task="t", notes_heading="N:", notes_items=("- n",), steer_heading="", steer_items=())
    assert pack(a, 5000).text == pack(b, 5000).text


@pytest.mark.asyncio
async def test_core_renders_a_steer_once_and_reports_it():
    core, llm = _core()
    ring = SteerRing()
    assert ring.offer(_msg(message_id="M1")) and not ring.offer(_msg(message_id="M1"))
    assert core.receive_steer(_msg(message_id="M2"))
    r = await core.process_task({"signal_type": "TASK_ASSIGN", "task_id": "t", "content": "review"},
                                RoleDefinitionConfig(purpose="review"))
    assert r.steer_used == ["M2"] and STEERING_HEADING in llm.users[-1]
    assert llm.users[-1].index(STEERING_HEADING) < llm.users[-1].index("review")
    r2 = await core.process_task({"signal_type": "TASK_ASSIGN", "task_id": "t2", "content": "again"},
                                 RoleDefinitionConfig(purpose="review"))
    assert r2.steer_used == [] and STEERING_HEADING not in llm.users[-1]


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_busy_agent_is_steered_and_the_receipt_says_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    core, _ = _core()
    redis = _Redis()
    stub = _stub_with_receipts(core=core, redis=redis, in_flight=1)
    status = await Agent._deliver_message(stub, _msg(message_id="S1", delivery="auto").model_dump())
    assert status == "delivered"
    assert [m.message_id for m in core.pending_steer()] == ["S1"]
    receipt = json.loads(redis.kv["acc:c:message:S1"])
    assert receipt["status"] == "delivered" and receipt["resolved_delivery"] == "steer"
    assert redis.lists["acc:c:agent:b1:messages"] == ["S1"]
    journal = json.loads((tmp_path / "messages-b1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert journal["kind"] == "agent_message" and journal["status"] == "delivered"


@pytest.mark.asyncio
async def test_an_idle_agent_gets_a_follow_up_task_through_its_own_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    core, _ = _core()
    handler = AsyncMock()
    redis = _Redis()
    stub = _stub_with_receipts(core=core, redis=redis, in_flight=0, handler=handler)
    body = _msg(message_id="F1", delivery="steer", attribution={"requested_by": "cli:flg"}).model_dump()
    status = await Agent._deliver_message(stub, body)
    assert status == "follow_up", "a steer with nothing in flight is delivered as a follow-up"
    handler.assert_awaited_once()
    task = json.loads(handler.await_args.args[0])
    assert task["target_agent_id"] == "b1" and task["requested_by"] == "cli:flg" and task["message_id"] == "F1"
    receipt = json.loads(redis.kv["acc:c:message:F1"])
    assert receipt["status"] == "follow_up" and receipt["task_ref"] == task["task_id"]
    assert core.pending_steer() == []


@pytest.mark.asyncio
async def test_wrong_address_and_missing_core_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    core, _ = _core()
    stub = _stub_with_receipts(core=core)
    assert await Agent._deliver_message(stub, _msg(to_agent="someone-else").model_dump()) == "dropped:not-addressed"
    assert await Agent._deliver_message(stub, "garbage") == "dropped:invalid"
    stub2 = _stub_with_receipts(core=None, redis=_Redis())
    assert await Agent._deliver_message(stub2, _msg(message_id="D1").model_dump()) == "dropped:no-core"
    assert json.loads(stub2._redis.kv["acc:c:message:D1"])["reason"] == "no-core"


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def test_only_the_arbiter_and_the_operator_may_publish_to_an_inbox():
    from acc import nats_permissions
    roles = nats_permissions.load_permission_matrix()
    subject = "acc.sol-01.agent.analyst-1.inbox"

    def may(role, verb):
        return any(nats_permissions.subject_matches(g, subject) for g in roles[role][verb])

    assert may("arbiter", "publish") and may("tui", "publish")
    for worker in ("analyst", "coding_agent", "ingester", "synthesizer", "observer"):
        assert not may(worker, "publish"), worker
        assert may(worker, "subscribe"), worker


# ---------------------------------------------------------------------------
# The CLI writes "sent" BEFORE it publishes, so the receiver's receipt wins
# ---------------------------------------------------------------------------


def test_msg_send_records_sent_before_it_publishes(monkeypatch):
    from acc.cli import msg_cmd

    order: list[str] = []
    redis = _Redis()
    redis.set = lambda k, v: order.append("redis:" + json.loads(v)["status"])  # type: ignore[assignment]
    monkeypatch.setattr(msg_cmd, "_redis", lambda: redis)

    async def _connect():
        class _NC:
            async def publish(self, subject, payload):
                order.append("publish:" + subject)

            async def flush(self, timeout=0):
                pass

            async def close(self):
                pass
        return _NC()

    monkeypatch.setattr(msg_cmd, "connect_nats", _connect)
    args = SimpleNamespace(agent_id="b1", text="hello", delivery="auto", task="", collective="c", json=True)
    assert msg_cmd._cmd_send(args) == 0
    assert order == ["redis:sent", "publish:acc.c.agent.b1.inbox"]
