"""Operator slash-command parser for the prompt pane (PR-5).

When the operator types ``/<verb> <args>`` in the prompt textarea,
the prompt screen routes the input through :func:`parse` instead of
the normal LLM dispatch path.  Recognised commands return a
:class:`SlashIntent` describing the action to take; unrecognised
input falls back to a free-form prompt (back-compat — every existing
keystroke pattern still works).

Supported verbs (initial set):

* ``/help``                       — list verbs in the transcript.
* ``/cancel <task_id|cluster_id>`` — publish TASK_CANCEL.
* ``/cluster show [<cid>]``       — render snapshot in transcript.
* ``/cluster kill <cid>``         — cancel every member of a cluster.
* ``/role list``                  — list roles in transcript.
* ``/skills``                     — list active skills for current target.
* ``/oversight pending``          — list pending oversight items.
* ``/oversight approve <id>``     — publish OVERSIGHT_DECISION approve.
* ``/oversight reject <id> <reason>`` — publish OVERSIGHT_DECISION reject.

Design notes:

* The parser is **pure**.  No I/O, no asyncio.  It returns an intent;
  the screen interprets it.  This keeps tests fast and the screen
  side trivially mockable.
* Unknown verbs return a ``SlashIntent`` of kind ``unknown`` — never
  raise.  The screen renders the unknown-command message in the
  transcript so operators learn from typos.
* Empty / whitespace-only ``/`` lines map to ``help``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Intent types
# ---------------------------------------------------------------------------


@dataclass
class SlashIntent:
    """Parsed slash command.

    ``kind`` is the routing key the screen dispatches on.  ``args``
    carries the verb-specific parameters; the screen reads them by
    name so renames stay easy.
    """

    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    """Non-empty for ``unknown`` / ``invalid`` intents — rendered as a
    system message in the transcript so the operator sees what went
    wrong without leaving the screen."""


# Concrete intent kinds — exposed as module constants so tests +
# screen handlers reference them by name.
KIND_HELP = "help"
KIND_CANCEL = "cancel"
KIND_CLUSTER_SHOW = "cluster_show"
KIND_CLUSTER_KILL = "cluster_kill"
KIND_ROLE_LIST = "role_list"
KIND_SKILLS = "skills"
KIND_OVERSIGHT_PENDING = "oversight_pending"
KIND_OVERSIGHT_APPROVE = "oversight_approve"
KIND_OVERSIGHT_REJECT = "oversight_reject"
KIND_UNKNOWN = "unknown"
KIND_INVALID = "invalid"
KIND_NOT_SLASH = "not_slash"
"""Returned when input doesn't start with ``/`` — caller continues
the normal prompt-dispatch path."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse(text: str) -> SlashIntent:
    """Parse one prompt input.  Returns a :class:`SlashIntent`.

    Whitespace-only / empty input → ``KIND_NOT_SLASH``: the prompt
    screen treats it the same as the legacy "type a prompt first"
    nag.

    Input not starting with ``/`` → ``KIND_NOT_SLASH``: caller
    proceeds with regular LLM dispatch.
    """
    stripped = text.strip()
    if not stripped:
        return SlashIntent(kind=KIND_NOT_SLASH)
    if not stripped.startswith("/"):
        return SlashIntent(kind=KIND_NOT_SLASH)

    body = stripped[1:].strip()
    if not body:
        return SlashIntent(kind=KIND_HELP)

    parts = body.split()
    verb = parts[0].lower()
    rest = parts[1:]

    if verb == "help":
        return SlashIntent(kind=KIND_HELP)

    if verb == "cancel":
        if not rest:
            return SlashIntent(
                kind=KIND_INVALID,
                error="usage: /cancel <task_id|cluster_id>",
            )
        target = rest[0]
        # Cluster ids carry the c- prefix per acc.cluster.new_cluster_id.
        if target.startswith("c-"):
            return SlashIntent(
                kind=KIND_CLUSTER_KILL, args={"cluster_id": target},
            )
        return SlashIntent(kind=KIND_CANCEL, args={"task_id": target})

    if verb == "cluster":
        if not rest:
            return SlashIntent(
                kind=KIND_INVALID,
                error="usage: /cluster show [<cid>] | /cluster kill <cid>",
            )
        sub = rest[0].lower()
        cluster_id = rest[1] if len(rest) > 1 else ""
        if sub == "show":
            return SlashIntent(
                kind=KIND_CLUSTER_SHOW, args={"cluster_id": cluster_id},
            )
        if sub == "kill":
            if not cluster_id:
                return SlashIntent(
                    kind=KIND_INVALID, error="usage: /cluster kill <cid>",
                )
            return SlashIntent(
                kind=KIND_CLUSTER_KILL, args={"cluster_id": cluster_id},
            )
        return SlashIntent(
            kind=KIND_INVALID,
            error=f"unknown cluster subcommand: {sub!r}",
        )

    if verb == "role":
        if rest and rest[0].lower() == "list":
            return SlashIntent(kind=KIND_ROLE_LIST)
        return SlashIntent(
            kind=KIND_INVALID, error="usage: /role list",
        )

    if verb == "skills":
        return SlashIntent(kind=KIND_SKILLS)

    if verb == "oversight":
        if not rest:
            return SlashIntent(
                kind=KIND_INVALID,
                error="usage: /oversight pending | approve <id> | reject <id> <reason>",
            )
        sub = rest[0].lower()
        if sub == "pending":
            return SlashIntent(kind=KIND_OVERSIGHT_PENDING)
        if sub == "approve":
            if len(rest) < 2:
                return SlashIntent(
                    kind=KIND_INVALID, error="usage: /oversight approve <id>",
                )
            return SlashIntent(
                kind=KIND_OVERSIGHT_APPROVE, args={"oversight_id": rest[1]},
            )
        if sub == "reject":
            if len(rest) < 3:
                return SlashIntent(
                    kind=KIND_INVALID,
                    error="usage: /oversight reject <id> <reason>",
                )
            reason = " ".join(rest[2:])
            return SlashIntent(
                kind=KIND_OVERSIGHT_REJECT,
                args={"oversight_id": rest[1], "reason": reason},
            )
        return SlashIntent(
            kind=KIND_INVALID, error=f"unknown oversight subcommand: {sub!r}",
        )

    return SlashIntent(
        kind=KIND_UNKNOWN,
        error=f"unknown command: /{verb} (type /help for the list)",
    )


# ---------------------------------------------------------------------------
# Help text — rendered when KIND_HELP fires
# ---------------------------------------------------------------------------


HELP_TEXT = (
    "Slash commands:\n"
    "  /help                                — this list\n"
    "  /cancel <task_id|cluster_id>         — cancel a task or cluster\n"
    "  /cluster show [<cid>]                — render cluster snapshot\n"
    "  /cluster kill <cid>                  — cancel every cluster member\n"
    "  /role list                           — list available roles\n"
    "  /skills                              — list skills for current target\n"
    "  /oversight pending                   — list pending oversight items\n"
    "  /oversight approve <id>              — approve an oversight item\n"
    "  /oversight reject <id> <reason>      — reject with a reason"
)
