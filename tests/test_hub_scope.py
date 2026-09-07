"""`20260906-enterprise-brain-hub-scope` Phase 1 — the enterprise brain.

Two things, both pinned against the quorum / probation / directedness the
memory change already had:

1. **The hub tier is a destination and a read path, and the only one across
   instances.** A publish proposal may name ``hub:<cid>``; on approval the
   note lands under the hub's collective id, and every collective bound to
   that hub reads it on the prompt path. A collective never reads another
   collective's shared tier; the hub's own keys are never candidates.
2. **The information rule is enforced.** A note carries the highest ceiling
   among its sources (an unattributed source counts as the operator's); the
   cache and the published copy carry it; a reader below it never sees it —
   in its own scope, in a shared tier, in the hub.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc import memory_curate as C
from acc import memory_forget as F
from acc import memory_reflection as M
from acc.assistant_proposal import (
    AssistantProposal,
    PROPOSAL_PUBLISH,
    QuorumNotMet,
    build_publish_proposal,
    dispatch_approved_proposal,
)
from acc.signals import redis_memory_notes_key, redis_shared_notes_key


class _FakeRedis:
    def __init__(self):
        self.store, self.ttls = {}, {}

    def set(self, k, v):
        self.store[k] = v

    def setex(self, k, ttl, v):
        self.store[k] = v; self.ttls[k] = ttl

    def get(self, k):
        return self.store.get(k)

    def expire(self, k, ttl):
        self.ttls[k] = ttl

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        if "*" in prefix:                                      # acc:*:memory_notes_shared:*
            head, tail = prefix.split("*", 1)
            return [k for k in self.store if k.startswith(head) and tail.rstrip("*") in k]
        return [k for k in self.store if k.startswith(prefix)]


def _note(**kw):
    base = dict(summary="PDFs over 10MB exhaust the ingester.", agent_id="a1",
                role_label="analyst", source_ids=["e1", "e2"],
                source_requesters=["slack:U1", "slack:U2"], scope="slack#C1",
                ceiling="MEDIUM")
    base.update(kw)
    return M.MemoryNote(**base)


@pytest.fixture(autouse=True)
def _no_probation(monkeypatch):
    monkeypatch.setattr(M, "PROBATION_S", 0.0)
    monkeypatch.setattr(C, "PROBATION_S", 0.0)


# ---------------------------------------------------------------------------
# 1. destinations
# ---------------------------------------------------------------------------


class TestDestination:
    def test_hub_destination_round_trips(self):
        assert M.hub_destination("enterprise") == "hub:enterprise"
        assert M.parse_destination("hub:enterprise") == ("enterprise", M.HUB_TIER)
        assert M.parse_destination("slack#C9") == ("", "slack#C9")
        assert M.parse_destination("") == ("", "")

    def test_publish_proposal_carries_the_ceiling_and_the_hub(self):
        p = build_publish_proposal(_note(), "hub:enterprise", collective_id="alice-dev")
        assert p.kind == PROPOSAL_PUBLISH
        assert p.params["destination_scope"] == "hub:enterprise"
        assert p.params["ceiling"] == "MEDIUM"
        assert p.params["source_requesters"] == ["slack:U1", "slack:U2"]

    def test_quorum_still_guards_a_hub_proposal(self):
        with pytest.raises(QuorumNotMet):
            build_publish_proposal(_note(source_requesters=["slack:U1"]), "hub:enterprise")


@pytest.mark.asyncio
async def test_approved_hub_publication_lands_under_the_hub():
    redis = _FakeRedis()
    p = build_publish_proposal(_note(), "hub:enterprise", collective_id="alice-dev")
    p.operator_id = "system:flg"; p.collective_id = "alice-dev"
    ok = await dispatch_approved_proposal(AsyncMock(), p, redis_client=redis)
    assert ok is True
    hub_key = redis_shared_notes_key("enterprise", "analyst", M.HUB_TIER)
    entries = json.loads(redis.store[hub_key])
    assert entries[0]["summary"].startswith("PDFs over 10MB")
    assert entries[0]["ceiling"] == "MEDIUM"
    assert entries[0]["source_requesters"] == ["slack:U1", "slack:U2"]
    assert entries[0]["note_id"]
    # nothing was written into the publishing collective's own shared tier
    assert not any(k.startswith("acc:alice-dev:memory_notes_shared") for k in redis.store)


@pytest.mark.asyncio
async def test_plain_destination_is_unchanged():
    redis = _FakeRedis()
    p = build_publish_proposal(_note(), "slack#C9", collective_id="alice-dev")
    p.operator_id = "system:flg"; p.collective_id = "alice-dev"
    assert await dispatch_approved_proposal(AsyncMock(), p, redis_client=redis)
    assert redis_shared_notes_key("alice-dev", "analyst", "slack#C9") in redis.store


# ---------------------------------------------------------------------------
# 2. the read path: hub-only across instances
# ---------------------------------------------------------------------------


class TestReadPath:
    def _seed(self, redis):
        M.publish_note(redis, "enterprise", "analyst", "hub lesson", M.HUB_TIER, ceiling="MEDIUM")
        M.publish_note(redis, "bob-dev", "analyst", "bob's shared lesson", "slack#C1", ceiling="LOW")
        M.write_hot_cache(redis, "alice-dev", "analyst", [_note(summary="alice's own", scope="slack#C1")])

    def test_bound_collective_reads_its_own_and_the_hub_not_a_peer(self):
        redis = _FakeRedis(); self._seed(redis)
        out = M.read_hot_cache(redis, "alice-dev", "analyst", "slack#C1", 10, hub_collective_id="enterprise")
        assert "alice's own" in out and "hub lesson" in out
        assert "bob's shared lesson" not in out                      # never a peer's shared tier

    def test_unbound_collective_does_not_read_the_hub(self):
        redis = _FakeRedis(); self._seed(redis)
        out = M.read_hot_cache(redis, "alice-dev", "analyst", "slack#C1", 10)
        assert out == ["alice's own"]

    def test_hub_never_reads_itself_twice(self):
        redis = _FakeRedis(); self._seed(redis)
        out = M.read_hot_cache(redis, "enterprise", "analyst", M.HUB_TIER, 10, hub_collective_id="enterprise")
        assert out.count("hub lesson") == 1


# ---------------------------------------------------------------------------
# 3. the information rule
# ---------------------------------------------------------------------------


class TestCeiling:
    def test_note_ceiling_is_the_highest_source(self):
        eps = [{"payload_json": json.dumps({"requester_tier": "requester", "requester_ceiling": "MEDIUM"})},
               {"payload_json": json.dumps({"requester_tier": "requester", "requester_ceiling": "LOW"})}]
        assert M.note_ceiling(eps) == "MEDIUM"
        eps.append({"payload_json": json.dumps({"requested_by": "tui"})})   # unattributed = operator
        assert M.note_ceiling(eps) == "CRITICAL"
        assert M.note_ceiling([]) == ""

    @pytest.mark.asyncio
    async def test_consolidate_stamps_the_ceiling(self):
        llm = MagicMock()
        llm.complete = AsyncMock(return_value={"content": "Big PDFs exhaust the ingester."})
        llm.embed = AsyncMock(return_value=[0.1] * 4)
        emb = [1.0, 0.0, 0.0, 0.0]
        eps = [{"id": f"e{i}", "embedding": emb, "scope": "slack#C1", "signal_type": "TASK_ASSIGN",
                "requester": f"slack:U{i}", "payload_json": json.dumps({"content": "pdf", "requester_ceiling": c})}
               for i, c in enumerate(["LOW", "MEDIUM"])]
        (note,) = await M.consolidate("a1", "analyst", eps, llm, min_cluster=2)
        assert note.ceiling == "MEDIUM"

    def test_cache_entries_carry_id_people_and_ceiling(self):
        redis = _FakeRedis()
        M.write_hot_cache(redis, "c", "analyst", [_note()])
        (entry,) = M.list_notes(redis, "c", "analyst", "slack#C1")
        assert entry["note_id"] and entry["ceiling"] == "MEDIUM"
        assert entry["source_requesters"] == ["slack:U1", "slack:U2"]

    def test_reader_below_the_ceiling_never_sees_the_note(self):
        redis = _FakeRedis()
        M.write_hot_cache(redis, "c", "analyst", [_note(summary="medium note", ceiling="MEDIUM"),
                                                   _note(summary="low note", ceiling="LOW")])
        M.publish_note(redis, "enterprise", "analyst", "critical hub note", M.HUB_TIER, ceiling="CRITICAL")
        low = M.read_hot_cache(redis, "c", "analyst", "slack#C1", 10, reader_ceiling="LOW", hub_collective_id="enterprise")
        assert low == ["low note"]
        medium = M.read_hot_cache(redis, "c", "analyst", "slack#C1", 10, reader_ceiling="MEDIUM", hub_collective_id="enterprise")
        assert set(medium) == {"low note", "medium note"}
        operator = M.read_hot_cache(redis, "c", "analyst", "slack#C1", 10, reader_ceiling="", hub_collective_id="enterprise")
        assert set(operator) == {"low note", "medium note", "critical hub note"}

    def test_a_cache_written_before_ceilings_reads_as_critical(self):
        redis = _FakeRedis()
        redis.set(redis_memory_notes_key("c", "analyst", "local"), json.dumps(["old bare note"]))
        assert M.read_hot_cache(redis, "c", "analyst", "local", 10, reader_ceiling="HIGH") == []
        assert M.read_hot_cache(redis, "c", "analyst", "local", 10) == ["old bare note"]


# ---------------------------------------------------------------------------
# 4. erasure reaches the hub
# ---------------------------------------------------------------------------


class _FakeVector:
    def __init__(self, episodes, notes):
        self.tables = {"episodes": episodes, "memory_notes": notes}

    def rows(self, table):
        return list(self.tables[table])

    def delete_where(self, table, predicate):
        self.tables[table] = [r for r in self.tables[table] if r["id"] not in predicate]

    def replace_row(self, table, row_id, row):
        self.tables[table] = [row if r["id"] == row_id else r for r in self.tables[table]]

    def insert(self, table, records):
        self.tables.setdefault(table, []).extend(records)


def test_forget_unpublishes_from_the_hub():
    redis = _FakeRedis()
    M.publish_note(redis, "enterprise", "analyst", "the lesson", M.HUB_TIER, ceiling="MEDIUM",
                   source_requesters=["slack:U1", "slack:U2"])
    M.publish_note(redis, "alice-dev", "analyst", "the lesson", "slack#C9")
    episodes = [{"id": "e1", "requester": "slack:U1", "scope": "slack#C1", "payload_json": "{}"},
                {"id": "e2", "requester": "slack:U2", "scope": "slack#C1", "payload_json": "{}"}]
    notes = [{"id": "n1", "summary": "the lesson", "role_label": "analyst", "scope": "slack#C1",
              "tier": M.TIER_SHARED, "source_ids": json.dumps(["e1", "e2"]),
              "source_requesters": json.dumps(["slack:U1", "slack:U2"])}]
    report = F.forget_person(_FakeVector(episodes, notes), "slack:U1", redis_client=redis,
                             collective_id="alice-dev", k=2, dry_run=False, hub_collective_id="enterprise")
    assert report.notes_demoted == 1
    assert set(report.unpublished_from) == {"slack#C9", "hub:enterprise"}
    assert json.loads(redis.store[redis_shared_notes_key("enterprise", "analyst", M.HUB_TIER)]) == []


# ---------------------------------------------------------------------------
# 5. the curator's look
# ---------------------------------------------------------------------------


def test_curate_finds_qualifying_shared_notes_and_skips_the_rest():
    redis = _FakeRedis()
    M.publish_note(redis, "alice-dev", "analyst", "two people agree", "slack#C1", ceiling="MEDIUM",
                   source_requesters=["slack:U1", "slack:U2"], note_id="n1")
    M.publish_note(redis, "bob-dev", "analyst", "one person's account", "slack#C2", ceiling="LOW",
                   source_requesters=["slack:U3"])
    M.publish_note(redis, "carol-dev", "analyst", "already in the hub", "slack#C3", ceiling="LOW",
                   source_requesters=["slack:U4", "slack:U5"])
    M.publish_note(redis, "enterprise", "analyst", "already in the hub", M.HUB_TIER, ceiling="LOW")
    M.publish_note(redis, "enterprise", "analyst", "hub-only note", M.HUB_TIER, ceiling="LOW",
                   source_requesters=["slack:U6", "slack:U7"])
    found = C.candidates(redis, "enterprise")
    assert [(c.collective_id, c.summary) for c in found] == [("alice-dev", "two people agree")]
    c = found[0]
    assert c.people == 2 and c.ceiling == "MEDIUM" and c.note_id == "n1" and c.role_label == "analyst"
    p = build_publish_proposal(c.note(), M.hub_destination("enterprise"), collective_id="alice-dev")
    assert p.params["destination_scope"] == "hub:enterprise" and p.params["ceiling"] == "MEDIUM"


def test_curate_respects_probation(monkeypatch):
    monkeypatch.setattr(C, "PROBATION_S", 900.0)
    redis = _FakeRedis()
    M.publish_note(redis, "alice-dev", "analyst", "fresh", "slack#C1", source_requesters=["slack:U1", "slack:U2"])
    assert C.candidates(redis, "enterprise") == []
    assert len(C.candidates(redis, "enterprise", now=time.time() + 1000)) == 1


def test_curate_can_be_limited_to_bound_collectives():
    redis = _FakeRedis()
    for cid in ("alice-dev", "stranger"):
        M.publish_note(redis, cid, "analyst", f"{cid} lesson", "slack#C1", source_requesters=["slack:U1", "slack:U2"])
    assert [c.collective_id for c in C.candidates(redis, "enterprise", collectives=["alice-dev"])] == ["alice-dev"]


# ---------------------------------------------------------------------------
# 6. the memory CLI opens the vector backend it is configured with
# ---------------------------------------------------------------------------


def test_memory_cli_backends_use_the_real_config_attribute(monkeypatch, capsys):
    """``cfg.vector.path`` never existed; ``memory forget`` refused every erasure
    with "no vector backend" until this was read from ``vector_db.lancedb_path``."""
    from types import SimpleNamespace
    from acc.cli import memory_cmd

    opened = {}

    class _Lance:
        def __init__(self, path):
            opened["path"] = path

    cfg = SimpleNamespace(vector_db=SimpleNamespace(lancedb_path="/tmp/lance-x"),
                          agent=SimpleNamespace(role="analyst", collective_id="c", hub_collective_id=""))
    monkeypatch.setattr("acc.config.load_config", lambda: cfg)
    monkeypatch.setattr("acc.backends.vector_lancedb.LanceDBBackend", _Lance)
    monkeypatch.setattr("acc.agent._build_redis_client", lambda c: None)
    got_cfg, vector, redis_client = memory_cmd._backends()
    assert opened["path"] == "/tmp/lance-x" and isinstance(vector, _Lance)
    assert capsys.readouterr().out == ""                       # nothing on stdout
