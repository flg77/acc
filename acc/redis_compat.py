"""Sync/async Redis compatibility shim.

The agent's :func:`acc.agent._build_redis_client` returns a SYNC
``redis.Redis`` (built via ``redis.from_url``).  Some ACC modules
were authored against ``redis.asyncio`` and call ``await client.X(...)``
— mixing the two produced runtime errors of the form::

    object set can't be used in 'await' expression

on every heartbeat as soon as the Redis op returned a plain (sync)
result.  This module provides a single helper, :func:`call_redis`,
that detects which client kind it has and adapts at call time:

* Async client → the underlying method returns a coroutine; awaited.
* Sync client  → the method returns the result directly; passed through.

Cost on the sync path: one ``iscoroutine`` check per Redis op + the
sync call blocks the asyncio event loop for the duration of the
Redis IO.  Oversight + scratchpad ops are infrequent (a few per
minute per agent in practice), so the loop-block cost is negligible.
Switching to ``asyncio.to_thread`` would hide the block but adds a
thread-pool hop on every call — disproportionate for the throughput
we see.

When the agent factory is migrated to ``redis.asyncio`` in a future
PR, this shim continues to work without modification — every call
site already does ``await call_redis(...)``.

Usage::

    from acc.redis_compat import call_redis

    raw = await call_redis(self._redis.get, key)
    await call_redis(self._redis.set, key, value, ex=ttl)
"""

from __future__ import annotations

import asyncio


async def call_redis(method, *args, **kwargs):
    """Invoke a Redis op against either a sync or async client.

    Args:
        method: Bound method on the Redis client (e.g. ``client.smembers``).
        *args / **kwargs: Forwarded to ``method``.

    Returns:
        The op's result.  For async clients we ``await`` the returned
        coroutine; for sync clients we return the result as-is.

    Raises:
        Whatever ``method`` would raise — error handling is the
        caller's responsibility (every caller in oversight + scratchpad
        already wraps in a try/except around the Redis op).
    """
    result = method(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result
