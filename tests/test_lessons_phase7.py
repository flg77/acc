"""`20260923-lessons-that-travel` — the open parts: Phase 7 (the arbiter
countersigns unsigned ROLE_UPDATEs), the cross-step critic join (Phase 5, the
arbiter's half) and the per-episode harness fingerprint (PA-04)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.agent import Agent
from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.plan import PlanExecutor, _Plan, _Step
from acc.role_assign import generate_keypair_b64
from acc.role_store import RoleStore


# ---------------------------------------------------------------------------
# Phase 7 — countersigning
# ---------------------------------------------------------------------------


def _arbiter(*, signing_key: str, roster=None, redis=None):
    return SimpleNamespace(
        agent_id="arbiter-1",
        config=SimpleNamespace(agent=SimpleNamespace(role="arbiter", collective_id="c"),
                               security=SimpleNamespace(arbiter_signing_key=signing_key)),
        _redis=redis, _worker_roster=roster or {},
        _bump_role_version=Agent._bump_role_version,
        _resolve_role_definition=lambda name: Agent._resolve_role_definition(_ARB[0], name),
    )


_ARB: list = []


def _mk(**kw):
    a = _arbiter(**kw)
    _ARB[:] = [a]
    return a


def _store_for(role_def: RoleDefinitionConfig, verify_key: str, agent_id="reviewer-1") -> RoleStore:
    store = RoleStore.__new__(RoleStore)
    store._current = role_def
    store._redis = None
    store._vector = None
    store._collective_id = "c"
    store._agent_id = agent_id
    store._role_updated = MagicMock()
    store._config = SimpleNamespace(security=SimpleNamespace(
        signing_mode="ed25519", arbiter_verify_key=verify_key,
        spiffe=SimpleNamespace(allow_ed25519_fallback=False)))
    store._get_arbiter_id = lambda: "arbiter-1"
    return store


@pytest.mark.parametrize("v, expected", [("0.1.0", "0.1.1"), ("7", "8"), ("v2", "v2.1"), ("", "0.1.1")])
def test_version_bump(v, expected):
    assert Agent._bump_role_version(v) == expected


def test_an_approved_proposal_is_countersigned_and_the_role_store_accepts_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    priv, pub = generate_keypair_b64()
    current = RoleDefinitionConfig(purpose="review things", version="0.3.0", memory_note_bandwidth=3)
    # The roster knows a reviewer; its countersigned state is in Redis.
    redis = MagicMock()
    redis.get.return_value = current.model_dump_json().encode()
    arb = _mk(signing_key=priv, roster={"reviewer-1": SimpleNamespace(role="reviewer")}, redis=redis)

    unsigned = {"trigger": "assistant_proposal", "proposal_id": "p1", "role": "reviewer",
                "fields": {"memory_note_bandwidth": 5}, "lesson_id": "L1", "ts": 1.0}
    signed = Agent._countersign_role_update(arb, unsigned)
    assert signed is not None
    assert signed["approver_id"] == "arbiter-1" and signed["countersigned"] is True
    assert signed["countersigned_for"] == "assistant_proposal" and signed["lesson_id"] == "L1"
    assert signed["role_definition"]["memory_note_bandwidth"] == 5
    assert signed["role_definition"]["version"] == "0.3.1"
    assert signed["role_definition"]["purpose"] == "review things", "the rest of the role is kept"

    # A reviewer's RoleStore verifies the signature against the arbiter's key and applies it.
    store = _store_for(current, pub)
    store.apply_update(signed)
    assert store._current.memory_note_bandwidth == 5 and store._current.version == "0.3.1"
    rows = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["kind"] == "role_patch" and rows[-1]["evidence"]["lesson_id"] == "L1"
    assert rows[-1]["target"]["old"] == {"version": "0.3.0", "memory_note_bandwidth": 3}

    # Tampering after signing is caught.
    tampered = dict(signed); tampered["role_definition"] = {**signed["role_definition"], "memory_note_bandwidth": 99}
    from acc.role_store import RoleUpdateRejectedError
    with pytest.raises(RoleUpdateRejectedError):
        _store_for(current, pub).apply_update(tampered)


def test_countersign_refuses_what_it_should():
    priv, _pub = generate_keypair_b64()
    # not the arbiter
    worker = _mk(signing_key=priv); worker.config.agent.role = "analyst"
    assert Agent._countersign_role_update(worker, {"role": "x", "fields": {"a": 1}}) is None
    # no key
    assert Agent._countersign_role_update(_mk(signing_key=""), {"role": "x", "fields": {"a": 1}}) is None
    # unresolvable role, no definition
    arb = _mk(signing_key=priv)
    arb._resolve_role_definition = lambda name: None
    assert Agent._countersign_role_update(arb, {"role": "ghost", "fields": {"a": 1}}) is None
    # a definition that does not validate
    assert Agent._countersign_role_update(
        _mk(signing_key=priv), {"role": "r", "role_definition": {"purpose": "x", "memory_note_bandwidth": "lots"}},
    ) is None


def test_a_cli_infuse_with_an_empty_signature_is_countersigned_as_is():
    priv, pub = generate_keypair_b64()
    arb = _mk(signing_key=priv)
    definition = RoleDefinitionConfig(purpose="ingest", version="1.0.0").model_dump()
    signed = Agent._countersign_role_update(arb, {"signal_type": "ROLE_UPDATE", "approver_id": "flg",
                                                  "signature": "", "role_definition": definition, "role": "ingester"})
    assert signed["countersigned_for"] == "flg" and signed["role_definition"]["version"] == "1.0.0", "no fields: no bump"
    _store_for(RoleDefinitionConfig(purpose="old"), pub, agent_id="ingester-1").apply_update(signed)


@pytest.mark.asyncio
async def test_the_handler_countersigns_once_and_applies_only_to_the_named_role():
    """The wire round trip: an unsigned update reaches the arbiter, which
    re-publishes a signed one; the signed one reaches a reviewer (applies) and
    an analyst (ignores); the signed one reaching the arbiter again is not
    re-signed."""
    priv, pub = generate_keypair_b64()
    published: list[dict] = []

    async def _pub(subject, payload):
        published.append(json.loads(payload))

    calls = {"applied": []}

    def _handler_for(role, agent_id, countersign):
        store = MagicMock()
        store.apply_update = lambda payload: calls["applied"].append((agent_id, payload["role_definition"]["version"]))
        store.get_current = lambda: SimpleNamespace(version="x")
        return SimpleNamespace(
            agent_id=agent_id,
            config=SimpleNamespace(agent=SimpleNamespace(role=role, collective_id="c"),
                                   security=SimpleNamespace(arbiter_signing_key=priv if countersign else "")),
            backends=SimpleNamespace(signaling=SimpleNamespace(publish=_pub)),
            _role_store=store, _active_role=None,
            _countersign_role_update=(lambda payload: countersign_fn(payload)) if countersign else (lambda payload: None),
        )

    arb = _mk(signing_key=priv)
    arb._resolve_role_definition = lambda name: RoleDefinitionConfig(purpose="review", version="2").model_dump()
    countersign_fn = lambda payload: Agent._countersign_role_update(arb, payload)  # noqa: E731

    # Drive the inner handler through the real method by extracting it: the
    # subscription registers a closure, so call _subscribe_role_updates with a
    # signaling stub that captures the handler and a stop event that is set.
    import asyncio

    async def _run(ns):
        captured = {}

        async def _subscribe(subject, handler):
            captured["h"] = handler

        ns.backends.signaling.subscribe = _subscribe
        ns._stop_event = asyncio.Event(); ns._stop_event.set()
        await Agent._subscribe_role_updates(ns)
        return captured["h"]

    h_arb = await _run(_handler_for("arbiter", "arbiter-1", True))
    unsigned = json.dumps({"trigger": "assistant_proposal", "role": "reviewer", "fields": {"memory_note_bandwidth": 4}}).encode()
    await h_arb(unsigned)
    assert len(published) == 1 and published[0]["countersigned"] and published[0]["signature"]
    signed_bytes = json.dumps(published[0]).encode()

    await h_arb(signed_bytes)
    assert len(published) == 1, "a countersigned update is not signed again"

    h_rev = await _run(_handler_for("reviewer", "reviewer-1", False))
    h_ana = await _run(_handler_for("analyst", "analyst-1", False))
    await h_rev(signed_bytes)
    await h_ana(signed_bytes)
    assert calls["applied"] == [("reviewer-1", "3")], "only the named role applies it (version 2 bumped to 3)"
    await h_ana(unsigned)
    assert len(published) == 1 and calls["applied"] == [("reviewer-1", "3")], "a worker neither signs nor applies"


# ---------------------------------------------------------------------------
# Phase 5, the arbiter's half — a reviewer's verdict reaches the reviewed
# step's lessons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reviewer_verdict_lands_on_the_upstream_steps_lessons():
    ex = PlanExecutor.__new__(PlanExecutor)
    seen: list[dict] = []
    ex.on_critic_verdict = lambda **kw: seen.append(kw)
    work = _Step(step_id="work", role="coding_agent", depends_on=[], raw={}, task_id="t-work", lessons_used=["L1", "L2"])
    review = _Step(step_id="review", role="reviewer", depends_on=["work"], raw={}, task_id="t-review")
    plan = _Plan.__new__(_Plan); plan.plan_id = "p1"; plan.steps = {"work": work, "review": review}
    n = await PlanExecutor._notify_critic_verdict(ex, plan, review, {"eval_outcome": {"verdict": "GOOD"}}, "t-review")
    assert n == 1
    assert seen == [{"lesson_ids": ["L1", "L2"], "reviewed_task_id": "t-work", "reviewer_task_id": "t-review",
                     "verdict": "GOOD", "plan_id": "p1", "step_id": "work"}]
    assert await PlanExecutor._notify_critic_verdict(ex, plan, review, {"output": "no verdict"}, "t-review") == 0
    assert await PlanExecutor._notify_critic_verdict(ex, plan, work, {"eval_outcome": {"verdict": "BAD"}}, "t-work") == 0, \
        "a step with no upstream reviews nothing"


def test_the_arbiter_records_a_critic_outcome_as_such(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    lists: dict = {}
    redis = MagicMock()
    redis.get.return_value = json.dumps({"lesson_id": "L1", "confidence": 0.4})
    redis.rpush = lambda k, v: lists.setdefault(k, []).append(v)
    saved: dict = {}
    redis.set = lambda k, v: saved.__setitem__(k, v)
    arb = SimpleNamespace(agent_id="arbiter-1", _redis=redis,
                          config=SimpleNamespace(agent=SimpleNamespace(collective_id="c", role="arbiter")),
                          _record_lesson_outcomes=None, lesson_outcome_delta=Agent.lesson_outcome_delta,
                          _index_lessons_used=lambda *a: (_ for _ in ()).throw(AssertionError("critic must not index")))
    arb._record_lesson_outcomes = lambda *a, **k: Agent._record_lesson_outcomes(arb, *a, **k)
    Agent._record_critic_verdict(arb, lesson_ids=["L1"], reviewed_task_id="t-work", reviewer_task_id="t-review",
                                 verdict="NEEDS_REVISE", plan_id="p1", step_id="work")
    obs = json.loads(lists["acc:c:lesson:L1:outcomes"][0])
    assert obs["source"] == "critic" and obs["reviewer_task_id"] == "t-review" and obs["delta"] == -0.1
    assert json.loads(saved["acc:c:lesson:L1"])["confidence"] == pytest.approx(0.3)
    row = json.loads((tmp_path / "led.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["trigger"] == "critic:verdict:NEEDS_REVISE" and row["evidence"]["reviewer_task_id"] == "t-review"


# ---------------------------------------------------------------------------
# PA-04 — the harness fingerprint
# ---------------------------------------------------------------------------


class _LLM:
    async def complete(self, system, user, response_schema=None):
        return {"content": "ok", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    async def embed(self, text):
        return [0.0] * 384


class _Vec:
    def __init__(self):
        self.rows = []

    def insert(self, table, rows):
        self.rows.extend((table, r) for r in rows)

    def search(self, *_a, **_k):
        return []


@pytest.mark.asyncio
async def test_the_fingerprint_is_stable_per_harness_and_rides_the_episode():
    vec = _Vec()
    core = CognitiveCore(agent_id="b1", collective_id="c", llm=_LLM(), vector=vec, redis_client=None, role_label="r")
    role = RoleDefinitionConfig(purpose="p", version="1")
    r1 = await core.process_task({"signal_type": "TASK_ASSIGN", "task_id": "t1", "content": "a"}, role)
    r2 = await core.process_task({"signal_type": "TASK_ASSIGN", "task_id": "t2", "content": "b"}, role)
    assert r1.harness_fingerprint and r1.harness_fingerprint == r2.harness_fingerprint, "same harness, different task"
    r3 = await core.process_task({"signal_type": "TASK_ASSIGN", "task_id": "t3", "content": "a"},
                                 RoleDefinitionConfig(purpose="p", version="2"))
    assert r3.harness_fingerprint != r1.harness_fingerprint, "a role version is a different harness"
    episode = next(r for t, r in vec.rows if t == "episodes")
    assert json.loads(episode["payload_json"])["harness_fingerprint"] == r1.harness_fingerprint
    assert "harness_fingerprint" not in episode, "the schema is fixed; it rides payload_json"
