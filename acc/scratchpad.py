"""ACC ScratchpadClient — per-task shared state over Redis (ACC-10).

The scratchpad provides a short-lived shared workspace for agents collaborating
on a PLAN.  Unlike ICL episodes (permanent) or NATS messages (transient), the
scratchpad persists for the duration of the plan and is readable by all roles
within the collective.

Key pattern
-----------
``acc:{collective_id}:scratchpad:{plan_id}:{role}:{key}``

Access semantics
----------------
* **Write**: a role may only write to its own namespace (``{role}`` segment
  must match the publishing agent's role).
* **Read**: cross-role reads are unrestricted by design — the scratchpad exists
  to share intermediate state between analyst and synthesizer, for example.

TTL management
--------------
TTL is set at PLAN creation time and stored once via ``EXPIREAT``.  The
:meth:`ScratchpadClient.set_plan_ttl` method sets the TTL for all current and
*future* keys in the plan via a single Redis ``EXPIREAT`` on each write.

When the plan completes or is cancelled the arbiter calls
:meth:`ScratchpadClient.flush_plan` which deletes all keys matching
``acc:{cid}:scratchpad:{plan_id}:*`` (SCAN + DEL pipeline).

Usage::

    from acc.scratchpad import ScratchpadClient

    async with ScratchpadClient(
        redis_client=redis,
        collective_id="sol-01",
        role="analyst",
    ) as sc:
        await sc.set("plan-abc", "intermediate_result", '{"tokens": [1, 2, 3]}')
        value = await sc.get("plan-abc", "analyst", "intermediate_result")
        # Other roles can read across namespaces
        other = await sc.get("plan-abc", "synthesizer", "summary")
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from acc.signals import redis_scratchpad_key

logger = logging.getLogger("acc.scratchpad")

# Default upper bound on scratchpad lifetime when no explicit TTL is given
_DEFAULT_TTL_S = 7200  # 2 hours — matches max_scratchpad_ttl_s setpoint default


class ScratchpadAccessError(Exception):
    """Raised when a role attempts to write to another role's namespace."""


