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
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from acc.redis_compat import call_redis as _call_redis

logger = logging.getLogger("acc.oversight")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


_SYNTHETIC_NS = uuid.UUID("6f1c5c0e-4a3b-4c1d-9e2f-0b7a8d9c1e2f")


def status_label(item: dict) -> str:
    """``PENDING`` or ``PENDING 1/2`` -- what a surface shows for a row that
    asks for more than one approval."""
    status = str(item.get("status") or "PENDING")
    required = int(item.get("required_approvals") or 1)
    if status == "PENDING" and required > 1:
        return f"PENDING {len(item.get('approvals') or [])}/{required}"
    return status


def synthetic_oversight_id(payload: dict) -> str:
    """The one id every agent derives for the same ``OVERSIGHT_SUBMIT`` event.

    ``acc-cli oversight submit`` mints the id itself; this is the fallback for
    a publisher that did not, so N subscribers still enqueue one row: a UUID5
    over the fields that identify the event (task, agent, summary, ts)."""
    given = str(payload.get("oversight_id") or "").strip()
    if given:
        return given
    key = "|".join(str(payload.get(k, "")) for k in ("task_id", "agent_id", "summary", "ts", "collective_id"))
    return str(uuid.uuid5(_SYNTHETIC_NS, key))


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
    status: str = "PENDING"  # PENDING | APPROVED | REJECTED | EXPIRED | AUTO_APPROVED
    approver_id: str = ""
    rejection_reason: str = ""
    resolved_at_ms: int = 0
    # `20260902-assistant-autonomy-prompt-pane-approvals` 1.3 -- on an
    # AUTO_APPROVED row, what the policy's execution did ("dispatched" /
    # "dispatch_failed").  Empty on human-decided rows.
    outcome: str = ""
    # `20260906-enterprise-brain-hub-scope` Phase 2b -- how many distinct
    # people must approve before the row is APPROVED (1 = a decision is a
    # decision, D-013).  A hub promotion of a HIGH / CRITICAL note asks for 2
    # (HG-40.1 §2.5); ``approvals`` records each one (approver, tier, ms).
    required_approvals: int = 1
    approvals: list = field(default_factory=list)

    @property
    def approvals_needed(self) -> int:
        """Distinct people still to approve (0 once the row is decided)."""
        if self.status != "PENDING":
            return 0
        return max(0, int(self.required_approvals or 1) - len(self.approvals))


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
    # 1.3 -- decided items (human or policy) in resolution order, newest
    # first, capped.  This is the history the Compliance pane renders; the
    # pending set alone forgets a row the moment it is decided.
    _KEY_DECIDED_LIST = "acc:{cid}:oversight:decided"
    _DECIDED_KEEP = 50
    _DECIDED_TTL_S = 24 * 3600

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
        oversight_id: str | None = None,
        required_approvals: int = 1,
    ) -> str:
        """Submit a task to the oversight queue.

        Args:
            task_id:    The task identifier from the TASK_ASSIGN payload.
            risk_level: EU AI Act risk level (HIGH | UNACCEPTABLE).
            summary:    Human-readable description of why oversight is needed.
            role_id:    The submitting agent's role label.
            oversight_id: Optional id to use instead of minting one.  A row that
                several agents enqueue from one bus event (``OVERSIGHT_SUBMIT``
                reaches every agent) must share an id, or the queue shows one
                row per agent.  An id that already exists is left untouched.

        Returns:
            ``oversight_id`` — UUID string identifying this oversight request.
        """
        if oversight_id:
            existing = await self._load(oversight_id)
            if existing is not None:
                return oversight_id
        oversight_id = oversight_id or str(uuid.uuid4())
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
            required_approvals=max(1, int(required_approvals or 1)),
        )

        await self._save(item)
        logger.warning(
            "oversight: submitted oversight_id=%s task_id=%s risk=%s%s",
            oversight_id,
            task_id,
            risk_level,
            f" approvals_required={item.required_approvals}" if item.required_approvals > 1 else "",
        )
        return oversight_id

    async def record_auto_approved(
        self,
        task_id: str,
        risk_level: str,
        summary: str,
        role_id: str,
        *,
        policy: str,
        outcome: str = "",
    ) -> str:
        """Record a proposal the operating mode executed without asking.

        `20260902-assistant-autonomy-prompt-pane-approvals` 1.3 -- "tracked,
        not asked".  The row is born resolved: status ``AUTO_APPROVED``,
        ``approver_id`` = ``policy:<mode>`` so the history names the policy
        as the approver, never a person.  It is never PENDING, so no gate
        card / queue row appears and nothing waits on it.

        Returns the ``oversight_id`` of the recorded row.
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
            timeout_ms=now_ms,
            status="AUTO_APPROVED",
            approver_id=f"policy:{policy}",
            resolved_at_ms=now_ms,
            outcome=outcome,
        )
        await self._save(item)
        await self._push_decided(oversight_id)
        logger.info(
            "oversight: auto-approved oversight_id=%s task_id=%s policy=%s outcome=%s",
            oversight_id, task_id, policy, outcome,
        )
        return oversight_id

    async def recent_decisions(self, limit: int = 20) -> list[OversightItem]:
        """Decided items (APPROVED / REJECTED / EXPIRED / AUTO_APPROVED),
        newest first, at most *limit*.  The Compliance pane's history."""
        if self._redis is not None:
            try:
                key = self._KEY_DECIDED_LIST.format(cid=self._cid)
                ids = await _call_redis(self._redis.lrange, key, 0, max(limit - 1, 0))
                items: list[OversightItem] = []
                seen: set[str] = set()
                for oid in ids or []:
                    oid = oid.decode() if isinstance(oid, bytes) else str(oid)
                    if oid in seen:      # a list written before _push_decided de-duplicated
                        continue
                    seen.add(oid)
                    item = await self._load(oid)
                    if item is not None and item.status != "PENDING":
                        items.append(item)
                return items
            except Exception as exc:
                logger.error("oversight: Redis recent query failed: %s", exc)

        decided = [
            item for item in self._in_process.values()
            if item.status != "PENDING"
        ]
        decided.sort(key=lambda it: it.resolved_at_ms, reverse=True)
        return decided[:limit]

    async def approve(
        self, oversight_id: str, approver_id: str, approver_tier: str = "",
    ) -> bool:
        """Mark an oversight item as approved.

        A decision is final: a row that is already APPROVED is left as it is
        (idempotent -- every agent applies the same OVERSIGHT_DECISION, so the
        second and later calls are the norm, not an error), and a row that is
        already REJECTED / EXPIRED / AUTO_APPROVED is **refused** -- the first
        decision stands, a late or conflicting one is logged and dropped.

        A row that asks for **more than one approval** (a hub promotion of a
        HIGH / CRITICAL note) records this approval and stays PENDING until
        as many *distinct people* have approved; the same person again is a
        no-op; an approver below operator tier is refused on such a row
        (the tier is what the second signature is for).  Every agent applies
        the same decision, so recording is keyed by person, not by call.

        Returns:
            ``True`` when the item is APPROVED after this call (freshly, or
            already), ``False`` when it was refused, not found, or is still
            waiting for another approver -- callers use that to decide
            whether the approved mutation may be dispatched.
        """
        item = await self._load(oversight_id)
        if item is None:
            logger.warning("oversight: approve — item %s not found", oversight_id)
            return False
        if item.status != "PENDING":
            if item.status == "APPROVED":
                return True
            logger.warning(
                "oversight: approve refused — %s is already %s by %s; the first "
                "decision stands", oversight_id, item.status, item.approver_id,
            )
            return False
        required = max(1, int(item.required_approvals or 1))
        if required > 1:
            from acc.attribution import person_of  # noqa: PLC0415
            if str(approver_tier or "").lower() != "operator":
                logger.warning(
                    "oversight: approve refused — %s needs %d operator-tier approvals; "
                    "%s is %s", oversight_id, required, approver_id,
                    f"tier {approver_tier!r}" if approver_tier else "of unknown tier",
                )
                return False
            who = person_of(approver_id) or str(approver_id)
            if any((person_of(a.get("approver_id", "")) or a.get("approver_id")) == who
                   for a in item.approvals):
                logger.info(
                    "oversight: %s already approved by %s (%d/%d) — waiting for another person",
                    oversight_id, approver_id, len(item.approvals), required,
                )
                return False
            item.approvals.append({
                "approver_id": approver_id, "approver_tier": approver_tier,
                "ts_ms": int(time.time() * 1000),
            })
            if len(item.approvals) < required:
                await self._save(item)
                logger.info(
                    "oversight: %s approved by %s (%d/%d) — waiting for another person",
                    oversight_id, approver_id, len(item.approvals), required,
                )
                return False
        else:
            item.approvals = [{
                "approver_id": approver_id, "approver_tier": approver_tier,
                "ts_ms": int(time.time() * 1000),
            }]
        item.status = "APPROVED"
        item.approver_id = approver_id
        item.resolved_at_ms = int(time.time() * 1000)
        await self._save(item)
        await self._remove_from_pending(oversight_id)
        await self._push_decided(oversight_id)
        logger.info(
            "oversight: approved oversight_id=%s approver=%s%s", oversight_id, approver_id,
            f" ({len(item.approvals)} approvals)" if required > 1 else "",
        )
        return True

    async def reject(
        self, oversight_id: str, approver_id: str, reason: str = ""
    ) -> bool:
        """Mark an oversight item as rejected.

        Same finality as :meth:`approve`: an already-REJECTED row is a no-op
        (``True``), any other decided status is refused (``False``).

        Args:
            oversight_id: The oversight request ID.
            approver_id:  Identifier of the human reviewer.
            reason:       Optional rejection reason.
        """
        item = await self._load(oversight_id)
        if item is None:
            logger.warning("oversight: reject — item %s not found", oversight_id)
            return False
        if item.status != "PENDING":
            if item.status == "REJECTED":
                return True
            logger.warning(
                "oversight: reject refused — %s is already %s by %s; the first "
                "decision stands", oversight_id, item.status, item.approver_id,
            )
            return False
        item.status = "REJECTED"
        item.approver_id = approver_id
        item.rejection_reason = reason
        item.resolved_at_ms = int(time.time() * 1000)
        await self._save(item)
        await self._remove_from_pending(oversight_id)
        await self._push_decided(oversight_id)
        logger.info("oversight: rejected oversight_id=%s reason=%s", oversight_id, reason)
        return True

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
                await self._push_decided(item.oversight_id)
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

    async def wait_for_decision(
        self,
        oversight_id: str,
        *,
        poll_interval_s: float = 0.5,
        timeout_s: Optional[float] = None,
    ) -> "OversightItem | None":
        """Block until *oversight_id* leaves the PENDING state, or timeout.

        Used by :func:`acc.capability_dispatch.dispatch_invocations` to
        gate CRITICAL skill / MCP-tool invocations on a human approver.

        Args:
            oversight_id: Returned by :meth:`submit`.
            poll_interval_s: Sleep between status checks.  Defaults to
                500 ms — short enough that a human approving via the
                TUI sees the gated agent unblock without perceptible
                delay, long enough that 200 concurrent gated tasks
                don't hammer Redis.
            timeout_s: Hard wall-clock cap on the wait.  ``None`` (the
                default) means use the queue's configured
                ``timeout_s``.  When the cap fires, the item's status
                will be ``EXPIRED`` (the heartbeat loop's
                :meth:`expire_timed_out` call sets that).

        Returns:
            The resolved :class:`OversightItem`, or ``None`` if the
            item disappeared from storage entirely (Redis TTL flush,
            in-process restart) before resolving.

        Implementation: simple polling on top of :meth:`_load`.  We
        deliberately avoid Redis pubsub — the additional connection +
        channel surface is not justified for an event that fires at
        most a few times per minute per agent.
        """
        import asyncio  # noqa: PLC0415 — keep oversight.py import-light

        deadline = (
            time.monotonic()
            + (timeout_s if timeout_s is not None else self._timeout_s)
        )
        while True:
            item = await self._load(oversight_id)
            if item is None:
                return None
            if item.status != "PENDING":
                return item
            if time.monotonic() >= deadline:
                # Caller sees PENDING here — usually the heartbeat loop's
                # next expire_timed_out() pass will flip this to EXPIRED.
                # Returning the still-PENDING item is honest: the gate
                # timed out but the operator could still resolve it.
                return item
            await asyncio.sleep(poll_interval_s)

    # ------------------------------------------------------------------
    # Private storage helpers
    # ------------------------------------------------------------------

    async def _save(self, item: OversightItem) -> None:
        key = self._KEY_ITEM.format(cid=self._cid, oid=item.oversight_id)
        value = json.dumps(asdict(item))

        # A decided row is history and must outlive the gate window; a
        # pending row only needs to outlive its own timeout.
        ttl = (
            self._timeout_s * 2 if item.status == "PENDING"
            else max(self._timeout_s * 2, self._DECIDED_TTL_S)
        )
        if self._redis is not None:
            try:
                await _call_redis(self._redis.set, key, value, ex=ttl)
                # Add to pending list if still pending
                if item.status == "PENDING":
                    pkey = self._KEY_PENDING_LIST.format(cid=self._cid)
                    await _call_redis(self._redis.sadd, pkey, item.oversight_id)
                    await _call_redis(self._redis.expire, pkey, self._timeout_s * 2)
                return
            except Exception as exc:
                logger.error("oversight: Redis save failed: %s", exc)

        self._in_process[item.oversight_id] = item

    async def _load(self, oversight_id: str) -> Optional[OversightItem]:
        key = self._KEY_ITEM.format(cid=self._cid, oid=oversight_id)

        if self._redis is not None:
            try:
                raw = await _call_redis(self._redis.get, key)
                if raw:
                    data = json.loads(raw)
                    return OversightItem(**data)
                return None
            except Exception as exc:
                logger.error("oversight: Redis load failed: %s", exc)

        return self._in_process.get(oversight_id)

    async def _push_decided(self, oversight_id: str) -> None:
        if self._redis is None:
            return  # in-process: recent_decisions() sorts the dict
        try:
            key = self._KEY_DECIDED_LIST.format(cid=self._cid)
            # Every agent applies the same decision, so this runs once per
            # agent: drop any earlier copy first so the list holds one row per
            # id (the Compliance DECISION HISTORY showed six copies of one
            # decision on a six-agent collective, lighthouse 2026-09-05).
            lrem = getattr(self._redis, "lrem", None)
            if lrem is not None:
                await _call_redis(lrem, key, 0, oversight_id)
            await _call_redis(self._redis.lpush, key, oversight_id)
            await _call_redis(self._redis.ltrim, key, 0, self._DECIDED_KEEP - 1)
            await _call_redis(self._redis.expire, key, self._DECIDED_TTL_S)
        except Exception as exc:
            logger.error("oversight: Redis decided-list push failed: %s", exc)

    async def _remove_from_pending(self, oversight_id: str) -> None:
        if self._redis is not None:
            try:
                pkey = self._KEY_PENDING_LIST.format(cid=self._cid)
                await _call_redis(self._redis.srem, pkey, oversight_id)
            except Exception as exc:
                logger.error("oversight: Redis remove failed: %s", exc)
        else:
            pass  # in-process: status update is sufficient

    async def _pending_redis(self) -> list[OversightItem]:
        pkey = self._KEY_PENDING_LIST.format(cid=self._cid)
        ids = await _call_redis(self._redis.smembers, pkey)
        items: list[OversightItem] = []
        for oid in ids:
            item = await self._load(oid.decode() if isinstance(oid, bytes) else oid)
            if item and item.status == "PENDING":
                items.append(item)
        return items
