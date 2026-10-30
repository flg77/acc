"""Work done outside the collective -- the coding agents' items (CS-03).

`20261030-external-work-board` (backlog CS-03 / CS-10, proposal 062).  ACC's
work board shows what ACC's own agents do.  The operator also works through
coding agents ACC does not run -- Claude Code, Codex, Cursor -- and the chief
of staff has to see that work too.  An *external work item* is one unit of it:
claimed, progressed, handed over and completed by the agent itself (through
ACC's MCP server, CS-02) or closed by a session hook (CS-05).

Items live beside the oversight queue, like missions: in Redis when the queue
has it (``acc:{cid}:ext_work:{item_id}``), in process otherwise.  The arbiter
puts the newest of them on its HEARTBEAT, so a surface that joins late still
sees them; an ``EXTERNAL_WORK`` signal carries each change as it happens.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

from acc.redis_compat import call_redis as _call_redis

logger = logging.getLogger("acc.external_work")

QUEUED = "QUEUED"
RUNNING = "RUNNING"
DONE = "DONE"
FAILED = "FAILED"
STATUSES: tuple[str, ...] = (QUEUED, RUNNING, DONE, FAILED)

#: A RUNNING item with no progress for this long is flagged on the board.
STALE_AFTER_S = 24 * 3600

#: Progress lines kept per item; older ones fall off.
_PROGRESS_KEEP = 20


def new_item_id() -> str:
    return f"ext-{uuid.uuid4().hex[:10]}"


@dataclass
class ExternalWorkItem:
    """One unit of work an external coding agent does."""

    item_id: str
    title: str
    status: str = QUEUED
    executor: str = ""            # "claude-vertex", "codex", "cursor", ...
    session_id: str = ""
    repo: str = ""
    branch: str = ""
    requester: str = ""           # attribution of who asked; "" = the operator
    brief: str = ""               # the hand-off brief, when another agent should continue
    handoff_from: str = ""        # executor that handed it over
    progress: list[str] = field(default_factory=list)
    outcome: str = ""
    pr: str = ""
    diffstat: str = ""
    cost_usd: float = 0.0         # as reported by the agent
    tokens: int = 0               # as reported by the agent
    created_ts: float = 0.0
    updated_ts: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExternalWorkItem":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def add_progress(self, line: str, *, now: float | None = None) -> None:
        text = str(line or "").strip()
        if text:
            self.progress.append(text)
            del self.progress[:-_PROGRESS_KEEP]
        self.updated_ts = time.time() if now is None else now

    def summary(self) -> dict:
        """The compact row the heartbeat and the signal carry."""
        row = self.to_dict()
        row.pop("progress")
        row["last_progress"] = self.progress[-1] if self.progress else ""
        row["brief"] = self.brief[:400]
        return row


class ExternalWorkStore:
    """External work items beside the oversight queue: in Redis when the
    queue has it, in process otherwise.  Keys carry no TTL; a finished item
    stays as the record of what was done."""

    _KEY = "acc:{cid}:ext_work:{iid}"
    _KEY_ALL = "acc:{cid}:ext_work"

    def __init__(self, redis_client: Any = None, collective_id: str = "sol-01") -> None:
        self._redis = redis_client
        self._cid = collective_id
        self._mem: dict[str, ExternalWorkItem] = {}
        self._mem_sessions: dict[str, dict] = {}

    @classmethod
    def for_queue(cls, queue: Any) -> "ExternalWorkStore":
        """The store sharing *queue*'s backend (one per queue object)."""
        store = getattr(queue, "_external_work_store", None)
        if store is None:
            store = cls(getattr(queue, "_redis", None), getattr(queue, "_cid", "sol-01"))
            try:
                queue._external_work_store = store
            except Exception:  # noqa: BLE001 -- a queue that refuses attributes
                pass
        return store

    async def save(self, item: ExternalWorkItem) -> None:
        if self._redis is not None:
            try:
                await _call_redis(self._redis.set, self._KEY.format(cid=self._cid, iid=item.item_id),
                                  json.dumps(item.to_dict()))
                await _call_redis(self._redis.sadd, self._KEY_ALL.format(cid=self._cid), item.item_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("external_work: Redis save failed: %s", exc)
        self._mem[item.item_id] = item

    async def get(self, item_id: str) -> Optional[ExternalWorkItem]:
        if self._redis is not None:
            try:
                raw = await _call_redis(self._redis.get, self._KEY.format(cid=self._cid, iid=item_id))
                return ExternalWorkItem.from_dict(json.loads(raw)) if raw else None
            except Exception as exc:  # noqa: BLE001
                logger.error("external_work: Redis load failed: %s", exc)
        return self._mem.get(item_id)

    async def all(self, limit: int | None = None) -> list[ExternalWorkItem]:
        """Every item, most recently updated first."""
        ids: list[str] = []
        if self._redis is not None:
            try:
                raw = await _call_redis(self._redis.smembers, self._KEY_ALL.format(cid=self._cid))
                ids = [i.decode() if isinstance(i, bytes) else str(i) for i in raw or ()]
            except Exception as exc:  # noqa: BLE001
                logger.error("external_work: Redis list failed: %s", exc)
        else:
            ids = list(self._mem)
        out = [it for it in [await self.get(i) for i in ids] if it is not None]
        out.sort(key=lambda it: it.updated_ts, reverse=True)
        return out[:limit] if limit is not None else out


    # -- sessions (CS-02 ``register``) ------------------------------------

    _KEY_SESSION = "acc:{cid}:ext_session:{sid}"
    _KEY_SESSIONS = "acc:{cid}:ext_sessions"
    SESSION_TTL_S = 7 * 24 * 3600

    async def touch_session(self, session: dict) -> None:
        """Record (or refresh) one coding session; it expires after a week."""
        sid = str(session.get("session_id", "") or "")
        if not sid:
            return
        if self._redis is not None:
            await _call_redis(self._redis.set, self._KEY_SESSION.format(cid=self._cid, sid=sid),
                              json.dumps(session), ex=self.SESSION_TTL_S)
            await _call_redis(self._redis.sadd, self._KEY_SESSIONS.format(cid=self._cid), sid)
            return
        self._mem_sessions[sid] = dict(session)

    async def sessions(self) -> list[dict]:
        """Sessions seen in the last week, most recent first."""
        if self._redis is None:
            rows = list(self._mem_sessions.values())
        else:
            raw = await _call_redis(self._redis.smembers, self._KEY_SESSIONS.format(cid=self._cid))
            rows = []
            for sid in raw or ():
                sid = sid.decode() if isinstance(sid, bytes) else str(sid)
                body = await _call_redis(self._redis.get, self._KEY_SESSION.format(cid=self._cid, sid=sid))
                if body:
                    rows.append(json.loads(body))
                else:                     # expired: drop it from the index
                    await _call_redis(self._redis.srem, self._KEY_SESSIONS.format(cid=self._cid), sid)
        rows.sort(key=lambda r: float(r.get("last_seen_ts", 0.0) or 0.0), reverse=True)
        return rows


# ---------------------------------------------------------------------------
# Notes -- what a coding agent wants the others to know (CS-02 remember/recall)
# ---------------------------------------------------------------------------
#
# The collective's document store needs an embedding model and lives inside the
# agent processes; the MCP server runs on the host beside the coding agent.  So
# the coding agents' notes are a bounded list in the same Redis, matched by
# words, newest first.  The chief of staff reads them (CS-11).


@dataclass
class ExternalNote:
    note_id: str
    text: str
    tags: list[str] = field(default_factory=list)
    executor: str = ""
    session_id: str = ""
    repo: str = ""
    item_id: str = ""
    ts: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExternalNote":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def _words(text: str) -> set[str]:
    out, cur = set(), []
    for ch in str(text or "").lower() + " ":
        if ch.isalnum() or ch in "_-":
            cur.append(ch)
        elif cur:
            w = "".join(cur)
            if len(w) >= 3:
                out.add(w)
            cur = []
    return out


class ExternalNoteStore:
    """The newest :attr:`KEEP` notes: an id list plus one key per note, so a
    note can be replaced in place (one note per ingested session)."""

    KEEP = 1000
    _KEY = "acc:{cid}:ext_note:{nid}"
    _KEY_ALL = "acc:{cid}:ext_notes"

    def __init__(self, redis_client: Any = None, collective_id: str = "sol-01") -> None:
        self._redis = redis_client
        self._cid = collective_id
        self._mem: dict[str, ExternalNote] = {}
        self._mem_order: list[str] = []

    async def put(self, note: ExternalNote) -> None:
        """Add *note*, or replace the note with the same id and move it to the front."""
        if not note.ts:
            note.ts = time.time()
        if self._redis is not None:
            key_all = self._KEY_ALL.format(cid=self._cid)
            await _call_redis(self._redis.set, self._KEY.format(cid=self._cid, nid=note.note_id),
                              json.dumps(note.to_dict()))
            await _call_redis(self._redis.lrem, key_all, 0, note.note_id)
            await _call_redis(self._redis.lpush, key_all, note.note_id)
            await _call_redis(self._redis.ltrim, key_all, 0, self.KEEP - 1)
            return
        self._mem[note.note_id] = note
        self._mem_order = [note.note_id] + [n for n in self._mem_order if n != note.note_id]
        for dropped in self._mem_order[self.KEEP:]:
            self._mem.pop(dropped, None)
        del self._mem_order[self.KEEP:]

    async def recent(self) -> list[ExternalNote]:
        if self._redis is None:
            return [self._mem[n] for n in self._mem_order]
        ids = await _call_redis(self._redis.lrange, self._KEY_ALL.format(cid=self._cid), 0, self.KEEP - 1)
        out = []
        for nid in ids or ():
            nid = nid.decode() if isinstance(nid, bytes) else str(nid)
            body = await _call_redis(self._redis.get, self._KEY.format(cid=self._cid, nid=nid))
            if body:
                out.append(ExternalNote.from_dict(json.loads(body)))
        return out

    async def search(self, query: str = "", *, tags: list[str] | None = None,
                     repo: str = "", limit: int = 5) -> list[ExternalNote]:
        """Notes sharing the most words with *query* (all, when it is empty),
        carrying every tag in *tags*, newest first among equals."""
        want = _words(query)
        need = {str(t).lower() for t in tags or ()}
        scored = []
        for pos, note in enumerate(await self.recent()):
            if need and not need <= {t.lower() for t in note.tags}:
                continue
            if repo and note.repo != repo:
                continue
            hits = len(want & _words(note.text + " " + " ".join(note.tags)))
            if want and not hits:
                continue
            scored.append((-hits, pos, note))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [n for _, _, n in scored[:limit]]


__all__ = [
    "DONE", "ExternalNote", "ExternalNoteStore", "ExternalWorkItem", "ExternalWorkStore",
    "FAILED", "QUEUED", "RUNNING", "STALE_AFTER_S", "STATUSES", "new_item_id",
]
