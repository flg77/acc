"""`20260923-lessons-that-travel` Phase 1 — the Lesson envelope, the peer ring,
the prompt block, the agent's publish / accept paths, and the S2 relay.

Unit-scope: no NATS, no LLM, no Redis.  The relay test (last section) is the
acceptance test named in the gap item: what one agent's reflection distilled
is rendered into a *different* agent's next prompt, under the same scope and
ceiling rules the notes use, exactly once.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.agent import Agent
from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.context_budget import KIND_NOTES, KIND_PEER_LESSONS, pack, standard_blocks
from acc.lessons import (
    PEER_LESSONS_HEADING,
    Lesson,
    PeerLessonRing,
    lesson_from_note,
    parse_lesson,
    peer_lessons_parts,
)
from acc.memory_reflection import MemoryNote


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _CapturingLLM:
    """Records the user message of every completion."""

    def __init__(self) -> None:
        self.users: list[str] = []

    async def complete(self, system, user, response_schema=None):
        self.users.append(user)
        return {"content": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}

    async def embed(self, text):
        return [0.0] * 384


class _StubVector:
    def insert(self, *_a, **_k):
        pass

    def search(self, *_a, **_k):
        return []


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict = {}
        self.z: dict = {}
        self.sets: dict = {}

    def set(self, k, v):
        self.kv[k] = v

    def get(self, k):
        return self.kv.get(k)

    def expire(self, k, ttl):
        pass

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)

    def sadd(self, k, *members):
        self.sets.setdefault(k, set()).update(members)


def _lesson(**kw) -> Lesson:
    base = dict(collective_id="c", from_agent="a1", role_label="analyst", summary="Big PDFs exhaust the ingester.")
    base.update(kw)
    return Lesson(**base)


def _payload(content="what did the analyst learn?") -> dict:
    return {"signal_type": "TASK_ASSIGN", "task_id": "t-1", "content": content}


def _core(agent_id="b1", role_label="reviewer") -> tuple[CognitiveCore, _CapturingLLM]:
    llm = _CapturingLLM()
    core = CognitiveCore(agent_id=agent_id, collective_id="c", llm=llm, vector=_StubVector(),
                         redis_client=None, role_label=role_label)
    return core, llm


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


def test_envelope_round_trips_and_ignores_unknown_fields():
    body = _lesson().model_dump()
    body["future_field"] = {"x": 1}
    back = parse_lesson(body)
    assert back is not None
    assert back.summary == "Big PDFs exhaust the ingester."
    assert back.signal_type == "KNOWLEDGE_SHARE"
    assert not hasattr(back, "future_field")


@pytest.mark.parametrize("bad", [
    "not a dict",
    {"from_agent": "a1"},                                   # no summary
    {"from_agent": "a1", "summary": ""},                    # empty summary
    {"from_agent": "a1", "summary": "x", "kind": "spell"},  # unknown kind
    {"summary": "x"},                                       # no sender
])
def test_malformed_envelopes_are_dropped_not_guessed(bad):
    assert parse_lesson(bad) is None


def test_a_note_becomes_a_lesson_and_keeps_its_identity():
    note = MemoryNote(summary="Retry the PDF in chunks.", agent_id="a1", role_label="ingester",
                      source_ids=["e1", "e2"], source_requesters=["slack:U1", "slack:U2"],
                      scope="slack#ops", dissent="one run needed no chunks", ceiling="MEDIUM",
                      confidence=0.7)
    lesson = lesson_from_note(note, collective_id="c", domain_tag="docs")
    assert lesson.lesson_id == note.note_id
    assert lesson.kind == "note"
    assert lesson.scope == "slack#ops" and lesson.ceiling == "MEDIUM"
    assert lesson.evidence.source_episode_ids == ["e1", "e2"]
    assert lesson.evidence.dissent == "one run needed no chunks"
    assert lesson.source_requesters == ["slack:U1", "slack:U2"]
    assert lesson.domain_tag == "docs"
    assert "(dissent:" in lesson.render()


# ---------------------------------------------------------------------------
# The ring — consumed once, filtered by scope + ceiling, bounded
# ---------------------------------------------------------------------------


def test_a_lesson_is_rendered_once_then_gone():
    ring = PeerLessonRing()
    assert ring.offer(_lesson(lesson_id="L1"))
    assert not ring.offer(_lesson(lesson_id="L1")), "duplicate ids are ignored"
    got = ring.take(reader_ceiling="", reader_scope="local", limit=3)
    assert [g.lesson_id for g in got] == ["L1"]
    assert ring.take(reader_ceiling="", reader_scope="local", limit=3) == []


def test_the_ceiling_hides_a_lesson_and_keeps_it_for_a_higher_reader():
    ring = PeerLessonRing()
    ring.offer(_lesson(lesson_id="H", ceiling="HIGH"))
    assert ring.take(reader_ceiling="MEDIUM", reader_scope="local", limit=3) == []
    assert len(ring) == 1, "hidden, not consumed"
    assert [g.lesson_id for g in ring.take(reader_ceiling="CRITICAL", reader_scope="local", limit=3)] == ["H"]


def test_an_unlabelled_ceiling_reads_as_critical():
    ring = PeerLessonRing()
    ring.offer(_lesson(lesson_id="U", ceiling=""))
    assert ring.take(reader_ceiling="HIGH", reader_scope="local", limit=3) == []
    assert ring.take(reader_ceiling="", reader_scope="local", limit=3), "no reader ceiling = operator"


def test_scope_must_match_exactly():
    ring = PeerLessonRing()
    ring.offer(_lesson(lesson_id="S", scope="slack#ops"))
    assert ring.take(reader_ceiling="", reader_scope="local", limit=3) == []
    assert ring.take(reader_ceiling="", reader_scope="slack#ops", limit=3)


def test_only_notes_render_in_phase_1_and_limit_and_newest_first_hold():
    ring = PeerLessonRing(ttl_s=0)  # tiny timestamps below; no expiry here
    ring.offer(_lesson(lesson_id="R", kind="role_patch", patch={"field": "x"}, ts=1.0))
    ring.offer(_lesson(lesson_id="N1", ts=2.0))
    ring.offer(_lesson(lesson_id="N2", ts=3.0))
    ring.offer(_lesson(lesson_id="N3", ts=4.0))
    got = ring.take(reader_ceiling="", reader_scope="local", limit=2)
    assert [g.lesson_id for g in got] == ["N3", "N2"]
    assert {g.lesson_id for g in ring.peek()} == {"R", "N1"}


def test_ttl_and_maxlen_bound_the_ring():
    ring = PeerLessonRing(maxlen=2, ttl_s=100.0)
    ring.offer(_lesson(lesson_id="old", ts=0.0), now=50.0)
    ring.offer(_lesson(lesson_id="a", ts=60.0), now=60.0)
    ring.offer(_lesson(lesson_id="b", ts=61.0), now=61.0)
    ring.offer(_lesson(lesson_id="c", ts=62.0), now=62.0)
    assert [g.lesson_id for g in ring.peek()] == ["b", "c"], "maxlen evicts oldest"
    assert ring.take(reader_ceiling="", reader_scope="local", limit=5, now=500.0) == [], "ttl expired all"


# ---------------------------------------------------------------------------
# The prompt block — after notes, evicted first, invisible when empty
# ---------------------------------------------------------------------------


def test_empty_peer_block_leaves_the_prompt_byte_identical():
    before = standard_blocks(task="do x", notes_heading="N:", notes_items=("- n1",))
    after = standard_blocks(task="do x", notes_heading="N:", notes_items=("- n1",),
                            peer_heading="", peer_items=())
    assert pack(before, 10_000).text == pack(after, 10_000).text


def test_peer_lessons_render_after_notes_and_are_evicted_before_them():
    heading, items = peer_lessons_parts([_lesson(summary="peer says y")])
    assert heading == PEER_LESSONS_HEADING
    blocks = standard_blocks(task="do x", notes_heading="MEMORY_NOTES:", notes_items=("- mine",),
                             peer_heading=heading, peer_items=tuple(items))
    full = pack(blocks, 10_000)
    assert full.text.index("MEMORY_NOTES:") < full.text.index(PEER_LESSONS_HEADING) < full.text.index("do x")
    # Squeeze: the task + notes fit, the peer block does not.
    need = pack(standard_blocks(task="do x", notes_heading="MEMORY_NOTES:", notes_items=("- mine",)), 10_000).est_tokens
    tight = pack(blocks, need + 2)
    assert tight.dropped.get(KIND_PEER_LESSONS, 0) >= 1
    assert tight.dropped.get(KIND_NOTES, 0) == 0
    assert "- mine" in tight.text and "peer says y" not in tight.text


# ---------------------------------------------------------------------------
# CognitiveCore — the ring drains into the next prompt, and only the next
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_renders_a_received_lesson_once_and_reports_it(monkeypatch):
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    core, llm = _core()
    assert core.receive_lesson(_lesson(lesson_id="L9", summary="Chunk PDFs over 40 MB."))
    role = RoleDefinitionConfig(purpose="review")

    first = await core.process_task(_payload(), role)
    assert first.lessons_used == ["L9"]
    assert PEER_LESSONS_HEADING in llm.users[-1]
    assert "[analyst] Chunk PDFs over 40 MB." in llm.users[-1]
    assert llm.users[-1].index(PEER_LESSONS_HEADING) < llm.users[-1].index("what did the analyst learn?")

    second = await core.process_task(_payload(), role)
    assert second.lessons_used == []
    assert PEER_LESSONS_HEADING not in llm.users[-1]


@pytest.mark.asyncio
async def test_the_kill_switch_keeps_the_prompt_untouched(monkeypatch):
    monkeypatch.setenv("ACC_PEER_LESSONS", "0")
    core, llm = _core()
    assert core.receive_lesson(_lesson()) is False
    result = await core.process_task(_payload(), RoleDefinitionConfig(purpose="review"))
    assert result.lessons_used == []
    assert PEER_LESSONS_HEADING not in llm.users[-1]


@pytest.mark.asyncio
async def test_memory_retrieval_off_also_means_no_peer_lessons(monkeypatch):
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    core, llm = _core()
    core.receive_lesson(_lesson())
    result = await core.process_task(_payload(), RoleDefinitionConfig(purpose="review", memory_retrieval=False))
    assert result.lessons_used == []
    assert PEER_LESSONS_HEADING not in llm.users[-1]
    assert core.pending_peer_lessons(), "not consumed by a reader that could not see it"


# ---------------------------------------------------------------------------
# Agent — accept path (transport filters) and publish path
# ---------------------------------------------------------------------------


def _agent_stub(*, agent_id="b1", receptors=None, core=None, redis=None, domain_id=""):
    ns = _agent_ns(agent_id=agent_id, receptors=receptors, core=core, redis=redis, domain_id=domain_id)
    ns._classify_lesson = lambda payload: Agent._classify_lesson(ns, payload)
    ns._adopt_lesson = lambda lesson: Agent._adopt_lesson(ns, lesson)
    return ns


def _agent_ns(*, agent_id="b1", receptors=None, core=None, redis=None, domain_id=""):
    return SimpleNamespace(
        agent_id=agent_id,
        _cognitive_core=core,
        _active_role=SimpleNamespace(domain_receptors=list(receptors or []), domain_id=domain_id,
                                     memory_reflection=True),
        config=SimpleNamespace(agent=SimpleNamespace(role="reviewer", collective_id="c")),
        backends=SimpleNamespace(signaling=SimpleNamespace(publish=AsyncMock()), vector=MagicMock(),
                                 llm=MagicMock()),
        _redis=redis,
        _peer_lessons_enabled=Agent._peer_lessons_enabled,
        _lessons_journal_id=lambda: "lessons-" + agent_id,
        _lesson_domain_tag=lambda: domain_id,
        _store_lesson=lambda cid, lid, body: Agent._store_lesson(_holder[0], cid, lid, body),
    )


_holder: list = []


def _stub_with_store(**kw):
    stub = _agent_stub(**kw)
    _holder[:] = [stub]
    return stub


@pytest.mark.parametrize("payload, verdict", [
    ("garbage", "invalid"),
    ({"from_agent": "b1", "summary": "mine"}, "own"),
    ({"from_agent": "a1", "summary": "for c9", "target_agent_id": "c9"}, "not-addressed"),
    ({"from_agent": "a1", "summary": "finance", "domain_tag": "finance"}, "no-receptor"),
])
def test_accept_drops_what_the_transport_rules_say(tmp_path, monkeypatch, payload, verdict):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    core, _ = _core()
    stub = _agent_stub(receptors=["software"], core=core)
    assert Agent._accept_lesson(stub, payload) == verdict
    assert core.pending_peer_lessons() == []


def test_accept_takes_a_matching_ligand_and_journals_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    core, _ = _core()
    stub = _agent_stub(receptors=["software"], core=core)
    body = _lesson(domain_tag="software", lesson_id="J1").model_dump()
    assert Agent._accept_lesson(stub, body) == "accepted"
    assert [l.lesson_id for l in core.pending_peer_lessons()] == ["J1"]
    # Addressed to me on the shared subject: accepted too.
    body2 = _lesson(domain_tag="", lesson_id="J2", target_agent_id="b1").model_dump()
    assert Agent._accept_lesson(stub, body2) == "accepted"
    journal = (tmp_path / "lessons-b1.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in journal]
    assert kinds == ["lesson", "lesson"]
    assert json.loads(journal[0])["direction"] == "received"


def test_accept_without_a_core_drops(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    stub = _agent_stub(core=None)
    assert Agent._accept_lesson(stub, _lesson().model_dump()) == "no-core"


@pytest.mark.asyncio
async def test_publish_lifts_each_note_onto_the_knowledge_subject(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    redis = _FakeRedis()
    stub = _stub_with_store(agent_id="a1", redis=redis, domain_id="docs")
    notes = [MemoryNote(summary=f"lesson {i}", agent_id="a1", role_label="ingester", ceiling="LOW")
             for i in range(2)]
    sent = await Agent._publish_lessons(stub, notes)
    assert sent == 2
    calls = stub.backends.signaling.publish.await_args_list
    assert [c.args[0] for c in calls] == ["acc.c.knowledge.docs"] * 2
    wire = [parse_lesson(json.loads(c.args[1])) for c in calls]
    assert all(w is not None and w.domain_tag == "docs" and w.from_agent == "a1" for w in wire)
    assert {w.lesson_id for w in wire} == {n.note_id for n in notes}
    assert set(redis.z["acc:c:lessons"]) == {n.note_id for n in notes}
    assert json.loads(redis.kv[f"acc:c:lesson:{notes[0].note_id}"])["summary"] == "lesson 0"
    journal = (tmp_path / "lessons-a1.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["direction"] for l in journal] == ["published", "published"]


@pytest.mark.asyncio
async def test_publish_is_off_with_the_kill_switch(monkeypatch):
    monkeypatch.setenv("ACC_PEER_LESSONS", "0")
    stub = _stub_with_store(agent_id="a1")
    assert await Agent._publish_lessons(stub, [MemoryNote(summary="x", agent_id="a1", role_label="r")]) == 0
    stub.backends.signaling.publish.assert_not_awaited()


def test_lessons_used_is_indexed_for_trace():
    redis = _FakeRedis()
    stub = SimpleNamespace(_redis=redis)
    Agent._index_lessons_used(stub, "c", "task-7", ["L1", "L2"])
    assert redis.sets["acc:c:lesson:L1:used"] == {"task-7"}
    assert redis.sets["acc:c:lesson:L2:used"] == {"task-7"}


# ---------------------------------------------------------------------------
# S2 — the relay.  Agent A reflects; agent B's next prompt carries it.
# ---------------------------------------------------------------------------


def _ep(content, emb):
    return {"id": content[:6], "agent_id": "a1", "ts": 1.0, "signal_type": "TASK_ASSIGN",
            "payload_json": json.dumps({"content": content}), "embedding": emb}


@pytest.mark.asyncio
async def test_s2_a_lesson_distilled_by_one_agent_reaches_another_agents_next_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)

    # --- agent A (analyst): two similar failures, one reflection pass ------
    near = [1.0, 0.0] + [0.0] * 382
    episodes = [_ep("pdf failed on 60MB", near), _ep("pdf oom again at 55MB", [0.99, 0.0] + [0.0] * 382)]
    a_llm = MagicMock()
    a_llm.complete = AsyncMock(return_value={"content": "PDFs above 50 MB exhaust the ingester; chunk them."})
    a_llm.embed = AsyncMock(return_value=[0.1] * 384)
    published: list[tuple[str, bytes]] = []

    async def _capture(subject, payload):
        published.append((subject, payload))

    stub_a = SimpleNamespace(
        agent_id="a1",
        _cognitive_core=SimpleNamespace(recent_episodes=lambda: episodes),
        _active_role=SimpleNamespace(memory_reflection=True, domain_id="", domain_receptors=[]),
        config=SimpleNamespace(agent=SimpleNamespace(role="analyst", collective_id="c")),
        backends=SimpleNamespace(llm=a_llm, vector=MagicMock(), signaling=SimpleNamespace(publish=_capture)),
        _redis=_FakeRedis(),
        _stop_event=None,
        _peer_lessons_enabled=Agent._peer_lessons_enabled,
        _lessons_journal_id=lambda: "lessons-a1",
        _lesson_domain_tag=lambda: "",
        _publish_lessons=None,
        _store_lesson=None,
    )
    stub_a._publish_lessons = lambda notes: Agent._publish_lessons(stub_a, notes)
    stub_a._record_notes = lambda notes: Agent._record_notes(stub_a, notes)
    stub_a._store_lesson = lambda cid, lid, body: Agent._store_lesson(stub_a, cid, lid, body)
    await Agent._run_reflection_once(stub_a)
    assert published, "reflection published nothing"
    subject, raw = published[0]
    assert subject == "acc.c.knowledge.general"

    # --- agent B (reviewer, a different role): hears it, uses it once ------
    core_b, llm_b = _core(agent_id="b1", role_label="reviewer")
    stub_b = _agent_stub(agent_id="b1", receptors=[], core=core_b)
    assert Agent._accept_lesson(stub_b, json.loads(raw)) == "accepted"

    result = await core_b.process_task(_payload("review the ingest pipeline"), RoleDefinitionConfig(purpose="review"))
    assert "chunk them" in llm_b.users[-1]
    assert "[analyst]" in llm_b.users[-1], "the reader sees the role, not the agent id"
    assert "a1" not in llm_b.users[-1].split(PEER_LESSONS_HEADING)[1].split("\n")[1]
    assert len(result.lessons_used) == 1
    assert result.lessons_used[0] == json.loads(raw)["lesson_id"]

    # Once.  The next turn does not repeat hearsay.
    again = await core_b.process_task(_payload("and again"), RoleDefinitionConfig(purpose="review"))
    assert again.lessons_used == [] and "chunk them" not in llm_b.users[-1]


# ---------------------------------------------------------------------------
# Phase 3 — a role_patch lesson becomes a proposal, never an applied change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_role_patch_lesson_is_queued_as_a_role_update_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    core, _ = _core()
    queued: list = []

    async def _queue(p, cid):
        queued.append((p, cid))
        return "ov-1"

    stub = _agent_stub(core=core)
    stub._queue_assistant_proposal = _queue
    body = _lesson(kind="role_patch", lesson_id="RP1", summary="Reviewers need more notes.",
                   patch={"role": "reviewer", "fields": {"memory_note_bandwidth": 5}}).model_dump()
    verdict, lesson = Agent._classify_lesson(stub, body)
    assert verdict == "role_patch" and core.pending_peer_lessons() == [], "not rendered as hearsay"
    oversight_id = await Agent._propose_from_lesson(stub, lesson)
    assert oversight_id == "ov-1"
    proposal, cid = queued[0]
    assert proposal.kind == "role_update" and cid == "c"
    assert proposal.params == {"role": "reviewer", "fields": {"memory_note_bandwidth": 5}, "lesson_id": "RP1"}
    assert "RP1" in proposal.rationale and proposal.risk_level == "HIGH"
    journal = [json.loads(l) for l in (tmp_path / "lessons-b1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert journal[-1]["direction"] == "proposed" and journal[-1]["oversight_id"] == "ov-1"


@pytest.mark.asyncio
async def test_a_role_patch_without_fields_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    stub = _agent_stub(core=_core()[0])
    stub._queue_assistant_proposal = AsyncMock(return_value="ov-x")
    assert await Agent._propose_from_lesson(stub, _lesson(kind="role_patch", patch={"role": "r"})) == ""
    stub._queue_assistant_proposal.assert_not_awaited()


def test_other_kinds_are_held_on_the_wire_but_not_rendered():
    core, _ = _core()
    stub = _agent_stub(core=core)
    verdict, _ = Agent._classify_lesson(stub, _lesson(kind="rule", patch={"rule": "x"}).model_dump())
    assert verdict == "kind-not-rendered" and core.pending_peer_lessons() == []


# ---------------------------------------------------------------------------
# Phase 4 — durable adoption behind a role flag
# ---------------------------------------------------------------------------


def test_a_role_definition_that_says_nothing_adopts_peer_lessons():
    """D-028 (operator, 2026-09-24): durable by default."""
    from acc.config import ACCEPT_PEER_LESSONS_DEFAULT, RoleDefinitionConfig

    assert ACCEPT_PEER_LESSONS_DEFAULT is True
    assert RoleDefinitionConfig().accept_peer_lessons is True
    assert RoleDefinitionConfig(accept_peer_lessons=False).accept_peer_lessons is False


def test_adoption_is_on_by_default_and_a_role_can_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    core, _ = _core()
    redis = _FakeRedis()
    stub = _agent_stub(core=core, redis=redis)
    key = "acc:c:memory_notes_shared:reviewer:local"

    # Opted out: rendered once, nothing durable.
    stub._active_role.accept_peer_lessons = False
    assert Agent._accept_lesson(stub, _lesson(lesson_id="A0", scope="local").model_dump()) == "accepted"
    assert not [k for k in redis.kv if "memory_notes_shared" in k], "opted out: nothing durable"

    # The default -- a role object that does not carry the field at all, which is
    # exactly what an unset field in a role definition means.
    del stub._active_role.accept_peer_lessons
    assert Agent._accept_lesson(stub, _lesson(lesson_id="A1", ceiling="LOW", scope="local",
                                              source_requesters=["slack:U1"]).model_dump()) == "accepted"
    entries = json.loads(redis.kv[key])
    assert entries[0]["note_id"] == "A1" and entries[0]["ceiling"] == "LOW" and entries[0]["at"] > 0
    from acc.memory_reflection import read_hot_cache
    assert read_hot_cache(redis, "c", "reviewer", "local") == [], "probation: not read yet"
    rows = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["kind"] == "adopt" and rows[-1]["target"]["id"] == "A1"
    assert rows[-1]["trigger"] == "accept_peer_lessons"


def test_the_default_does_not_widen_what_a_lesson_may_reach(tmp_path, monkeypatch):
    """Durable-by-default changes *whether* an accepted lesson is kept, never
    who may read it: the adopted note keeps the lesson's ceiling and scope, and
    the read path filters on both.  A role_patch is still never adopted."""
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    monkeypatch.delenv("ACC_PEER_LESSONS", raising=False)
    from acc.memory_reflection import read_hot_cache
    core, _ = _core()
    redis = _FakeRedis()
    stub = _agent_stub(core=core, redis=redis)
    assert not hasattr(stub._active_role, "accept_peer_lessons"), "the default path"

    Agent._accept_lesson(stub, _lesson(lesson_id="C1", ceiling="CRITICAL", scope="local",
                                       summary="critical-only finding").model_dump())
    Agent._accept_lesson(stub, _lesson(lesson_id="S1", ceiling="LOW", scope="team-b",
                                       summary="another scope's finding").model_dump())
    for k in [k for k in redis.kv if "memory_notes_shared" in k]:     # past probation
        entries = json.loads(redis.kv[k])
        for e in entries:
            e["at"] = 0
        redis.kv[k] = json.dumps(entries)

    assert "critical-only finding" not in read_hot_cache(redis, "c", "reviewer", "local", reader_ceiling="MEDIUM")
    assert "critical-only finding" in read_hot_cache(redis, "c", "reviewer", "local", reader_ceiling="CRITICAL")
    assert "another scope's finding" not in read_hot_cache(redis, "c", "reviewer", "local", reader_ceiling="CRITICAL")
    assert "another scope's finding" in read_hot_cache(redis, "c", "reviewer", "team-b", reader_ceiling="CRITICAL")

    verdict, _ = Agent._classify_lesson(stub, _lesson(lesson_id="R1", kind="role_patch",
                                                      patch={"fields": {"purpose": "x"}}).model_dump())
    assert verdict == "role_patch", "a role_patch still proposes, it is never adopted"
    assert not any("R1" in v for v in redis.kv.values() if isinstance(v, str))


# ---------------------------------------------------------------------------
# Phase 5 — the outcome: objective signals move confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blocked, verdict, signal, delta", [
    (True, "", "blocked", -0.10),
    (False, "NEEDS_REVISE", "verdict:NEEDS_REVISE", -0.10),
    (False, "BAD", "verdict:BAD", -0.10),
    (False, "GOOD", "verdict:GOOD", 0.10),
    (False, "PARTIAL", "verdict:PARTIAL", 0.0),
    (False, "", "completed", 0.0),
])
def test_outcome_delta_is_objective(blocked, verdict, signal, delta):
    assert Agent.lesson_outcome_delta(blocked=blocked, verdict=verdict) == (signal, delta)


def test_an_outcome_moves_the_lessons_confidence_and_is_ledgered(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    redis = _FakeRedis()
    redis.lists = {}
    redis.rpush = lambda k, v: redis.lists.setdefault(k, []).append(v)
    redis.set("acc:c:lesson:L1", json.dumps({"lesson_id": "L1", "confidence": 0.5}))
    stub_ref: list = []
    stub = SimpleNamespace(agent_id="b1", _redis=redis,
                           config=SimpleNamespace(agent=SimpleNamespace(collective_id="c", role="reviewer")),
                           _index_lessons_used=lambda cid, t, ids: Agent._index_lessons_used(stub_ref[0], cid, t, ids),
                           lesson_outcome_delta=Agent.lesson_outcome_delta)
    stub_ref.append(stub)
    Agent._record_lesson_outcomes(stub, "c", "task-3", ["L1"], blocked=False, verdict="GOOD", score=0.9)
    assert json.loads(redis.kv["acc:c:lesson:L1"])["confidence"] == pytest.approx(0.6)
    obs = json.loads(redis.lists["acc:c:lesson:L1:outcomes"][0])
    assert obs["signal"] == "verdict:GOOD" and obs["task_id"] == "task-3"
    assert redis.sets["acc:c:lesson:L1:used"] == {"task-3"}
    Agent._record_lesson_outcomes(stub, "c", "task-4", ["L1"], blocked=True, verdict="", score=0.2)
    assert json.loads(redis.kv["acc:c:lesson:L1"])["confidence"] == pytest.approx(0.5)
    rows = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["outcome", "outcome"]
    assert rows[1]["measured"]["signal"] == "blocked" and rows[1]["target"]["id"] == "L1"
