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
# Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1 — Assistant
# sleep/wake from the Prompt screen.  Args carry ``"action": "sleep"|"wake"``.
KIND_ASSISTANT_CONTROL = "assistant_control"
# Proposal 039 (PR-3) — inspection/config palette verbs.
KIND_STATUS = "status"
KIND_MODE = "mode"
KIND_CLEAR = "clear"
# Proposal 039 (PR-4) — catalog/model read-only verbs.
KIND_CATALOG = "catalog"
KIND_MODEL = "model"
# Proposal 039 (PR-5) — pinned objective.
KIND_GOAL = "goal"
KIND_UNKNOWN = "unknown"
KIND_INVALID = "invalid"
KIND_NOT_SLASH = "not_slash"
"""Returned when input doesn't start with ``/`` — caller continues
the normal prompt-dispatch path."""


# ---------------------------------------------------------------------------
# Command registry — single source of truth for *discovery* (proposal 039)
# ---------------------------------------------------------------------------
#
# The prompt palette (interactive ``/`` autocomplete) and the generated
# ``/help`` both read this table, so the verb list lives in exactly one place.
# ``parse`` below still owns argument *parsing* (each verb shapes its args
# differently); ``test_registry_verbs_are_all_known_to_parse`` asserts the two
# never drift.  Keep the list ALPHABETICAL by name so the palette's default
# order needs no re-sort.


@dataclass(frozen=True)
class CommandSpec:
    """Discovery metadata for one top-level slash verb.

    ``arg_hint`` is the inline placeholder shown for single-shape verbs;
    ``subforms`` lists ``(signature, summary)`` pairs for verbs with
    sub-commands (cluster, oversight) so the generated help documents each.
    """

    name: str
    summary: str
    arg_hint: str = ""
    category: str = "general"   # query | control | oversight | general
    aliases: tuple[str, ...] = ()
    subforms: tuple[tuple[str, str], ...] = ()


COMMANDS: list[CommandSpec] = [
    CommandSpec("cancel", "Cancel a task or cluster", "<task_id|cluster_id>", "control"),
    CommandSpec("catalog", "Browse the role/package catalog", "[<@scope|filter>]", "query"),
    CommandSpec("clear", "Clear the transcript", category="control"),
    CommandSpec(
        "cluster", "Inspect or kill a cluster", category="query",
        subforms=(
            ("show [<cid>]", "render cluster snapshot"),
            ("kill <cid>", "cancel every cluster member"),
        ),
    ),
    CommandSpec("goal", "Set a pinned objective (prepended to prompts)", "[<text> | clear]", "control"),
    CommandSpec("help", "List the available commands", category="general"),
    CommandSpec("mode", "Set the operating mode", "<AUTO|PLAN|ACCEPT_EDITS|ASK_PERMISSIONS>", "control"),
    CommandSpec("model", "List the models.yaml registry", category="query"),
    CommandSpec(
        "oversight", "Review the oversight queue", category="oversight",
        subforms=(
            ("pending", "list pending oversight items"),
            ("approve <id>", "approve an oversight item"),
            ("reject <id> <reason>", "reject with a reason"),
        ),
    ),
    CommandSpec("role", "List available roles", "list", "query"),
    CommandSpec("skills", "List skills for the current target", category="query"),
    CommandSpec("sleep", "Assistant → dormant-watcher mode", category="control"),
    CommandSpec("status", "Show prompt state (role/mode/workspace)", category="query"),
    CommandSpec("wake", "Wake the Assistant (also Ctrl+Z toggle)", category="control"),
]


def complete(buffer: str) -> list[CommandSpec]:
    """Commands whose name (or alias) prefix-matches the typed ``buffer``
    (e.g. ``"/ov"`` → oversight), **alphabetical by name**.

    Case-insensitive.  A bare ``"/"`` (or empty) returns every command.
    Drives the prompt's interactive ``/`` palette (proposal 039).
    """
    p = buffer.lstrip("/").lower().strip()
    matches = [
        c for c in COMMANDS
        if not p or c.name.startswith(p) or any(a.startswith(p) for a in c.aliases)
    ]
    return sorted(matches, key=lambda c: c.name)


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

    # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1.
    if verb in ("sleep", "wake"):
        return SlashIntent(
            kind=KIND_ASSISTANT_CONTROL, args={"action": verb},
        )

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

    # Proposal 039 (PR-3) — inspection/config verbs.
    if verb == "clear":
        return SlashIntent(kind=KIND_CLEAR)

    if verb == "status":
        return SlashIntent(kind=KIND_STATUS)

    if verb == "mode":
        if not rest:
            return SlashIntent(
                kind=KIND_INVALID,
                error="usage: /mode <AUTO|PLAN|ACCEPT_EDITS|ASK_PERMISSIONS>",
            )
        m = rest[0].upper()
        if m not in ("AUTO", "PLAN", "ACCEPT_EDITS", "ASK_PERMISSIONS"):
            return SlashIntent(
                kind=KIND_INVALID,
                error=f"unknown mode {rest[0]!r}; valid: AUTO PLAN ACCEPT_EDITS ASK_PERMISSIONS",
            )
        return SlashIntent(kind=KIND_MODE, args={"mode": m})

    if verb == "catalog":
        return SlashIntent(kind=KIND_CATALOG, args={"filter": " ".join(rest)})

    if verb == "model":
        return SlashIntent(kind=KIND_MODEL)

    if verb == "goal":
        return SlashIntent(kind=KIND_GOAL, args={"text": " ".join(rest)})

    return SlashIntent(
        kind=KIND_UNKNOWN,
        error=f"unknown command: /{verb} (type /help for the list)",
    )


# ---------------------------------------------------------------------------
# Help text — GENERATED from COMMANDS (stays in sync automatically)
# ---------------------------------------------------------------------------


def _render_help() -> str:
    """Render ``/help`` from the COMMANDS registry — one aligned row per verb
    (or per sub-form for cluster/oversight), alphabetical by verb."""
    rows: list[tuple[str, str]] = []
    for c in sorted(COMMANDS, key=lambda c: c.name):
        if c.subforms:
            rows.extend((f"/{c.name} {sig}", sub) for sig, sub in c.subforms)
        else:
            rows.append((f"/{c.name} {c.arg_hint}".rstrip(), c.summary))
    width = max(len(sig) for sig, _ in rows)
    lines = ["Slash commands:"]
    lines += [f"  {sig:<{width}}  — {summary}" for sig, summary in rows]
    return "\n".join(lines)


HELP_TEXT = _render_help()
