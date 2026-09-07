"""`20260906-enterprise-brain-hub-scope` Phase 2 — the curator role and the
approver-tier check.

The curator never answers and never learns: a role with no chat surface whose
only output is publish proposals into its hub, built from what the bound
instances already published.  A hub promotion is a person's decision at
operator tier — a decision that carries no tier, or a lower one, is refused
and journalled, never silently applied.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from acc import memory_reflection as M
from acc.assistant_proposal import (
    PROPOSAL_PUBLISH,
    AssistantProposal,
    build_publish_proposal,
    dispatch_approved_proposal,
)
from acc.config import RoleDefinitionConfig
from acc.signals import redis_shared_notes_key

ROOT = Path(__file__).resolve().parent.parent


def _note(**kw):
    base = dict(summary="PDFs over 10MB exhaust the ingester.", agent_id="a1", role_label="analyst",
                source_ids=["e1", "e2"], source_requesters=["slack:U1", "slack:U2"],
                scope="slack#C1", ceiling="MEDIUM")
    base.update(kw)
    return M.MemoryNote(**base)


class _Redis:
    def __init__(self):
        self.store, self.sets = {}, {}

    def set(self, k, v): self.store[k] = v
    def setex(self, k, ttl, v): self.store[k] = v
    def get(self, k): return self.store.get(k)
    def expire(self, k, ttl): pass
    def delete(self, *ks):
        for k in ks: self.store.pop(k, None)
    def keys(self, pattern):
        head, _, tail = pattern.partition("*")
        return [k for k in self.store if k.startswith(head) and tail.rstrip("*") in k]
    def smembers(self, k): return set(self.sets.get(k, set()))
    def sadd(self, k, *m): self.sets.setdefault(k, set()).update(m)


# ---------------------------------------------------------------------------
# the role
# ---------------------------------------------------------------------------


class TestRole:
    def test_role_yaml_loads_as_a_no_surface_curator(self):
        raw = yaml.safe_load((ROOT / "roles" / "hub_curator" / "role.yaml").read_text(encoding="utf-8"))
        role = RoleDefinitionConfig(**raw["role_definition"])
        assert role.chat_surface is False and role.curate_interval_s == 900
        assert [s for s in role.allowed_skills if s != "okf"] == []          # okf is the pure default every role gets
        assert role.allowed_mcps == [] and role.task_types == []
        assert role.can_route is False and role.memory_reflection is False and role.memory_retrieval is False

    def test_every_other_role_keeps_a_chat_surface_by_default(self):
        assert RoleDefinitionConfig().chat_surface is True
        assert RoleDefinitionConfig().curate_interval_s == 0

    def test_known_to_the_operator_but_not_a_control_pack_role(self):
        """A built-in role the operator's catalogue knows; deliberately NOT in
        CONTROL_ROLES, which resolve only from the signed @acc/control-roles
        pack (rebuilding that fixture needs the signing key)."""
        from acc.pkg.role_resolution import CONTROL_ROLES
        assert "hub_curator" not in CONTROL_ROLES
        known = (ROOT / "operator" / "internal" / "rolecatalogue" / "known_roles.txt").read_text(encoding="utf-8").split()
        assert "hub_curator" in known
        assert (ROOT / "roles" / "hub_curator" / "role.yaml").is_file()

    def test_task_loop_drops_tasks_for_a_role_without_a_chat_surface(self):
        from acc import agent as agent_mod
        src = inspect.getsource(agent_mod)
        i = src.index('if not getattr(self._active_role, "chat_surface", True):')
        assert "return" in src[i:i + 300]
        assert i < src.index("# PR-V4 — directed-by-ROLE filter")   # before any role-target filtering


# ---------------------------------------------------------------------------
# the approver-tier check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,expected_ok", [("operator", True), ("requester", False), ("", False), ("viewer", False)])
async def test_hub_promotion_requires_an_operator_tier_approver(tier, expected_ok):
    redis = _Redis(); signaling = MagicMock(); signaling.publish = AsyncMock()
    p = build_publish_proposal(_note(), "hub:enterprise", collective_id="alice-hub")
    p.operator_id = "system:flg"; p.collective_id = "alice-hub"
    ok = await dispatch_approved_proposal(signaling, p, redis_client=redis, approver_tier=tier)
    assert ok is expected_ok
    hub_key = redis_shared_notes_key("enterprise", "analyst", M.HUB_TIER)
    assert (hub_key in redis.store) is expected_ok
    trigger = signaling.publish.await_args.args[1]["trigger"]
    assert trigger == ("note_published" if expected_ok else "note_publish_refused")
    if not expected_ok:
        assert signaling.publish.await_args.args[1]["reason"].startswith("operator tier required")


@pytest.mark.asyncio
async def test_ordinary_destination_does_not_need_a_tier():
    redis = _Redis(); signaling = MagicMock(); signaling.publish = AsyncMock()
    p = build_publish_proposal(_note(), "slack#C9", collective_id="alice-hub")
    p.operator_id = "system:flg"; p.collective_id = "alice-hub"
    assert await dispatch_approved_proposal(signaling, p, redis_client=redis) is True


def test_every_decision_surface_stamps_the_tier():
    from acc.cli import oversight_cmd
    from acc.tui import actor, app
    from acc.webgui import routes_action
    assert "approver_tier" in inspect.getsource(oversight_cmd) and callable(oversight_cmd._approver_tier)
    assert "approver_tier" in inspect.getsource(app) and callable(actor.tui_actor_tier)
    assert "approver_tier" in inspect.getsource(routes_action)


def test_agent_threads_the_tier_from_the_decision_to_the_dispatch():
    from acc import agent as agent_mod
    src = inspect.getsource(agent_mod)
    assert 'approver_tier = str(payload.get("approver_tier", "") or "")' in src
    assert "approver_tier=approver_tier," in src[src.index("async def _maybe_dispatch_assistant_proposal"):]


# ---------------------------------------------------------------------------
# the curator's pass
# ---------------------------------------------------------------------------


class _Runtime:
    """The attributes _curate_once / _queue_assistant_proposal read."""

    def __init__(self, redis):
        from types import SimpleNamespace
        self.config = SimpleNamespace(agent=SimpleNamespace(collective_id="enterprise", role="hub_curator"))
        self.agent_id = "curator-1"
        self._redis = redis
        self._oversight_queue = MagicMock()
        self._oversight_queue._timeout_s = 300
        self._oversight_queue.submit = AsyncMock(side_effect=lambda **k: f"ov-{k['task_id'][:8]}")
        self.backends = MagicMock(); self.backends.signaling = MagicMock(); self.backends.signaling.publish = AsyncMock()
        self._active_role = RoleDefinitionConfig(curate_interval_s=900, chat_surface=False)
        self._stop_event = asyncio.Event()
        from acc.agent import Agent  # noqa: PLC0415
        self._queue_assistant_proposal = Agent._queue_assistant_proposal.__get__(self)
        self._curate_once = Agent._curate_once.__get__(self)


def _seed(redis):
    M.publish_note(redis, "alice-hub", "analyst", "two people agree", "slack#C1", ceiling="MEDIUM",
                   source_requesters=["slack:U1", "slack:U2"], note_id="n1")
    M.publish_note(redis, "bob-hub", "analyst", "one person's account", "slack#C2",
                   source_requesters=["slack:U3"], note_id="n2")
    M.publish_note(redis, "enterprise", "analyst", "already there", M.HUB_TIER, source_requesters=["slack:U4", "slack:U5"])
    M.publish_note(redis, "carol-hub", "analyst", "already there", "slack#C3", source_requesters=["slack:U4", "slack:U5"], note_id="n3")
    key = "acc:alice-hub:memory_notes_shared:analyst:slack#C1"     # past probation
    rows = json.loads(redis.store[key]); rows[0]["at"] -= 5000; redis.store[key] = json.dumps(rows)
    key = "acc:carol-hub:memory_notes_shared:analyst:slack#C3"
    rows = json.loads(redis.store[key]); rows[0]["at"] -= 5000; redis.store[key] = json.dumps(rows)


def test_curate_once_queues_one_hub_proposal_per_qualifying_note():
    from acc.agent import Agent
    redis = _Redis(); _seed(redis); rt = _Runtime(redis)
    queued = asyncio.run(Agent._curate_once(rt))
    assert queued == 1
    submit = rt._oversight_queue.submit.await_args.kwargs
    assert submit["role_id"] == "assistant" or submit["role_id"]           # a row in the HUB's queue
    cached = [k for k in redis.store if k.startswith("acc:enterprise:assistant_proposal:")]
    assert len(cached) == 1
    p = AssistantProposal.from_payload(json.loads(redis.store[cached[0]]))
    assert p.kind == PROPOSAL_PUBLISH and p.params["destination_scope"] == "hub:enterprise"
    assert p.params["summary"] == "two people agree" and p.params["ceiling"] == "MEDIUM"
    assert p.collective_id == "enterprise" and "alice-hub" in p.rationale
    assert rt.backends.signaling.publish.await_count >= 1                  # the pending announcement
    assert "n1" in redis.sets["acc:enterprise:curator:proposed"]


def test_curate_once_does_not_repeat_itself():
    from acc.agent import Agent
    redis = _Redis(); _seed(redis); rt = _Runtime(redis)
    assert asyncio.run(Agent._curate_once(rt)) == 1
    assert asyncio.run(Agent._curate_once(rt)) == 0                       # remembered for a week


def test_curate_once_is_a_no_op_without_redis_or_queue():
    from acc.agent import Agent
    rt = _Runtime(None)
    assert asyncio.run(Agent._curate_once(rt)) == 0
    rt = _Runtime(_Redis()); rt._oversight_queue = None
    assert asyncio.run(Agent._curate_once(rt)) == 0


def test_curator_loop_returns_at_once_for_roles_that_do_not_curate():
    from acc.agent import Agent
    rt = _Runtime(_Redis()); rt._active_role = RoleDefinitionConfig()
    asyncio.run(asyncio.wait_for(Agent._curator_loop(rt), timeout=1))


def test_curator_loop_runs_a_pass_then_waits_on_the_stop_event(monkeypatch):
    from acc.agent import Agent
    rt = _Runtime(_Redis()); rt._active_role = RoleDefinitionConfig(curate_interval_s=3600)
    passes = []

    async def fake_once():
        passes.append(1); rt._stop_event.set(); return 0
    rt._curate_once = fake_once                      # the runtime binds its own; replace the bound one
    asyncio.run(asyncio.wait_for(Agent._curator_loop(rt), timeout=2))
    assert passes == [1]
