"""The hub curator's job, as a function (`20260906-enterprise-brain-hub-scope`).

The enterprise brain fills only through people: a note in an instance's
**shared** tier (already approved once, by two people, out of the context it
was distilled in) may be proposed *up* into the hub's enterprise tier, where
two more approvals put it in front of every instance bound to the hub.

The curator never learns and never answers anyone; it looks at what the
instances have already agreed on and proposes. This module is that look,
deterministic and readable: which shared notes across the collectives on this
Redis meet the quorum, are past probation, and are not in the hub yet. The
proposing is `acc-cli memory curate --propose` today; a curator role that
runs it on a schedule is Phase 2.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from acc.memory_reflection import (
    HUB_TIER,
    PROBATION_S,
    QUORUM_DEFAULT,
    MemoryNote,
    _raw_note_entries,
)
from acc.signals import redis_shared_notes_key

logger = logging.getLogger("acc.memory_curate")

_SHARED_KEY_RE = re.compile(r"^acc:([^:]+):memory_notes_shared:([^:]+):(.+)$")


@dataclass
class Candidate:
    """A shared note that qualifies for the hub."""

    collective_id: str
    role_label: str
    scope: str
    summary: str
    people: int
    ceiling: str
    source_requesters: list[str] = field(default_factory=list)
    dissent: str = ""
    note_id: str = ""
    published_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "collective_id": self.collective_id, "role_label": self.role_label,
            "scope": self.scope, "summary": self.summary, "people": self.people,
            "ceiling": self.ceiling, "note_id": self.note_id,
            "published_at": self.published_at, "dissent": self.dissent,
        }

    def note(self) -> MemoryNote:
        """The note as the proposal builder expects it."""
        return MemoryNote(
            summary=self.summary, agent_id="", role_label=self.role_label,
            source_requesters=list(self.source_requesters), scope=self.scope,
            dissent=self.dissent, ceiling=self.ceiling, note_id=self.note_id,
        )


def _shared_keys(redis_client: Any) -> list[str]:
    lister = getattr(redis_client, "scan_iter", None) or getattr(redis_client, "keys", None)
    if lister is None:
        return []
    try:
        found = lister("acc:*:memory_notes_shared:*")
    except Exception:  # noqa: BLE001
        logger.debug("memory_curate: cannot list shared keys", exc_info=True)
        return []
    out = []
    for key in found or ():
        out.append(key.decode("utf-8", "replace") if isinstance(key, (bytes, bytearray)) else str(key))
    return out


def candidates(
    redis_client: Any, hub_collective_id: str, *, k: int = QUORUM_DEFAULT,
    collectives: Iterable[str] | None = None, now: float | None = None,
) -> list[Candidate]:
    """Shared notes across the bound instances that qualify for *hub*.

    Qualifies = published into an instance's shared tier (an approval record
    exists), rests on at least *k* people, is past probation, and is not in
    the hub's enterprise tier yet. The hub's own keys and, when *collectives*
    is given, keys of other collectives are skipped.
    """
    if redis_client is None or not hub_collective_id:
        return []
    now = time.time() if now is None else now
    allowed = set(collectives) if collectives is not None else None
    in_hub: dict[str, set[str]] = {}
    out: list[Candidate] = []
    for key in sorted(_shared_keys(redis_client)):
        m = _SHARED_KEY_RE.match(key)
        if not m:
            continue
        cid, role, scope = m.group(1), m.group(2), m.group(3)
        if cid == hub_collective_id:
            continue
        if allowed is not None and cid not in allowed:
            continue
        if role not in in_hub:
            in_hub[role] = {
                str(e.get("summary") or "")
                for e in _raw_note_entries(
                    redis_client, redis_shared_notes_key(hub_collective_id, role, HUB_TIER))
            }
        for entry in _raw_note_entries(redis_client, key):
            summary = str(entry.get("summary") or "").strip()
            if not summary or summary in in_hub[role]:
                continue
            at = float(entry.get("at") or 0.0)
            if at and (now - at) < PROBATION_S:
                continue
            requesters = [str(r) for r in (entry.get("source_requesters") or [])]
            from acc.attribution import people_in  # noqa: PLC0415
            people = len(people_in(requesters))
            if people < max(1, k):
                continue
            out.append(Candidate(
                collective_id=cid, role_label=role, scope=scope, summary=summary,
                people=people, ceiling=str(entry.get("ceiling") or "CRITICAL"),
                source_requesters=requesters, dissent=str(entry.get("dissent") or ""),
                note_id=str(entry.get("note_id") or ""), published_at=at,
            ))
    return out