class ScratchpadClient:
    """Per-task shared state client backed by Redis.

    Args:
        redis_client: An ``redis.asyncio`` client (or compatible async Redis
            client).  Must be connected before use.
        collective_id: The collective this agent belongs to.
        role: The role of the agent using this client.  Used for write-access
            enforcement — the client can only write to its own namespace.
        max_ttl_s: Upper bound on scratchpad lifetime in seconds.  Defaults to
            ``_DEFAULT_TTL_S`` (2 hours).  Should be set from the
            ``max_scratchpad_ttl_s`` Cat-B setpoint at runtime.
    """

    def __init__(
        self,
        redis_client: Any,
        collective_id: str,
        role: str,
        max_ttl_s: int = _DEFAULT_TTL_S,
    ) -> None:
        self._redis = redis_client
        self._collective_id = collective_id
        self._role = role
        self._max_ttl_s = max_ttl_s
        # plan_id → UNIX epoch expiry time (seconds)
        self._plan_expiry: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Context manager helpers (optional convenience)
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ScratchpadClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass  # Redis client lifecycle managed by the caller

    # ------------------------------------------------------------------
    # TTL management
    # ------------------------------------------------------------------

    def register_plan(self, plan_id: str, ttl_s: int) -> None:
        """Register a plan's TTL so that subsequent writes use it.

        Call this when the agent receives a PLAN signal.

        Args:
            plan_id: The plan identifier from the PLAN signal payload.
            ttl_s: Desired TTL in seconds.  Clamped to ``max_ttl_s``.
        """
        effective = min(ttl_s, self._max_ttl_s)
        self._plan_expiry[plan_id] = int(time.time()) + effective
        logger.debug(
            "Scratchpad plan %s registered with TTL %ds (expires at %d)",
            plan_id,
            effective,
            self._plan_expiry[plan_id],
        )

    def _expiry_for(self, plan_id: str) -> int:
        """Return absolute expiry epoch for *plan_id*, defaulting to max_ttl_s."""
        return self._plan_expiry.get(
            plan_id, int(time.time()) + self._max_ttl_s
        )

    # ------------------------------------------------------------------
    # Write (own namespace only)
    # ------------------------------------------------------------------

    async def set(
        self,
        plan_id: str,
        key: str,
        value: str,
        *,
        role: Optional[str] = None,
    ) -> None:
        """Write *value* to the scratchpad under *key* in this role's namespace.

        Args:
            plan_id: The plan this key belongs to.
            key: Arbitrary string key within this role's namespace.
            value: String value (callers are responsible for serialisation,
                e.g. ``json.dumps(obj)``).
            role: Override the write role.  Defaults to ``self._role``.
                Passing a different role raises :class:`ScratchpadAccessError`.

        Raises:
            ScratchpadAccessError: If *role* does not match this client's role.
        """
        write_role = role or self._role
        if write_role != self._role:
            raise ScratchpadAccessError(
                f"Role '{self._role}' cannot write to namespace '{write_role}'"
            )
        redis_key = redis_scratchpad_key(self._collective_id, plan_id, write_role, key)
        expiry = self._expiry_for(plan_id)
        if self._redis is not None:
            await self._redis.set(redis_key, value)
            await self._redis.expireat(redis_key, expiry)
            logger.debug("Scratchpad SET %s (expires at %d)", redis_key, expiry)

    async def set_json(
        self,
        plan_id: str,
        key: str,
        obj: Any,
        *,
        role: Optional[str] = None,
    ) -> None:
        """Serialise *obj* to JSON and write to the scratchpad.

        Convenience wrapper around :meth:`set`.
        """
        await self.set(plan_id, key, json.dumps(obj), role=role)

    # ------------------------------------------------------------------
    # Read (cross-role)
    # ------------------------------------------------------------------

    async def get(
        self,
        plan_id: str,
        role: str,
        key: str,
    ) -> Optional[str]:
        """Read a value from any role's scratchpad namespace.

        Args:
            plan_id: The plan this key belongs to.
            role: The role whose namespace to read from.
            key: The key within that namespace.

        Returns:
            The stored string value, or ``None`` if not found or expired.
        """
        redis_key = redis_scratchpad_key(self._collective_id, plan_id, role, key)
        if self._redis is None:
            return None
        raw = await self._redis.get(redis_key)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def get_json(
        self,
        plan_id: str,
        role: str,
        key: str,
    ) -> Optional[Any]:
        """Read and JSON-deserialise a value from any role's namespace.

        Returns ``None`` if the key does not exist or the value is not valid JSON.
        """
        raw = await self.get(plan_id, role, key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Scratchpad get_json: invalid JSON at %s/%s/%s", plan_id, role, key)
            return None

    async def get_own(self, plan_id: str, key: str) -> Optional[str]:
        """Read a value from this client's own role namespace."""
        return await self.get(plan_id, self._role, key)

    async def get_all_for_plan(self, plan_id: str) -> dict[str, str]:
        """Return all scratchpad entries for *plan_id* across all roles.

        Keys in the result are formatted as ``"{role}/{key}"`` for readability.

        Note: Uses Redis SCAN which is O(N) — intended for debugging/observer
        use, not hot-path agent logic.
        """
        if self._redis is None:
            return {}
        pattern = f"acc:{self._collective_id}:scratchpad:{plan_id}:*"
        result: dict[str, str] = {}
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
            for raw_key in keys:
                k = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
                # Extract role/key from the full Redis key
                # Format: acc:{cid}:scratchpad:{plan_id}:{role}:{key}
                parts = k.split(":")
                # parts[4] = role, parts[5:] = key components
                if len(parts) >= 6:
                    role_part = parts[4]
                    key_part = ":".join(parts[5:])
                    # Use the decoded string key for the GET to avoid bytes/str mismatch
                    val = await self._redis.get(k)
                    if val is not None:
                        result[f"{role_part}/{key_part}"] = (
                            val.decode() if isinstance(val, bytes) else str(val)
                        )
            if cursor == 0:
                break
        return result

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, plan_id: str, key: str) -> None:
        """Delete a single key from this client's own role namespace."""
        redis_key = redis_scratchpad_key(self._collective_id, plan_id, self._role, key)
        if self._redis is not None:
            await self._redis.delete(redis_key)

    async def flush_plan(self, plan_id: str) -> int:
        """Delete all scratchpad entries for *plan_id* (SCAN + pipeline DEL).

        Called by the arbiter when a plan completes or is cancelled.

        Returns:
            Number of keys deleted.
        """
        if self._redis is None:
            return 0
        pattern = f"acc:{self._collective_id}:scratchpad:{plan_id}:*"
        deleted = 0
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=200)
            if keys:
                # Use multi-key DELETE (avoids pipeline context-manager complexity)
                result = await self._redis.delete(*keys)
                deleted += result if isinstance(result, int) else len(keys)
            if cursor == 0:
                break
        if deleted:
            logger.info("Scratchpad flushed %d keys for plan %s", deleted, plan_id)
        # Remove local TTL tracking
        self._plan_expiry.pop(plan_id, None)
        return deleted
