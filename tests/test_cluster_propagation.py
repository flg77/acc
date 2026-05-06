"""Cluster identity + propagation tests (PR-1 — foundation for the
estimator-and-spawn work in PR-2 and the TUI cluster panel in PR-4).

Covers three layers:

* :mod:`acc.cluster` — dataclass invariants, registry round-trip,
  list/lookup/unregister contracts, sync/async lookup mix.
* :mod:`acc.plan` — ``cluster_id`` field is attached to TASK_ASSIGN
  *only* when the publisher passes it; legacy single-agent payloads
  remain byte-identical so existing receivers are untouched.
* :mod:`acc.tui.client.NATSObserver` — per-cluster listener registry
  fans TASK_PROGRESS + TASK_COMPLETE events out by ``cluster_id``,
  isolates exceptions, and respects the back-compat
  ``cluster_id``-omitted path.

The point of this test module is *invariants*, not feature behaviour:
PR-1 ships zero user-visible work, but every following PR depends on
the wire-protocol guarantees pinned here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from acc.cluster import (
    ClusterPlan,
    _reset_for_tests,
    configure_redis_mirror,
    fetch_cluster_async,
    list_clusters,
    lookup_cluster,
    new_cluster_id,
    register_cluster,
    unregister_cluster,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Each test sees a clean in-memory registry + no Redis mirror.

    The module-global registry is convenient for production callers
    (one canonical view for arbiter / TUI / sub-agent) but would leak
    state across tests without this teardown.
    """
    _reset_for_tests()
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# ClusterPlan dataclass invariants
# ---------------------------------------------------------------------------


def test_cluster_id_format_is_prefixed_uuid():
    """Identifiers carry a ``c-`` prefix so log lines / dashboards can
    discriminate cluster_id from task_id (``plan-…``) and agent_id
    (``<role>-<hex>``) without a schema lookup."""
    cid = new_cluster_id()
    assert cid.startswith("c-")
    # uuid4().hex is 32 chars; full id therefore 34.
    assert len(cid) == 34
    # Two consecutive ids never collide (smoke test for randomness).
    assert new_cluster_id() != new_cluster_id()


def test_subagent_count_must_be_at_least_one():
    """Zero / negative member counts make no operational sense and
    would silently bypass the spawn loop in PR-2.  Reject at construction."""
    with pytest.raises(ValueError, match="subagent_count"):
        ClusterPlan(
            cluster_id=new_cluster_id(),
            target_role="coding_agent",
            subagent_count=0,
            parent_task_id="t-1",
        )
    with pytest.raises(ValueError, match="subagent_count"):
        ClusterPlan(
            cluster_id=new_cluster_id(),
            target_role="coding_agent",
            subagent_count=-3,
            parent_task_id="t-1",
        )


def test_difficulty_must_stay_in_unit_interval():
    """The estimator emits a normalised score; values outside [0, 1]
    would break the colour-tint mapping in PR-4 and indicate a buggy
    estimator that should fail loudly rather than render misleading UI."""
    with pytest.raises(ValueError, match="estimated_difficulty"):
        ClusterPlan(
            cluster_id=new_cluster_id(),
            target_role="coding_agent",
            subagent_count=2,
            parent_task_id="t-1",
            estimated_difficulty=1.5,
        )
    with pytest.raises(ValueError, match="estimated_difficulty"):
        ClusterPlan(
            cluster_id=new_cluster_id(),
            target_role="coding_agent",
            subagent_count=2,
            parent_task_id="t-1",
            estimated_difficulty=-0.01,
        )


def test_to_from_dict_round_trips_all_fields():
    """Wire round-trip must preserve every field — version skew should
    only ever drop *new* fields the receiver doesn't yet know about,
    never lose ones both sides understand."""
    original = ClusterPlan(
        cluster_id="c-deadbeef",
        target_role="coding_agent",
        subagent_count=4,
        parent_task_id="plan-x-step1-aabb1122",
        skill_mix=["code_review", "test_generation"],
        estimated_difficulty=0.62,
        reason="4200 tokens, +1 security keyword",
        created_at=1234567.5,
    )
    rebuilt = ClusterPlan.from_dict(original.to_dict())
    assert rebuilt == original


def test_from_dict_tolerates_missing_optional_fields():
    """A pre-PR-1 publisher (or a future stripped-down variant) should
    still hydrate a usable plan when only the minimum identifying
    fields are present.  Fields default to safe empty values."""
    minimal = {
        "cluster_id": "c-min",
        "target_role": "coding_agent",
        "subagent_count": 1,
        "parent_task_id": "t-1",
    }
    plan = ClusterPlan.from_dict(minimal)
    assert plan.skill_mix == []
    assert plan.reason == ""
    assert plan.estimated_difficulty == 0.0


# ---------------------------------------------------------------------------
# In-memory registry
# ---------------------------------------------------------------------------


def test_register_and_lookup_round_trip():
    plan = ClusterPlan(
        cluster_id=new_cluster_id(),
        target_role="coding_agent",
        subagent_count=3,
        parent_task_id="t-1",
    )
    register_cluster(plan)
    assert lookup_cluster(plan.cluster_id) is plan


