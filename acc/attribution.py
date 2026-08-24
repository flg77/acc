"""Who produced a piece of memory.

The requester is resolved once, at admission (:mod:`acc.channel_access`), and
stamped onto the task. Everything downstream — the episode, the session, the
note distilled from them — has to carry it forward or the attribution is lost
one hop in, which is what happened before this module existed.

**The sentinel is the part worth reading.** A row with no requester is not a row
belonging to whoever happens to be asking now. It is a row from before
attribution existed, or from a surface that did not stamp one. Left as an empty
string it would eventually be compared equal to something — an empty scope, a
missing field, another blank row — so it is named instead, and
:func:`is_attributed` is the only thing that decides whether a value counts as a
person.

That distinction is what makes the later quorum honest: a lesson is corroborated
by *people*, and an unattributed row is not a person.
"""

from __future__ import annotations

from typing import Any, Iterable

#: A row whose requester is unknown. Never equal to a real principal, and never
#: counted as one.
UNATTRIBUTED = "unattributed"


def is_attributed(requester: Any) -> bool:
    """True when *requester* names someone.

    Blank, ``None`` and the sentinel are all "nobody in particular" — the
    distinction between them is history, not authority.
    """
    text = str(requester or "").strip()
    return bool(text) and text != UNATTRIBUTED


def requester_of(task_payload: dict[str, Any] | None) -> str:
    """The requester a task was admitted for, or :data:`UNATTRIBUTED`.

    ``requested_by`` is what :meth:`acc.channel_access.Admission.task_attribution`
    stamps. A task that never passed through admission — an internal
    reconciliation, a pre-v0.8.0 payload — has none, and says so.
    """
    if not isinstance(task_payload, dict):
        return UNATTRIBUTED
    value = task_payload.get("requested_by")
    return str(value).strip() if is_attributed(value) else UNATTRIBUTED


def row_requester(row: dict[str, Any] | None) -> str:
    """The requester on a stored row, normalised.

    A legacy row predates the column and reads ``None``; a backfilled row
    carries the sentinel already. Both mean the same thing, and callers should
    not have to know which they got.
    """
    if not isinstance(row, dict):
        return UNATTRIBUTED
    value = row.get("requester")
    return str(value).strip() if is_attributed(value) else UNATTRIBUTED


def person_of(requester: Any) -> str:
    """The *person* behind a requester string.

    :meth:`acc.identity.Principal.attribution` renders as
    ``source:subject@scope``, so the same human asking in two channels produces
    two different requester strings. Counting those as two people would let a
    quorum of two be satisfied by one person talking to themselves in a second
    room -- which is exactly the thing a quorum exists to prevent.

    Clustering is confined to one scope (Phase 3), so within a single note the
    scope suffix is constant and the distinction does not currently bite. It is
    stripped anyway, because relying on that argument means the count is correct
    by coincidence rather than by construction, and the coincidence ends the
    first time anything aggregates across scopes.
    """
    text = str(requester or "").strip()
    return text.split("@", 1)[0] if "@" in text else text


def distinct_people(rows: Iterable[dict[str, Any]]) -> list[str]:
    """The distinct humans behind *rows*, ignoring which room they spoke in."""
    seen: list[str] = []
    for row in rows or ():
        who = person_of(row_requester(row))
        if who != UNATTRIBUTED and who not in seen:
            seen.append(who)
    return seen


def people_in(requesters: Iterable[Any]) -> list[str]:
    """Distinct people from a list of requester strings (e.g. a note's)."""
    seen: list[str] = []
    for requester in requesters or ():
        who = person_of(requester)
        if is_attributed(who) and who not in seen:
            seen.append(who)
    return seen


def distinct_requesters(rows: Iterable[dict[str, Any]]) -> list[str]:
    """The distinct *people* behind *rows*, in first-seen order.

    Unattributed rows contribute nothing. This is deliberately not "the number
    of rows": ten episodes from one person are one person's account, and a
    quorum that cannot tell those apart is not a quorum.
    """
    seen: list[str] = []
    for row in rows or ():
        who = row_requester(row)
        if who != UNATTRIBUTED and who not in seen:
            seen.append(who)
    return seen
