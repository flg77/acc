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
KIND_LOOP = "loop"
KIND_SKILL = "skill"
KIND_NEW_AGENT = "new_agent"
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
    prod_locked: bool = False   # PR-6: refused in prod operator-mode (033 WS-F)


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
    CommandSpec("loop", "Re-run a prompt on an interval", "<30s|5m|2h> <prompt> | stop", "control", prod_locked=True),
    CommandSpec("mode", "Set the operating mode", "<AUTO|PLAN|ACCEPT_EDITS|ASK_PERMISSIONS>", "control"),
    CommandSpec("model", "Show the model for the Target role (--all: every role)", "[--all]", "query"),
    CommandSpec("new-agent", "Scaffold + launch a governed agentset from intent (signed A-BOM)", "<what the agent should do>", "control", prod_locked=True),
    CommandSpec(
        "oversight", "Review the oversight queue", category="oversight",
        subforms=(
            ("pending", "list pending oversight items"),
            ("approve <id>", "approve an oversight item"),
            ("reject <id> <reason>", "reject with a reason"),
        ),
    ),
    CommandSpec("role", "List available roles", "list", "query"),
    CommandSpec("skill", "Ask the active role to use a skill (governed prompt)", "<name> [args]", category="control"),
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


def is_allowed(verb: str, *, dev_mode: bool) -> bool:
    """PR-6 prod/dev gate: whether ``verb`` may run in the current operator
    mode.  ``prod_locked`` verbs are refused in prod (``dev_mode=False``);
    everything is allowed in dev.  Unknown verbs default allowed (parse handles
    them).  The *policy* (which verbs are locked) is the operator's call
    (039 §8 Q4); the shipped default locks ``/loop`` (recurring auto-dispatch).
    """
    v = verb.lstrip("/").lower()
    for c in COMMANDS:
        if c.name == v or v in c.aliases:
            return dev_mode or not c.prod_locked
    return True


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

    # Proposal 039 (PR-6 tail) — per-role skill invocation as a governed prompt.
    # `/skill <name> [args]` does NOT invoke the skill directly: skills are
    # agent-invoked + gated by the capability validator / describe-don't-invoke
    # rule (033 WS-A).  It enqueues a natural-language request to the active
    # role, which flows through the normal cognitive-core + governance path.
    if verb == "skill":
        if not rest:
            return SlashIntent(
                kind=KIND_INVALID,
                error="usage: /skill <name> [args]  (e.g. /skill git commit -am wip)",
            )
        return SlashIntent(
            kind=KIND_SKILL,
            args={"skill": rest[0], "args": " ".join(rest[1:])},
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
        show_all = any(
            r.lower() in ("--all", "-a", "all") for r in rest
        )
        return SlashIntent(kind=KIND_MODEL, args={"all": show_all})

    # Proposal 040 — guided "launch your agent": /new-agent [intent] opens the
    # acc-new-agent onboarding flow (deploy-class → prod-gated).
    if verb == "new-agent":
        return SlashIntent(kind=KIND_NEW_AGENT, args={"intent": " ".join(rest)})

    if verb == "goal":
        return SlashIntent(kind=KIND_GOAL, args={"text": " ".join(rest)})

    if verb == "loop":
        if not rest:
            return SlashIntent(kind=KIND_LOOP, args={"action": "show"})
        if rest[0].lower() == "stop":
            return SlashIntent(kind=KIND_LOOP, args={"action": "stop"})
        tok = rest[0]
        unit = tok[-1:].lower()
        num = tok[:-1]
        if len(rest) < 2 or unit not in ("s", "m", "h") or not num.isdigit():
            return SlashIntent(
                kind=KIND_INVALID,
                error="usage: /loop <30s|5m|2h> <prompt>  |  /loop stop",
            )
        secs = int(num) * {"s": 1, "m": 60, "h": 3600}[unit]
        return SlashIntent(
            kind=KIND_LOOP,
            args={"action": "start", "interval_s": secs, "prompt": " ".join(rest[1:])},
        )

    return SlashIntent(
        kind=KIND_UNKNOWN,
        error=f"unknown command: /{verb} (type /help for the list)",
    )


def skill_invocation_prompt(name: str, args: str = "") -> str:
    """Synthesize the governed natural-language request dispatched to the active
    role when the operator types ``/skill <name> [args]``.

    The operator is *asking the role* to use one of its skills — the role's
    cognitive core + governance still decide whether and how — so this honors
    describe-don't-invoke (033 WS-A) instead of bypassing it.  Pure so it can be
    unit-tested; the prompt screen's KIND_SKILL handler dispatches the result.
    """
    name = name.strip()
    args = args.strip()
    if args:
        return f"Use your '{name}' skill to: {args}"
    return f"Use your '{name}' skill."


def new_agent_intent_prompt(intent: str = "") -> str:
    """Synthesize the governed onboarding request dispatched to the Assistant when
    the operator types ``/new-agent [intent]`` (proposal 040).  The operator is
    *asking the concierge to run the acc-new-agent flow* — elicit the agentset,
    then produce a signed AgentBOM for oversight; nothing deploys without approval.
    Pure so it is unit-tested; the prompt screen's KIND_NEW_AGENT handler
    dispatches the result to the ``assistant`` role.
    """
    intent = intent.strip()
    head = (
        "[ACC NEW-AGENT] Run the acc-new-agent onboarding flow. Elicit the missing "
        "details — roles, per-role model, required packages, deploy target "
        "(rhoai / edge / standalone) and data residency — then produce a signed "
        "AgentBOM plus a collective.yaml for review. Do NOT deploy without "
        "oversight approval (this is a deploy-class action)."
    )
    if intent:
        return f"{head}\nOperator intent: {intent}"
    return f"{head}\nNo intent was given yet — start by asking what the agent should do."


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
