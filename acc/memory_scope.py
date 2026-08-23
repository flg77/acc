"""Which memory an episode belongs to.

Phase 2 of ``20260823-attributed-memory``. Retrieval used to filter on
``agent_id`` alone — right for the single-operator world it was written in, and
the wrong *axis* the moment two people share a surface: same agent, different
person, no filter.

**The mode is applied when an episode is written, not when it is read.** Every
episode carries a scope key, and retrieval is a single equality test against the
key of the task asking. That has two consequences worth stating, because they
are the reason it is done this way:

* Retrieval has no policy logic in it. One comparison, testable in isolation,
  with no way for a mode to be *almost* applied.
* Changing a surface's mode later does not silently re-partition history.
  Episodes written under the old policy keep their old key and stop being
  reachable, rather than being re-sorted into groups nobody consented to. That
  fails closed, which is the direction to fail in.

The five defaults come from the deployment scenarios in the OC-04 analysis, and
they are deliberately not the same. A single "multi-user mode" switch would be
wrong for most of them.
"""

from __future__ import annotations

from typing import Any

from acc.attribution import requester_of

#: One memory for the whole surface. Everyone at the console shares it.
POOLED = "pooled"
#: One memory per person.
PER_REQUESTER = "requester"
#: One memory per room. A channel is a context; the people in it are not.
PER_GROUP = "group"
#: One memory per caller, never pooled and (from Phase 3) never distilled.
ISOLATED = "isolated"

#: The scope for work that never passed admission — an internal reconciliation,
#: an operator at the TUI, anything from before attribution existed. It is a
#: real scope rather than a missing one, so unattributed history stays visible
#: to the operator and cannot leak into an attributed context.
LOCAL_SCOPE = "local"

#: Mode per surface. See the OC-04 deep dive for why these differ.
DEFAULT_MODES: dict[str, str] = {
    "tui": POOLED,
    "webgui": POOLED,
    "slack": PER_GROUP,
    "voice": PER_REQUESTER,
    "compat_endpoint": ISOLATED,
    "webhook": ISOLATED,
    "subscription": ISOLATED,
}

#: A surface nothing has a policy for is isolated, not pooled. The next adapter
#: added is the one most likely to be missing from the table above, and the
#: failure that matters is the one where its callers quietly share a memory.
UNKNOWN_SOURCE_MODE = ISOLATED


def resolve_mode(source: str) -> str:
    """The scoping mode for *source*."""
    return DEFAULT_MODES.get(str(source or "").strip().lower(), UNKNOWN_SOURCE_MODE)


def _source_of(task_payload: dict[str, Any]) -> str:
    """Which surface a task arrived on.

    Adapters do not all stamp the same keys — ``channel_access`` emits
    ``requester_source`` and ``requester_channel``, the compat endpoint emits
    only the former, and a prefix on ``requested_by`` is the last resort. Read
    all three rather than assume, because a missed source falls back to
    :data:`LOCAL_SCOPE` and pools external work with the operator's own.
    """
    for key in ("requester_source", "requester_channel"):
        value = str(task_payload.get(key) or "").strip()
        if value:
            # Case-folded, so a differently-spelled adapter cannot open a
            # second scope for the same surface -- resolve_mode() folds too,
            # and the two disagreeing is how one surface ends up with two
            # memories that behave alike.  Only the SOURCE is folded: a
            # channel id is the platform's to case, not ours.
            return value.lower()
    who = str(task_payload.get("requested_by") or "").strip()
    return who.split(":", 1)[0].lower() if ":" in who else ""


def _who(task_payload: dict[str, Any], source: str) -> str:
    """The requester, without repeating the source it already starts with.

    ``Principal.attribution()`` renders as ``slack:U123@C1``, so composing a key
    naively yields ``slack@slack:U123``. The key is opaque to the code and not
    to the operator reading a column, so the duplication is dropped.
    """
    who = requester_of(task_payload)
    prefix = source + ":"
    return who[len(prefix):] if who.startswith(prefix) else who


def scope_key(task_payload: dict[str, Any] | None) -> str:
    """The memory this task's episode belongs to."""
    if not isinstance(task_payload, dict):
        return LOCAL_SCOPE
    source = _source_of(task_payload)
    if not source:
        return LOCAL_SCOPE

    mode = resolve_mode(source)
    if mode == POOLED:
        return source

    if mode == PER_GROUP:
        group = str(task_payload.get("requester_scope") or "").strip()
        if group and group.lower() != "direct":
            return f"{source}#{group}"
        # A direct message is not a room. Keying it on the group would pool
        # every DM on the platform into one memory, which is the opposite of
        # what a private conversation is.
        return f"{source}@{_who(task_payload, source)}"

    return f"{source}@{_who(task_payload, source)}"


def source_of_scope(scope: str) -> str:
    """The surface a scope key came from.

    Keys are built here and take three shapes — ``source``, ``source#group``
    and ``source@who`` — so the source is everything before the first
    separator. Kept next to :func:`scope_key` on purpose: the two have to agree,
    and they will not stay in agreement if they live apart.
    """
    text = str(scope or "").strip() or LOCAL_SCOPE
    for sep in ("#", "@"):
        if sep in text:
            return text.split(sep, 1)[0]
    return text


def is_distillable(scope: str) -> bool:
    """Whether episodes in *scope* may be folded into a durable note.

    Isolated surfaces — the compat endpoint, webhooks, subscriptions — are
    excluded. **Anything that can prompt the collective must not be able to
    write what every future prompt reads**, and unattended ingress is the
    cheapest way in: nobody is watching, and the poisoning is invisible until
    it has been read a thousand times.

    Note what this deliberately does *not* exclude: the operator's own
    ``local`` scope, which is unattributed because the TUI never passes through
    admission. Excluding unattributed episodes outright — the obvious reading —
    would switch reflection off for every single-operator deployment, which is
    to say for the case it was built for. The unattributed material is instead
    held back at the *promotion* boundary, where a quorum counts distinct
    people and finds none.
    """
    text = str(scope or "").strip() or LOCAL_SCOPE
    if text == LOCAL_SCOPE:
        # The operator's own scope is not a surface and is not in the mode
        # table, so it would otherwise fall through to the unknown-source
        # default and be treated as isolated -- switching reflection off for
        # every single-operator deployment.
        return True
    return resolve_mode(source_of_scope(text)) != ISOLATED


def row_scope(row: dict[str, Any] | None) -> str:
    """The scope of a stored row.

    A row written before the column existed reads as :data:`LOCAL_SCOPE` — the
    same as the backfill — so pre-attribution history stays where the operator
    can still reach it and nowhere else.
    """
    if not isinstance(row, dict):
        return LOCAL_SCOPE
    return str(row.get("scope") or "").strip() or LOCAL_SCOPE
