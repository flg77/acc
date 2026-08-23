"""Phase 2 of `20260823-attributed-memory` — scoping at retrieval.

Retrieval filtered on ``agent_id`` alone: the right axis for one operator, the
wrong one for two. These assert the new filter separates people who should not
see each other, keeps together the ones a shared room already puts together, and
changes nothing at all for a single operator.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.memory_scope import (
    ISOLATED,
    LOCAL_SCOPE,
    PER_GROUP,
    POOLED,
    resolve_mode,
    row_scope,
    scope_key,
)


def _slack(user, channel):
    return {"requested_by": f"slack:{user}@{channel}", "requester_source": "slack",
            "requester_scope": channel, "signal_type": "TASK_ASSIGN"}


# ---------------------------------------------------------------------------
# The scope key
# ---------------------------------------------------------------------------

def test_a_channel_is_one_context_shared_by_the_people_in_it():
    """Settled question 3. Two people in the same room share a memory, because
    an agent that has forgotten what everyone present can still scroll back to
    is protecting nothing."""
    assert scope_key(_slack("U1", "C1")) == scope_key(_slack("U2", "C1"))


def test_the_same_person_in_two_channels_has_two_memories():
    """Keyed on the channel, never on the participant set — otherwise a
    channel's memory would contain everything its members ever did anywhere."""
    assert scope_key(_slack("U1", "C1")) != scope_key(_slack("U1", "C2"))


def test_direct_messages_are_not_pooled_with_each_other():
    """A DM is not a room. Keying it on the group would put every private
    conversation on the platform into one memory."""
    dm1 = {"requested_by": "slack:U1", "requester_source": "slack",
           "requester_scope": "direct"}
    dm2 = {"requested_by": "slack:U2", "requester_source": "slack",
           "requester_scope": "direct"}
    assert scope_key(dm1) != scope_key(dm2)


def test_a_direct_message_is_not_pooled_with_a_channel():
    dm = {"requested_by": "slack:U1", "requester_source": "slack",
          "requester_scope": "direct"}
    assert scope_key(dm) != scope_key(_slack("U1", "C1"))


def test_unattended_ingress_is_isolated_per_caller():
    a = {"requested_by": "compat:A", "requester_source": "compat_endpoint"}
    b = {"requested_by": "compat:B", "requester_source": "compat_endpoint"}
    assert scope_key(a) != scope_key(b)
    assert scope_key(a) != LOCAL_SCOPE


def test_a_pooled_surface_shares_one_memory():
    one = {"requested_by": "local:alice", "requester_source": "tui"}
    two = {"requested_by": "local:bob", "requester_source": "tui"}
    assert scope_key(one) == scope_key(two) == "tui"


def test_an_unknown_surface_is_isolated_not_pooled():
    """The next adapter added is the one most likely to be missing from the
    table, and the failure that matters is its callers quietly sharing."""
    assert resolve_mode("some-new-messenger") == ISOLATED
    a = {"requested_by": "matrix:U1", "requester_source": "matrix"}
    b = {"requested_by": "matrix:U2", "requester_source": "matrix"}
    assert scope_key(a) != scope_key(b)


def test_the_source_is_recovered_from_requested_by_when_not_stamped():
    """Adapters do not all stamp the same keys. A missed source falls back to
    the local scope, which would pool external work with the operator's own."""
    assert scope_key({"requested_by": "webhook:deploy"}) != LOCAL_SCOPE


@pytest.mark.parametrize("payload", [None, {}, "not-a-dict", {"signal_type": "X"}])
def test_work_that_never_passed_admission_is_local(payload):
    assert scope_key(payload) == LOCAL_SCOPE


def test_row_without_a_scope_column_reads_as_local():
    """A pre-migration row matches the backfill, so old history stays where the
    operator can reach it and nowhere else."""
    assert row_scope({}) == LOCAL_SCOPE
    assert row_scope({"scope": None}) == LOCAL_SCOPE
    assert row_scope({"scope": "slack#C1"}) == "slack#C1"


def test_the_defaults_are_deliberately_not_uniform():
    """A single "multi-user mode" switch would be wrong for most surfaces."""
    assert resolve_mode("tui") == POOLED
    assert resolve_mode("slack") == PER_GROUP
    assert resolve_mode("subscription") == ISOLATED


# ---------------------------------------------------------------------------
# Retrieval — tasks [30] and [31]
# ---------------------------------------------------------------------------

