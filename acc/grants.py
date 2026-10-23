"""Standing grants -- quieter over time, never by itself (vault proposal 058,
Phase 5; backlog B5 / HG-35 apply + revoke).

:mod:`acc.approval_patterns` turns a repeated, consistent approval into a
*proposal* and deliberately cannot apply one.  This module is the other half,
and keeps the same rules:

* **A person creates a grant.**  ``acc-cli grants accept <evidence>`` with the
  operator's name.  Nothing here runs on a schedule or from a pattern.
* **Exactly what was approved.**  A grant covers one role and one target, the
  signature the pattern was found on, and is never widened.
* **Checked before it is given.**  The posture scan (:mod:`acc.posture_scan`)
  must have no HIGH finding for the role.
* **Revocable, and recorded.**  ``acc-cli grants revoke``.  Every call a grant
  answers is an ``AUTO_APPROVED`` row whose approver is ``policy:grant:<id>``.

A grant never answers a CRITICAL call, a destructive call, or anything outside
``system_access``.  Those stay questions, as under a mission.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, fields
from typing import Any, Iterable, Optional

from acc.redis_compat import call_redis as _call_redis

logger = logging.getLogger("acc.grants")

STATUS_ACTIVE = "ACTIVE"
STATUS_REVOKED = "REVOKED"

_SUBJECT_RE = re.compile(r"\b(skill|mcp) ([A-Za-z0-9_][\w.\-]*)")


@dataclass
class StandingGrant:
    grant_id: str
    role: str
    target: str            # "skill:shell_exec" / "mcp:github.create_issue"
    approved_by: str
    created_ms: int
    evidence_hash: str = ""
    evidence: dict | None = None
    status: str = STATUS_ACTIVE
    revoked_by: str = ""
    revoked_ms: int = 0
    uses: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StandingGrant":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def subject_of(role_id: str, summary: str) -> str:
    """The pattern subject of an oversight row: ``<role>/<kind>:<target>``
    ("" when the row is not a capability gate)."""
    m = _SUBJECT_RE.search(summary or "")
    return f"{role_id}/{m.group(1)}:{m.group(2)}" if m else ""


def decisions_from(items: Iterable[Any]):
    """Oversight rows as :class:`acc.approval_patterns.Decision` (for detect)."""
    from acc.approval_patterns import Decision  # noqa: PLC0415

    out = []
    for it in items:
        status = str(getattr(it, "status", "") or "")
        if status not in ("APPROVED", "REJECTED"):
            continue  # AUTO_APPROVED / EXPIRED are not a person's judgement
        subject = subject_of(str(getattr(it, "role_id", "") or ""), str(getattr(it, "summary", "") or ""))
        if not subject:
            continue
        out.append(Decision(
            oversight_id=str(it.oversight_id), kind="call", subject=subject,
            approved=status == "APPROVED", approver=str(getattr(it, "approver_id", "") or ""),
            risk_level=str(getattr(it, "risk_level", "") or ""),
            at=float(getattr(it, "resolved_at_ms", 0) or 0) / 1000.0,
        ))
    return out


class GrantStore:
    """Grants beside the oversight queue (Redis keys with no TTL, or in process)."""

    _KEY = "acc:{cid}:grant:{gid}"
    _KEY_ALL = "acc:{cid}:grants"

    def __init__(self, redis_client: Any = None, collective_id: str = "sol-01") -> None:
        self._redis = redis_client
        self._cid = collective_id
        self._mem: dict[str, StandingGrant] = {}

    @classmethod
    def for_queue(cls, queue: Any) -> "GrantStore":
        store = getattr(queue, "_grant_store", None)
        if store is None:
            store = cls(getattr(queue, "_redis", None), getattr(queue, "_cid", "sol-01"))
            try:
                queue._grant_store = store
            except Exception:  # noqa: BLE001
                pass
        return store

    async def save(self, g: StandingGrant) -> None:
        if self._redis is not None:
            try:
                await _call_redis(self._redis.set, self._KEY.format(cid=self._cid, gid=g.grant_id),
                                  json.dumps(g.to_dict()))
                await _call_redis(self._redis.sadd, self._KEY_ALL.format(cid=self._cid), g.grant_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("grants: Redis save failed: %s", exc)
        self._mem[g.grant_id] = g

    async def all(self) -> list[StandingGrant]:
        if self._redis is not None:
            try:
                ids = await _call_redis(self._redis.smembers, self._KEY_ALL.format(cid=self._cid))
                out = []
                for i in ids or ():
                    i = i.decode() if isinstance(i, bytes) else str(i)
                    raw = await _call_redis(self._redis.get, self._KEY.format(cid=self._cid, gid=i))
                    if raw:
                        out.append(StandingGrant.from_dict(json.loads(raw)))
                return sorted(out, key=lambda g: g.created_ms, reverse=True)
            except Exception as exc:  # noqa: BLE001
                logger.error("grants: Redis list failed: %s", exc)
        return sorted(self._mem.values(), key=lambda g: g.created_ms, reverse=True)

    async def covering(self, role: str, target: str) -> Optional[StandingGrant]:
        for g in await self.all():
            if g.status == STATUS_ACTIVE and g.role == role and g.target == target:
                return g
        return None

    async def accept(self, *, role: str, target: str, approved_by: str,
                     evidence_hash: str = "", evidence: dict | None = None) -> StandingGrant:
        if not approved_by:
            raise ValueError("a grant needs the name of the person who gives it")
        g = StandingGrant(
            grant_id=f"g-{uuid.uuid4().hex[:12]}", role=role, target=target,
            approved_by=approved_by, created_ms=int(time.time() * 1000),
            evidence_hash=evidence_hash, evidence=evidence,
        )
        await self.save(g)
        logger.warning("grants: %s given by %s for %s %s", g.grant_id, approved_by, role, target)
        return g

    async def revoke(self, grant_id: str, *, by: str) -> Optional[StandingGrant]:
        for g in await self.all():
            if g.grant_id == grant_id:
                if g.status == STATUS_ACTIVE:
                    g.status, g.revoked_by, g.revoked_ms = STATUS_REVOKED, by, int(time.time() * 1000)
                    await self.save(g)
                return g
        return None


# ---------------------------------------------------------------------------
# Granted once, at the moment of asking (vault 060, `20261018-grant-once-allowances`)
# ---------------------------------------------------------------------------
#
# The operator (2026-10-18): "shell_exec etc. are granted once by the user, same
# goes for hosts and 3rd systems access."  A grant is still given by a person
# and still covers exactly one role and one target; what changes is that the
# question itself offers it, so a repeated pattern is no longer needed first.
# Targets: ``skill:<id>`` / ``mcp:<server.tool>`` (as before) and
# ``host:<name>[:<port>]`` for ssh_exec.

ANSWER_ONCE = "1"
ANSWER_ALWAYS = "2"
ANSWER_REFUSE = "3"


def host_target(host: str, port: int | str = 22) -> str:
    """The grant target for an ssh host (the port only when it is not 22)."""
    port = int(port or 22)
    return f"host:{host}" if port == 22 else f"host:{host}:{port}"


def grant_question(role: str, kind: str, target: str):
    """The question for a system-access call no mission or grant answers."""
    from acc.question import Question, QuestionOption  # noqa: PLC0415

    who = role or "the agent"
    return Question(
        text=f"{who} wants to run {kind} {target}.",
        options=(
            QuestionOption(key=ANSWER_ONCE, label="allow once", proceeds=True,
                           detail=("runs this call", "the next call asks again")),
            QuestionOption(key=ANSWER_ALWAYS, label=f"always allow for {who}", proceeds=True,
                           detail=(f"{who} may run {kind} {target} without asking from now on",
                                   "still asked: deletions, CRITICAL calls, sending for you",
                                   "revoke any time: acc-cli grants revoke <id>")),
            QuestionOption(key=ANSWER_REFUSE, label="don't run it", proceeds=False,
                           detail=("nothing runs",)),
        ),
    )


def host_question(role: str, host: str, port: int | str = 22):
    """The question for an ssh host no grant, plan or seed list covers."""
    from acc.question import Question, QuestionOption  # noqa: PLC0415

    who = role or "the agent"
    where = host if int(port or 22) == 22 else f"{host}:{port}"
    return Question(
        text=f"{who} wants to reach another system: ssh to {where}.",
        options=(
            QuestionOption(key=ANSWER_ONCE, label="allow once", proceeds=True,
                           detail=("this connection only",)),
            QuestionOption(key=ANSWER_ALWAYS, label=f"always allow {where} for {who}", proceeds=True,
                           detail=(f"{where} goes on {who}'s host allowance list",
                                   "the OpenShell sandbox lets ssh reach it from now on",
                                   "destructive remote commands are still asked")),
            QuestionOption(key=ANSWER_REFUSE, label="don't connect", proceeds=False,
                           detail=("nothing runs",)),
        ),
    )


async def give_from_answer(queue: Any, *, role: str, target: str, approver: str) -> StandingGrant | None:
    """The "always allow" answer: give the grant, and for a host add it to the
    sandbox's network policy.  None when nobody can be named as the giver."""
    if not approver or approver in ("default", "tui:anonymous", "unattributed"):
        logger.warning("grants: 'always allow' for %s %s without a named approver -- not given", role, target)
        return None
    store = GrantStore.for_queue(queue)
    existing = await store.covering(role, target)
    if existing is not None:
        return existing
    g = await store.accept(role=role, target=target, approved_by=approver,
                           evidence={"source": "question"})
    if target.startswith("host:"):
        from acc.sandbox.policy import allow_hosts  # noqa: PLC0415
        allow_hosts([target])
    return g