def test_lookup_unknown_returns_none():
    """Misses must NOT raise — callers (TUI panel) treat None as
    'not yet registered' and may retry via fetch_cluster_async."""
    assert lookup_cluster("c-nonexistent") is None


def test_unregister_drops_from_registry():
    plan = ClusterPlan(
        cluster_id=new_cluster_id(),
        target_role="coding_agent",
        subagent_count=2,
        parent_task_id="t-1",
    )
    register_cluster(plan)
    unregister_cluster(plan.cluster_id)
    assert lookup_cluster(plan.cluster_id) is None


def test_unregister_unknown_is_idempotent():
    """Idempotency matters — the arbiter cancels and unregisters from
    multiple paths (timeout, /cluster kill, panel auto-evict).  Any one
    of them being a second arrival must be a no-op, not an exception."""
    unregister_cluster("c-never-existed")  # must not raise


def test_list_clusters_returns_snapshot():
    """``list_clusters`` is used by the TUI on startup to backfill the
    panel; it must return a snapshot (mutation-safe) of all current
    plans, not a live view."""
    plan_a = ClusterPlan(
        cluster_id="c-aaa", target_role="coding_agent",
        subagent_count=1, parent_task_id="t-1",
    )
    plan_b = ClusterPlan(
        cluster_id="c-bbb", target_role="coding_agent",
        subagent_count=2, parent_task_id="t-2",
    )
    register_cluster(plan_a)
    register_cluster(plan_b)
    snapshot = list_clusters()
    assert {p.cluster_id for p in snapshot} == {"c-aaa", "c-bbb"}
    # Snapshot is independent of subsequent mutations.
    unregister_cluster("c-aaa")
    assert {p.cluster_id for p in snapshot} == {"c-aaa", "c-bbb"}


@pytest.mark.asyncio
async def test_fetch_cluster_async_local_cache_hit():
    """Async lookup hits the local cache without touching Redis when
    the entry is registered locally.  This is the hot path for any
    process that witnessed the spawn — most TUI lookups."""
    plan = ClusterPlan(
        cluster_id="c-async-1",
        target_role="coding_agent",
        subagent_count=1,
        parent_task_id="t-1",
    )
    register_cluster(plan)
    fetched = await fetch_cluster_async(plan.cluster_id)
    assert fetched is plan


@pytest.mark.asyncio
async def test_fetch_cluster_async_cache_miss_no_redis_returns_none():
    """When the cluster isn't local AND no Redis mirror is configured
    (typical edge / disconnected scenario), the async lookup returns
    None instead of hanging or raising.  Edge stays operational."""
    configure_redis_mirror(None)  # explicit detach
    assert await fetch_cluster_async("c-not-here") is None


# ---------------------------------------------------------------------------
# acc.plan — TASK_ASSIGN cluster_id propagation
# ---------------------------------------------------------------------------


