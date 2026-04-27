"""ACC Human Oversight Queue (ACC-12 / EU AI Act Art. 14).

High-risk tasks (EU AI Act Annex III risk level HIGH or UNACCEPTABLE) must be
submitted to a human oversight queue before the output is forwarded to
downstream agents.

Storage:
- **Redis backend** (production): per-item hash at ``acc:{cid}:oversight:{id}``
  with TTL = ``oversight_timeout_s``.  Pending list at ``acc:{cid}:oversight:pending``.
- **In-process fallback** (no Redis): ephemeral dict — items lost on restart.
  Logged at WARNING.

NATS subjects (for TUI / external approval clients):
- Submit notification:  ``acc.{cid}.oversight.pending``  payload = ``OversightItem``
- Approve:              ``acc.{cid}.oversight.{id}.approve``
- Reject:               ``acc.{cid}.oversight.{id}.reject``

Usage::

    queue = HumanOversightQueue(redis_client, collective_id, timeout_s=300)
    oversight_id = await queue.submit(task_id, "HIGH", "Analyst output requires review", "analyst")
    # ... wait ...
    items = await queue.pending()
    await queue.approve(oversight_id, "human-reviewer-01")
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional

logger = logging.getLogger("acc.oversight")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class OversightItem:
    """One item in the human oversight queue."""

    oversight_id: str
    task_id: str
    risk_level: str
    summary: str
    role_id: str
    agent_id: str
    submitted_at_ms: int
    timeout_ms: int
    status: str = "PENDING"  # PENDING | APPROVED | REJECTED | EXPIRED
    approver_id: str = ""
    rejection_reason: str = ""
    resolved_at_ms: int = 0


# ---------------------------------------------------------------------------
# HumanOversightQueue
# ---------------------------------------------------------------------------


class HumanOversightQueue:
    """Manages the human oversight queue for EU AI Act Art. 14 compliance.

    Args:
        redis_client:  Async Redis client.  Pass ``None`` for in-process mode.
        collective_id: Collective identifier used in Redis key namespace.
        timeout_s:     Seconds before an unresolved item is considered expired.
        agent_id:      Owning agent identifier (added to submitted items).
    """

    _KEY_ITEM = "acc:{cid}:oversight:{oid}"
    _KEY_PENDING_LIST = "acc:{cid}:oversight:pending"

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        collective_id: str = "sol-01",
        timeout_s: int = 300,
        agent_id: str = "",
    ) -> None:
        self._redis = redis_client
        self._cid = collective_id
        self._timeout_s = timeout_s
        self._agent_id = agent_id
        self._in_process: dict[str, OversightItem] = {}

        if redis_client is None:
            logger.warning(
                "oversight: no Redis configured — using in-process store. "
                "Items will be lost on restart."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(
        self,
        task_id: str,
        risk_level: str,
        summary: str,
        role_id: str,
    ) -> str:
        """Submit a task to the oversight queue.

        Args:
            task_id:    The task identifier from the TASK_ASSIGN payload.
            risk_level: EU AI Act risk level (HIGH | UNACCEPTABLE).
            summary:    Human-readable description of why oversight is needed.
            role_id:    The submitting agent's role label.

        Returns:
            ``oversight_id`` — UUID string identifying this oversight request.
        """
        oversight_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        item = OversightItem(
            oversight_id=oversight_id,
            task_id=task_id,
            risk_level=risk_level,
            summary=summary,
            role_id=role_id,
            agent_id=self._agent_id,
            submitted_at_ms=now_ms,
            timeout_ms=now_ms + (self._timeout_s * 1000),
        )

        await self._save(item)
        logger.warning(
            "oversight: submitted oversight_id=%s task_id=%s risk=%s",
            oversight_id,
            task_id,
            risk_level,
        )
        return oversight_id

    async def approve(self, oversight_id: str, approver_id: str) -> None:
        """Mark an oversight item as approved.

        Args:
            oversight_id: The oversight request ID returned by :meth:`submit`.
            approver_id:  Identifier of the human approver.
        """
        item = await self._load(oversight_id)
        if item is None:
            logger.warning("oversight: approve — item %s not found", oversight_id)
            return
        item.status = "APPROVED"
        item.approver_id = approver_id
        item.resolved_at_ms = int(time.time() * 1000)
        await self._save(item)
        await self._remove_from_pending(oversight_id)
        logger.info("oversight: approved oversight_id=%s approver=%s", oversight_id, approver_id)

    async def reject(
        self, oversight_id: str, approver_id: str, reason: str = ""
    ) -> None:
        """Mark an oversight item as rejected.

        Args:
            oversight_id: The oversight request ID.
            approver_id:  Identifier of the human reviewer.
            reason:       Optional rejection reason.
        """
        item = await self._load(oversight_id)
        if item is None:
            logger.warning("oversight: reject — item %s not found", oversight_id)
            return
        item.status = "REJECTED"
        item.approver_id = approver_id
        item.rejection_reason = reason
        item.resolved_at_ms = int(time.time() * 1000)
        await self._save(item)
        await self._remove_from_pending(oversight_id)
        logger.info("oversight: rejected oversight_id=%s reason=%s", oversight_id, reason)

    async def pending(self) -> list[OversightItem]:
        """Return all currently pending (unresolved) oversight items."""
        if self._redis is not None:
            try:
                return await self._pending_redis()
            except Exception as exc:
                logger.error("oversight: Redis pending query failed: %s", exc)

        return [
            item for item in self._in_process.values()
            if item.status == "PENDING"
        ]

    async def expire_timed_out(self) -> list[str]:
        """Mark timed-out items as EXPIRED and return their IDs.

        Called by the agent's heartbeat loop to detect unresponded requests.
        """
        now_ms = int(time.time() * 1000)
        items = await self.pending()
        expired: list[str] = []

        for item in items:
            if now_ms > item.timeout_ms:
                item.status = "EXPIRED"
                item.resolved_at_ms = now_ms
                await self._save(item)
                await self._remove_from_pending(item.oversight_id)
                expired.append(item.oversight_id)
                logger.warning(
                    "oversight: timeout expired oversight_id=%s task_id=%s",
                    item.oversight_id,
                    item.task_id,
                )

        return expired

    async def pending_count(self) -> int:
        """Return count of pending oversight items (for StressIndicators)."""
        return len(await self.pending())

    # ------------------------------------------------------------------
    # Private storage helpers
    # ------------------------------------------------------------------

    async def _save(self, item: OversightItem) -> None:
        key = self._KEY_ITEM.format(cid=self._cid, oid=item.oversight_id)
        value = json.dumps(asdict(item))

        if self._redis is not None:
            try:
                await self._redis.set(key, value, ex=self._timeout_s * 2)
                # Add to pending list if still pending
                if item.status == "PENDING":
                    pkey = self._KEY_PENDING_LIST.format(cid=self._cid)
                    await self._redis.sadd(pkey, item.oversight_id)
                    await self._redis.expire(pkey, self._timeout_s * 2)
                return
            except Exception as exc:
                logger.error("oversight: Redis save failed: %s", exc)

        self._in_process[item.oversight_id] = item

    async def _load(self, oversight_id: str) -> Optional[OversightItem]:
        key = self._KEY_ITEM.format(cid=self._cid, oid=oversight_id)

        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                if raw:
                    data = json.loads(raw)
                    return OversightItem(**data)
                return None
            except Exception as exc:
                logger.error("oversight: Redis load failed: %s", exc)

        return self._in_process.get(oversight_id)

    async def _remove_from_pending(self, oversight_id: str) -> None:
        if self._redis is not None:
            try:
                pkey = self._KEY_PENDING_LIST.format(cid=self._cid)
                await self._redis.srem(pkey, oversight_id)
            except Exception as exc:
                logger.error("oversight: Redis remove failed: %s", exc)
        else:
            pass  # in-process: status update is sufficient

    async def _pending_redis(self) -> list[OversightItem]:
        pkey = self._KEY_PENDING_LIST.format(cid=self._cid)
        ids = await self._redis.smembers(pkey)
        items: list[OversightItem] = []
        for oid in ids:
            item = await self._load(oid.decode() if isinstance(oid, bytes) else oid)
            if item and item.status == "PENDING":
                items.append(item)
        return items
