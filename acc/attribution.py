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