class _RecordingPublisher:
    """Captures every (subject, payload) pair :meth:`PlanExecutor._publish`
    would have sent to NATS.  Decouples the test from a real backend."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def __call__(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, json.loads(payload.decode())))


def _build_router(publisher: _RecordingPublisher):
    """Construct a minimal :class:`acc.plan.PlanExecutor` with the
    publisher monkey-patched in.  Reaches past public init via
    ``__new__`` to avoid pulling a NATS connection / event loop into
    tests — :meth:`_publish_task_assign` only consumes ``self._publish``
    and ``self._arbiter_id``, both swapped in below.
    """
    from acc.plan import PlanExecutor  # deferred — avoid module side-effects

    router = PlanExecutor.__new__(PlanExecutor)
    # _publish is the bound method called by _publish_task_assign — we
    # replace it with the recording stub so we can inspect the payload.
    router._publish = publisher  # type: ignore[attr-defined]
    router._arbiter_id = "arbiter-test"  # type: ignore[attr-defined]
    return router


def _make_step(role: str = "coding_agent", task_description: str = "do the thing"):
    """Construct a minimal :class:`acc.plan._Step` for the publisher test."""
    from acc.plan import _Step

    return _Step(
        step_id="step1",
        role=role,
        depends_on=[],
        raw={"task_description": task_description},
    )


def _make_plan(step):
    from acc.plan import _Plan

    return _Plan(
        plan_id="plan-test",
        collective_id="sol-01",
        steps={step.step_id: step},
        raw={},
    )


@pytest.mark.asyncio
async def test_task_assign_omits_cluster_id_by_default():
    """Single-agent legacy path: the publisher is called without a
    cluster_id, the wire payload must NOT contain the field.  This is
    the back-compat invariant — pre-PR-1 receivers stay happy."""
    pub = _RecordingPublisher()
    router = _build_router(pub)
    step = _make_step()
    plan = _make_plan(step)
    await router._publish_task_assign(plan, step, task_id="t-x")

    assert len(pub.published) == 1
    _, payload = pub.published[0]
    assert "cluster_id" not in payload
    assert "target_agent_id" not in payload  # same back-compat principle


@pytest.mark.asyncio
async def test_task_assign_attaches_cluster_id_when_supplied():
    """Cluster fan-out path: each TASK_ASSIGN carries the shared
    cluster_id verbatim so receivers can echo it back on every
    downstream signal."""
    pub = _RecordingPublisher()
    router = _build_router(pub)
    step = _make_step()
    plan = _make_plan(step)
    await router._publish_task_assign(
        plan, step,
        task_id="t-x",
        cluster_id="c-abc123",
        target_agent_id="coding_agent-deadbeef",
    )

    _, payload = pub.published[0]
    assert payload["cluster_id"] == "c-abc123"
    assert payload["target_agent_id"] == "coding_agent-deadbeef"


@pytest.mark.asyncio
async def test_task_assign_empty_cluster_id_treated_as_none():
    """Empty-string cluster_id (defensive — easy mistake when the
    estimator returns a placeholder) must NOT poison the payload.  We
    drop it so the receiver sees the legacy shape."""
    pub = _RecordingPublisher()
    router = _build_router(pub)
    step = _make_step()
    plan = _make_plan(step)
    await router._publish_task_assign(
        plan, step, task_id="t-x", cluster_id="", target_agent_id="",
    )

    _, payload = pub.published[0]
    assert "cluster_id" not in payload
    assert "target_agent_id" not in payload


# ---------------------------------------------------------------------------
# NATSObserver — per-cluster listener fan-out
# ---------------------------------------------------------------------------


def _build_observer():
    """Instantiate :class:`NATSObserver` without a NATS connection.

    The constructor only initialises in-memory state — no I/O — so
    tests can drive the routing handlers directly via the @handles
    dispatch table.
    """
    from acc.tui.client import NATSObserver

    queue: asyncio.Queue = asyncio.Queue()
    return NATSObserver(
        nats_url="nats://unused", collective_id="sol-01", update_queue=queue,
    )


def test_cluster_listener_fires_on_task_progress_with_cluster_id():
    obs = _build_observer()
    received: list[dict] = []
    obs.register_cluster_listener("c-xyz", received.append)

    obs._route_task_progress("coding_agent-1", {
        "signal_type": "TASK_PROGRESS",
        "task_id": "t-1",
        "agent_id": "coding_agent-1",
        "cluster_id": "c-xyz",
        "progress": {"current_step": 1, "total_steps_estimated": 3},
    })
    assert len(received) == 1
    assert received[0]["cluster_id"] == "c-xyz"


def test_cluster_listener_fires_on_task_complete_with_cluster_id():
    obs = _build_observer()
    received: list[dict] = []
    obs.register_cluster_listener("c-xyz", received.append)

    obs._route_task_complete("coding_agent-1", {
        "signal_type": "TASK_COMPLETE",
        "task_id": "t-1",
        "agent_id": "coding_agent-1",
        "cluster_id": "c-xyz",
        "blocked": False,
    })
    assert len(received) == 1
    assert received[0]["task_id"] == "t-1"


def test_cluster_listener_silent_on_payload_without_cluster_id():
    """Legacy traffic must never accidentally fire cluster listeners —
    that would muddle aggregation and produce phantom rows."""
    obs = _build_observer()
    received: list[dict] = []
    obs.register_cluster_listener("c-xyz", received.append)

    obs._route_task_progress("coding_agent-1", {
        "task_id": "t-1",
        "agent_id": "coding_agent-1",
        "progress": {"current_step": 1},
    })  # no cluster_id
    assert received == []


def test_cluster_listener_unregister_drops_callbacks():
    obs = _build_observer()
    received: list[dict] = []
    obs.register_cluster_listener("c-xyz", received.append)
    obs.unregister_cluster_listener("c-xyz")

    obs._route_task_complete("coding_agent-1", {
        "task_id": "t-1", "cluster_id": "c-xyz", "blocked": False,
    })
    assert received == []


def test_multiple_cluster_listeners_each_fire():
    """The panel + a future analytics sink may both register; both
    must see every event."""
    obs = _build_observer()
    a: list[dict] = []
    b: list[dict] = []
    obs.register_cluster_listener("c-xyz", a.append)
    obs.register_cluster_listener("c-xyz", b.append)

    obs._route_task_progress("coding_agent-1", {
        "task_id": "t-1", "cluster_id": "c-xyz",
        "progress": {"current_step": 1},
    })
    assert len(a) == 1
    assert len(b) == 1


def test_one_listener_exception_does_not_starve_others():
    """A buggy panel implementation must not silence telemetry.  The
    fan-out catches per-callback so the second registration still
    sees the event."""
    obs = _build_observer()

    def boom(_):
        raise RuntimeError("buggy listener")

    received: list[dict] = []
    obs.register_cluster_listener("c-xyz", boom)
    obs.register_cluster_listener("c-xyz", received.append)

    obs._route_task_complete("coding_agent-1", {
        "task_id": "t-1", "cluster_id": "c-xyz", "blocked": False,
    })
    assert len(received) == 1
