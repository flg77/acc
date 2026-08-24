"""Removing one person's contributions from memory, including the aggregates.

Phase 6 of ``20260823-attributed-memory``. Before this change the request
"remove what I said" had no implementation and could not have one: a memory note
recorded *how many* episodes it folded, never *which*, so a contribution was
untraceable the moment it was distilled.

**What erasure touches, and what it does not.** The audit trail records *that*
something happened; memory records *what was said*. Erasure removes what was
said and never that it happened — so a `forget` leaves the audit record of the
request intact, and leaves its own journal entry behind. There is no path here
that produces a silent deletion.

Three outcomes for a note that loses sources, and the middle one is the point:

* **Nothing left** — every source erased. The note goes too; there is no lesson
  without the episodes it was drawn from.
* **Below quorum** — sources remain but too few people. The note is **demoted to
  private, not deleted**, and pulled out of any context it was published into.
  Demoting rather than deleting keeps the demotion visible: something changed,
  and an operator can see what.
* **Still above quorum** — the note is rebuilt without the erased sources and
  keeps its tier. The summary text is left alone: it was written from episodes
  that are now gone, and re-deriving it would need the material erasure just
  removed. Recorded as a known limit rather than papered over.

Erasure needs to find rows by field rather than by similarity, which the
:class:`acc.backends.VectorBackend` protocol does not offer. Rather than widen
the protocol for one implementation — and make every other backend silently
non-compliant — the capability is **probed**. A backend that cannot erase is
reported as ``unsupported``, never as done.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from acc.attribution import person_of
from acc.memory_reflection import (
    QUORUM_DEFAULT,
    TIER_SHARED,
)
from acc.memory_scope import LOCAL_SCOPE
from acc.signals import redis_shared_notes_key

logger = logging.getLogger("acc.memory_forget")

_REQUIRED = ("rows", "delete_where", "replace_row")


@dataclass
class ForgetReport:
    """What an erasure actually did. Every count is an observation, not a plan."""

    person: str
    episodes_removed: int = 0
    notes_rebuilt: int = 0
    notes_demoted: int = 0
    notes_deleted: int = 0
    unpublished_from: list[str] = field(default_factory=list)
    unsupported: str = ""
    journal: list[dict[str, Any]] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not self.unsupported

    def as_dict(self) -> dict[str, Any]:
        return {
            "person": self.person,
            "episodes_removed": self.episodes_removed,
            "notes_rebuilt": self.notes_rebuilt,
            "notes_demoted": self.notes_demoted,
            "notes_deleted": self.notes_deleted,
            "unpublished_from": list(self.unpublished_from),
            "unsupported": self.unsupported,
            "journal": list(self.journal),
        }


def _sql_quote(value: str) -> str:
    return str(value).replace("'", "''")


def _episode_predicate(person: str) -> str:
    """Match a person however their requester string was rendered.

    ``Principal.attribution()`` emits ``source:subject`` for a direct exchange
    and ``source:subject@scope`` inside a room, so erasing one person means
    matching both shapes. Missing the suffixed form would leave every channel
    episode behind while reporting success.
    """
    quoted = _sql_quote(person)
    return f"requester = '{quoted}' OR requester LIKE '{quoted}@%'"


def _decode(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def forget_person(
    vector: Any,
    person: str,
    *,
    redis_client: Any = None,
    collective_id: str = "",
    k: int = QUORUM_DEFAULT,
    dry_run: bool = False,
) -> ForgetReport:
    """Erase *person*'s episodes and reconcile the notes drawn from them."""
    who = person_of(person)
    report = ForgetReport(person=who)
    if not who:
        report.unsupported = "no person given"
        return report

    missing = [name for name in _REQUIRED if not hasattr(vector, name)]
    if missing:
        report.unsupported = (
            f"this vector backend cannot erase (missing {', '.join(missing)})"
        )
        logger.warning("memory_forget: %s", report.unsupported)
        return report

    episodes = vector.rows("episodes")
    doomed = {
        str(e.get("id") or "") for e in episodes
        if person_of(e.get("requester")) == who and e.get("id")
    }
    report.episodes_removed = len(doomed)

    for note in vector.rows("memory_notes"):
        _reconcile_note(
            vector, note, doomed, who, report,
            redis_client=redis_client, collective_id=collective_id,
            k=k, dry_run=dry_run,
        )

    if not dry_run and doomed:
        vector.delete_where("episodes", _episode_predicate(who))

    report.journal.append({
        "event": "memory_forget",
        "person": who,
        "episodes_removed": report.episodes_removed,
        "notes_deleted": report.notes_deleted,
        "notes_demoted": report.notes_demoted,
        "notes_rebuilt": report.notes_rebuilt,
        "dry_run": dry_run,
        "ts": time.time(),
    })
    logger.info(
        "memory_forget: person=%s episodes=%d deleted=%d demoted=%d rebuilt=%d%s",
        who, report.episodes_removed, report.notes_deleted,
        report.notes_demoted, report.notes_rebuilt,
        " (dry run)" if dry_run else "",
    )
    return report


