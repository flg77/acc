"""The refinement ledger — every durable change to what an agent knows or is,
in one append-only place, with what it rested on and what came of it.

OpenSpec ``20260923-lessons-that-travel`` Phase 2 (vault PA-04).  ACC changes
itself through three stores with three audit shapes: ``memory_notes`` (a
note: ``source_ids``, ``dissent``), ``role_audit`` (a role version:
``diff_summary``, ``approver_id``) and ``proposed_rules.jsonl`` (a Cat-B/C
rule).  None records what the change was *expected* to do, none records what
it *did*, and no id joins them.  This ledger is the join.

One record per change, ``kind`` in :data:`KINDS`:

* ``note`` — reflection distilled a note (the wire form is a ``Lesson``).
* ``adopt`` — a peer's lesson entered this role's shared tier (Phase 4).
* ``publish`` / ``hub_promote`` — a person moved a note across a context.
* ``role_patch`` — a signed ``ROLE_UPDATE`` was applied.
* ``rule`` — a learned Cat-B/C rule was approved.
* ``forget`` — a person was erased.
* ``outcome`` — a task that had a lesson in its prompt ended (Phase 5);
  ``target.id`` is the lesson, ``measured`` says how it ended.
* ``rollback`` — a change was reverted; ``rollback_of`` names it.

Where it lives: ``refinements.jsonl`` under the tracelog directory (or
``ACC_REFINEMENTS_PATH``), one JSON object per line, plus a 30-day mirror in
Redis (``acc:{cid}:refinements``, a sorted set by time) so ``acc-cli refine``
on the operator's host reads it without the container's filesystem.
Best-effort everywhere: a ledger write never fails the change it records.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("acc.refinements")

KINDS = ("note", "adopt", "publish", "hub_promote", "role_patch", "rule", "forget",
         "outcome", "rollback")

#: Redis mirror TTL.  Longer than the lessons' 7 days: a rollback may be asked
#: for after the lesson itself has expired from the cache.
MIRROR_TTL_S = 30 * 24 * 3600


def redis_refinements_key(collective_id: str) -> str:
    """``acc:{cid}:refinements`` — sorted set, score = ts, member = the record."""
    return f"acc:{collective_id}:refinements"


def ledger_path() -> Path:
    raw = os.environ.get("ACC_REFINEMENTS_PATH", "").strip()
    if raw:
        return Path(raw)
    from acc.tracelog import tracelog_dir  # noqa: PLC0415
    return tracelog_dir() / "refinements.jsonl"


@dataclass
class RefinementRecord:
    kind: str
    collective_id: str = ""
    agent_id: str = ""
    role_label: str = ""
    trigger: str = ""
    #: What the change rested on.  Ids, not content.
    evidence: dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""
    #: ``{store, id, old, new}`` — what changed, and from/to what.
    target: dict[str, Any] = field(default_factory=dict)
    approver: str = ""
    ceiling: str = ""
    scope: str = ""
    rollback_of: str = ""
    #: Phase 5 — for ``outcome`` records: how the task ended.
    measured: dict[str, Any] = field(default_factory=dict)
    refinement_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record(
    kind: str,
    *,
    redis_client: Any = None,
    root: Path | None = None,
    **fields: Any,
) -> RefinementRecord | None:
    """Append one record.  Returns it, or ``None`` when nothing could be
    written anywhere (both sinks failed) — and never raises."""
    if kind not in KINDS:
        logger.warning("refinements: unknown kind %r dropped", kind)
        return None
    rec = RefinementRecord(kind=kind, **fields)
    line = json.dumps(rec.to_dict(), ensure_ascii=False, default=str)
    wrote = False
    try:
        path = (root / "refinements.jsonl") if root is not None else ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        wrote = True
    except Exception:  # noqa: BLE001
        logger.debug("refinements: file append failed", exc_info=True)
    if redis_client is not None and rec.collective_id:
        try:
            key = redis_refinements_key(rec.collective_id)
            redis_client.zadd(key, {line: rec.ts})
            redis_client.expire(key, MIRROR_TTL_S)
            wrote = True
        except Exception:  # noqa: BLE001
            logger.debug("refinements: redis mirror failed", exc_info=True)
    return rec if wrote else None


def _decode(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, (bytes, bytearray)) else str(value)


def load(
    redis_client: Any = None,
    collective_id: str = "",
    *,
    root: Path | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """The most recent records, newest first.  Redis when it answers, the
    file otherwise; the file alone when no collective is named."""
    out: list[dict[str, Any]] = []
    if redis_client is not None and collective_id:
        try:
            raw = redis_client.zrevrange(redis_refinements_key(collective_id), 0, max(0, limit - 1))
            for item in raw or []:
                try:
                    out.append(json.loads(_decode(item)))
                except json.JSONDecodeError:
                    continue
            if out:
                return out
        except Exception:  # noqa: BLE001
            logger.debug("refinements: redis read failed", exc_info=True)
    try:
        path = (root / "refinements.jsonl") if root is not None else ledger_path()
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not collective_id or rec.get("collective_id") == collective_id:
                    out.append(rec)
                if len(out) >= limit:
                    break
    except Exception:  # noqa: BLE001
        logger.debug("refinements: file read failed", exc_info=True)
    return out


def find(records: list[dict[str, Any]], prefix: str) -> dict[str, Any] | None:
    """One record by id or unique id prefix."""
    hits = [r for r in records if str(r.get("refinement_id", "")).startswith(prefix)]
    return hits[0] if len(hits) == 1 else None


def trace(records: list[dict[str, Any]], target_id: str) -> list[dict[str, Any]]:
    """Every record about one thing — the note / lesson / role — oldest first:
    how it came to be, where it went, what it did, whether it was undone."""
    hits = [
        r for r in records
        if str((r.get("target") or {}).get("id", "")) == target_id
        or str((r.get("evidence") or {}).get("lesson_id", "")) == target_id
        or r.get("rollback_of") == target_id
        or r.get("refinement_id") == target_id
    ]
    return sorted(hits, key=lambda r: float(r.get("ts", 0) or 0))


def outcomes_for(records: list[dict[str, Any]], lesson_id: str) -> list[dict[str, Any]]:
    return [r for r in trace(records, lesson_id) if r.get("kind") == "outcome"]


def changed_fields(old: dict[str, Any], new: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(old_subset, new_subset)`` of the keys whose values differ."""
    keys = [k for k in new if new.get(k) != old.get(k)] + [k for k in old if k not in new]
    return {k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}