def _core():
    from acc.cognitive_core import CognitiveCore
    core = CognitiveCore(
        agent_id="a1", collective_id="c", llm=MagicMock(), vector=MagicMock(),
        redis_client=None, role_label="analyst",
    )
    core._llm.embed = AsyncMock(return_value=[0.0] * 384)
    return core


def _stored(eid, scope, ts=None):
    import time
    return {"id": eid, "agent_id": "a1", "scope": scope,
            "ts": ts if ts is not None else time.time(),
            "signal_type": "TASK_ASSIGN", "payload_json": '{"content": "x"}'}


async def _retrieve(core, rows, scope):
    core._vector.search = MagicMock(return_value=rows)
    return await core._retrieve_episodes("q", MagicMock(), scope=scope)


@pytest.mark.asyncio
async def test_two_requesters_on_one_agent_cannot_read_each_other():
    """Task [30] — the leak this phase exists to close. Same agent, two people,
    nothing separating them before now."""
    core = _core()
    rows = [_stored("mine", "slack@U1"), _stored("theirs", "slack@U2")]

    got = await _retrieve(core, rows, "slack@U1")

    assert len(got) == 1, "one requester's episode leaked into another's prompt"


@pytest.mark.asyncio
async def test_a_channel_member_reads_the_channel_not_the_dms():
    core = _core()
    rows = [_stored("in-channel", "slack#C1"), _stored("a-dm", "slack@U1")]
    got = await _retrieve(core, rows, "slack#C1")
    assert len(got) == 1


@pytest.mark.asyncio
async def test_single_operator_retrieval_is_unchanged():
    """Task [31] — the regression that matters most, on the most-used path.

    Every episode a lone operator produces is local, and so is every
    pre-migration row, so the filter is a no-op and nothing is lost.
    """
    core = _core()
    rows = [_stored("a", LOCAL_SCOPE), _stored("b", LOCAL_SCOPE), _stored("legacy", None)]
    got = await _retrieve(core, rows, LOCAL_SCOPE)
    assert len(got) == 3


@pytest.mark.asyncio
async def test_an_attributed_context_never_sees_unattributed_history():
    """Task [10], enforced. Pre-attribution rows stay in the operator's scope
    rather than surfacing inside a channel."""
    core = _core()
    rows = [_stored("legacy", None), _stored("also-legacy", LOCAL_SCOPE)]
    assert await _retrieve(core, rows, "slack#C1") == []


@pytest.mark.asyncio
async def test_a_sibling_agent_is_still_filtered_out():
    """The pre-existing agent_id filter still holds; the scope is an additional
    axis, not a replacement."""
    core = _core()
    rows = [dict(_stored("sibling", LOCAL_SCOPE), agent_id="a2")]
    assert await _retrieve(core, rows, LOCAL_SCOPE) == []


# ---------------------------------------------------------------------------
# The write path
# ---------------------------------------------------------------------------

def test_the_episode_is_written_with_its_scope():
    """The mode is applied here, not at read time — so retrieval keeps one
    equality test and a mode cannot be almost-applied."""
    core = _core()
    core._persist_episode([0.0] * 384, _slack("U1", "C1"), {})
    (table, rows), _ = core._vector.insert.call_args
    assert table == "episodes"
    assert rows[0]["scope"] == "slack#C1"


def test_a_fresh_database_has_the_scope_column(tmp_path):
    pytest.importorskip("lancedb")
    from acc.backends.vector_lancedb import LanceDBBackend
    backend = LanceDBBackend(str(tmp_path / "fresh"))
    assert "scope" in set(backend._db.open_table("episodes").schema.names)

@pytest.mark.asyncio
async def test_a_crowded_neighbouring_scope_does_not_starve_the_asker():
    """Both filters run AFTER the vector search, so a busy channel could fill
    the top-k and leave the requester with nothing — which reads as "the agent
    forgot", not as a partition working. Over-fetching bounds that."""
    core = _core()
    rows = [_stored(f"theirs-{i}", "slack#busy") for i in range(8)]
    rows.append(_stored("mine", "slack#quiet"))

    got = await _retrieve(core, rows, "slack#quiet")

    assert len(got) == 1, "the asker's own episode was crowded out"
    (_, _, requested_k), _ = core._vector.search.call_args
    assert requested_k > 5, "no over-fetch: the filters would thin below top_k"


@pytest.mark.asyncio
async def test_retrieval_still_returns_at_most_top_k():
    """Over-fetching must not widen what reaches the prompt."""
    core = _core()
    rows = [_stored(f"e{i}", LOCAL_SCOPE) for i in range(20)]
    got = await _retrieve(core, rows, LOCAL_SCOPE)
    assert len(got) == 5