def _reconcile_note(
    vector: Any,
    note: dict[str, Any],
    doomed: set[str],
    who: str,
    report: ForgetReport,
    *,
    redis_client: Any,
    collective_id: str,
    k: int,
    dry_run: bool,
) -> None:
    note_id = str(note.get("id") or "")
    source_ids = _decode(note.get("source_ids"))
    if not note_id or not source_ids:
        return
    kept = [i for i in source_ids if i not in doomed]
    if len(kept) == len(source_ids):
        return  # this note owes nothing to the erased episodes

    requesters = [
        r for r in _decode(note.get("source_requesters")) if person_of(r) != who
    ]
    people = {person_of(r) for r in requesters if person_of(r)}
    scope = str(note.get("scope") or LOCAL_SCOPE)
    role_label = str(note.get("role_label") or "")

    if not kept:
        # Nothing is left of what the note was drawn from.
        report.notes_deleted += 1
        report.journal.append({
            "event": "note_deleted", "note_id": note_id, "scope": scope,
            "reason": "all sources erased",
        })
        if not dry_run:
            vector.delete_where("memory_notes", f"id = '{_sql_quote(note_id)}'")
        _unpublish(note, redis_client, collective_id, role_label, report, dry_run)
        return

    demote = len(people) < max(1, k) and str(note.get("tier")) == TIER_SHARED
    updated = dict(note)
    updated["source_ids"] = json.dumps(kept)
    updated["source_requesters"] = json.dumps(requesters)
    updated["source_count"] = len(kept)
    if demote:
        updated["tier"] = "private"
        report.notes_demoted += 1
        report.journal.append({
            "event": "note_demoted", "note_id": note_id, "scope": scope,
            "people_left": sorted(people), "quorum": k,
            "reason": "below quorum after erasure",
        })
    else:
        report.notes_rebuilt += 1
        report.journal.append({
            "event": "note_rebuilt", "note_id": note_id, "scope": scope,
            "sources_left": len(kept),
        })

    if not dry_run:
        vector.replace_row("memory_notes", note_id, updated)
    if demote:
        _unpublish(note, redis_client, collective_id, role_label, report, dry_run)


def _unpublish(
    note: dict[str, Any],
    redis_client: Any,
    collective_id: str,
    role_label: str,
    report: ForgetReport,
    dry_run: bool,
) -> None:
    """Pull a note out of every context it was published into.

    Demoting the stored row is not enough on its own: the published copy lives
    in a destination cache that the prompt path reads, and leaving it there
    would mean a note that no longer meets the bar still shaping replies until
    its TTL happened to expire.
    """
    if redis_client is None or not collective_id or not role_label:
        return
    summary = str(note.get("summary") or "").strip()
    if not summary:
        return

    from acc.memory_reflection import _raw_note_entries  # noqa: PLC0415

    for destination in _published_destinations(
        redis_client, collective_id, role_label,
    ):
        key = redis_shared_notes_key(collective_id, role_label, destination)
        entries = _raw_note_entries(redis_client, key)
        remaining = [e for e in entries if e.get("summary") != summary]
        if len(remaining) == len(entries):
            continue
        report.unpublished_from.append(destination)
        report.journal.append({
            "event": "note_unpublished", "destination": destination,
        })
        if dry_run:
            continue
        try:
            redis_client.set(key, json.dumps(remaining))
        except Exception:
            logger.warning(
                "memory_forget: could not unpublish from %s", destination,
                exc_info=True,
            )


def _published_destinations(
    redis_client: Any, collective_id: str, role_label: str,
) -> list[str]:
    """Which destinations this role has published notes into.

    Uses ``scan_iter``/``keys`` when the client offers one. A client with
    neither is reported as nothing found rather than assumed empty — the caller
    sees an empty ``unpublished_from`` and the journal shows no unpublish
    events, so a demotion that could not reach the cache is visible.
    """
    prefix = redis_shared_notes_key(collective_id, role_label, "")
    lister = getattr(redis_client, "scan_iter", None) or getattr(
        redis_client, "keys", None,
    )
    if lister is None:
        return []
    try:
        found = lister(f"{prefix}*")
    except Exception:
        logger.debug("memory_forget: cannot list published keys", exc_info=True)
        return []
    out: list[str] = []
    for key in found or ():
        text = key.decode("utf-8", "replace") if isinstance(key, (bytes, bytearray)) else str(key)
        if text.startswith(prefix):
            out.append(text[len(prefix):])
    return out
