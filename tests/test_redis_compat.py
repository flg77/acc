"""Tests for the sync/async Redis compatibility shim.

Pinpoints the bug surfaced on acc1 during the live stack smoke-test:

    ERROR acc.oversight oversight: Redis pending query failed:
    object set can't be used in 'await' expression

Root cause: ``acc.agent._build_redis_client`` returns a SYNC
``redis.Redis``; ``acc.oversight`` + ``acc.scratchpad`` were authored
to ``await client.X(...)``.  The mismatch only fired when the
oversight queue tried to read.

Fix: every Redis call in those modules now goes through
``acc.redis_compat.call_redis``, which inspects the return value
and awaits only when it's a coroutine (async client).

These tests mock both client kinds and verify each module's hot path
works against either.  No real Redis required.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.redis_compat import call_redis


# ---------------------------------------------------------------------------
# Sync + async fakes
# ---------------------------------------------------------------------------


class _SyncRedisFake:
    """Mimics the surface of a sync ``redis.Redis`` client.

    Methods return values directly (no coroutines).  Only the methods
    used by ``oversight`` + ``scratchpad`` are implemented.
    """

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set] = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = value
        return True

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.kv:
                del self.kv[k]
                n += 1
        return n

    def expire(self, key, seconds):
        return key in self.kv

    def expireat(self, key, ts):
        return key in self.kv

    def sadd(self, key, *vals):
        self.sets.setdefault(key, set()).update(vals)
        return len(vals)

    def srem(self, key, *vals):
        s = self.sets.get(key, set())
        n = sum(1 for v in vals if v in s)
        s.difference_update(vals)
        return n

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def scan(self, cursor, match=None, count=100):
        return (0, [])


class _AsyncRedisFake:
    """Mimics ``redis.asyncio`` — every method returns a coroutine."""

    def __init__(self) -> None:
        self._sync = _SyncRedisFake()

    async def get(self, key):
        return self._sync.get(key)

    async def set(self, key, value, ex=None):
        return self._sync.set(key, value, ex=ex)

    async def delete(self, *keys):
        return self._sync.delete(*keys)

    async def expire(self, key, seconds):
        return self._sync.expire(key, seconds)

    async def expireat(self, key, ts):
        return self._sync.expireat(key, ts)

    async def sadd(self, key, *vals):
        return self._sync.sadd(key, *vals)

    async def srem(self, key, *vals):
        return self._sync.srem(key, *vals)

    async def smembers(self, key):
        return self._sync.smembers(key)

    async def scan(self, cursor, match=None, count=100):
        return self._sync.scan(cursor, match=match, count=count)


# ---------------------------------------------------------------------------
# call_redis isolated tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_redis_with_sync_client_returns_value_directly():
    """Sync method → no coroutine, value flows through unchanged."""
    client = _SyncRedisFake()
    client.kv["k"] = "v"
    out = await call_redis(client.get, "k")
    assert out == "v"


@pytest.mark.asyncio
async def test_call_redis_with_async_client_awaits_the_coroutine():
    """Async method → coroutine, awaited transparently."""
    client = _AsyncRedisFake()
    await call_redis(client.set, "k", "v")
    out = await call_redis(client.get, "k")
    assert out == "v"


@pytest.mark.asyncio
async def test_call_redis_propagates_kwargs():
    """Kwargs (ex= for set, count= for scan) survive the dispatch."""
    sync = _SyncRedisFake()
    await call_redis(sync.set, "k", "v", ex=60)
    assert sync.kv["k"] == "v"

    async_ = _AsyncRedisFake()
    cursor, keys = await call_redis(async_.scan, 0, match="*", count=50)
    assert cursor == 0 and keys == []


@pytest.mark.asyncio
async def test_call_redis_does_not_swallow_exceptions():
    """Exceptions from the underlying op surface unchanged."""
    class _ExplodingClient:
        def get(self, key):
            raise RuntimeError("redis went away")

    with pytest.raises(RuntimeError, match="redis went away"):
        await call_redis(_ExplodingClient().get, "anything")


# ---------------------------------------------------------------------------
# HumanOversightQueue end-to-end with sync + async clients
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversight_pending_works_against_sync_client():
    """Reproduces the exact acc1 bug — pending() against sync Redis.

    Before the fix this raised:
        TypeError: object set can't be used in 'await' expression
    """
    from acc.oversight import HumanOversightQueue, OversightItem

    client = _SyncRedisFake()
    queue = HumanOversightQueue(
        redis_client=client,
        collective_id="sol-test",
        timeout_s=300,
        agent_id="arbiter-x",
    )

    oid = await queue.submit(
        task_id="task-1", risk_level="HIGH",
        summary="needs review", role_id="analyst",
    )
    assert oid

    pending = await queue.pending()
    assert len(pending) == 1
    assert isinstance(pending[0], OversightItem)
    assert pending[0].oversight_id == oid

    # Approve + verify it leaves the pending list.
    await queue.approve(oid, approver_id="reviewer-1")
    assert await queue.pending() == []
    assert await queue.pending_count() == 0


@pytest.mark.asyncio
async def test_oversight_pending_works_against_async_client():
    """Same flow, async client — confirms forward-compat."""
    from acc.oversight import HumanOversightQueue

    client = _AsyncRedisFake()
    queue = HumanOversightQueue(
        redis_client=client,
        collective_id="sol-test",
        timeout_s=300,
        agent_id="arbiter-x",
    )

    oid = await queue.submit(
        task_id="task-2", risk_level="CRITICAL",
        summary="critical action", role_id="coding_agent",
    )
    pending = await queue.pending()
    assert [p.oversight_id for p in pending] == [oid]

    await queue.reject(oid, approver_id="reviewer-1", reason="risky")
    assert await queue.pending() == []


# ---------------------------------------------------------------------------
# ScratchpadClient end-to-end with sync + async clients
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scratchpad_set_get_works_against_sync_client():
    from acc.scratchpad import ScratchpadClient

    client = _SyncRedisFake()
    sc = ScratchpadClient(
        redis_client=client,
        collective_id="sol-test",
        role="analyst",
    )
    sc.register_plan("plan-1", 600)

    await sc.set("plan-1", "intermediate", "value-A")
    out = await sc.get("plan-1", "analyst", "intermediate")
    assert out == "value-A"

    await sc.delete("plan-1", "intermediate")
    assert await sc.get("plan-1", "analyst", "intermediate") is None


@pytest.mark.asyncio
async def test_scratchpad_set_get_works_against_async_client():
    from acc.scratchpad import ScratchpadClient

    client = _AsyncRedisFake()
    sc = ScratchpadClient(
        redis_client=client,
        collective_id="sol-test",
        role="analyst",
    )
    sc.register_plan("plan-2", 600)

    await sc.set_json("plan-2", "outline", {"x": 1, "y": 2})
    out = await sc.get_json("plan-2", "analyst", "outline")
    assert out == {"x": 1, "y": 2}
