"""Operating modes (PR-L, D-003) — operator-controlled autonomy gate.

Four modes, all of which respect Cat-A constitutional rules
unconditionally:

* ``AUTO``               — Cat-A blocks, Cat-B observes, every other
  invocation runs — except CRITICAL ones and the two *gate categories*
  (system access, acting on the user's behalf), which are asked.
  Default.
* ``ASK_PERMISSIONS``    — every capability invocation
  (``[SKILL:…]`` / ``[MCP:…]``) is funneled through the human
  oversight queue.  Slowest; maximum operator control.
* ``ACCEPT_EDITS``       — read-only / pure-compute invocations
  run immediately; "write" / "edit" / "delete" / "spawn" actions
  are funneled through oversight.
* ``PLAN``               — the agent emits a PLAN signal listing
  the actions it would take but DOES NOT execute anything.  The
  operator reviews via the Comms ACTIVE PLAN pane and can approve
  the plan via the Compliance pane (a future hook; for now the
  reply just describes the plan).

The modes adjust **what fires the oversight queue**, NOT what fires
the Cat-A guardrails.  Cat-A guardrails are constitutional and
identical across every mode — PR-L preserves that invariant with a
dedicated test (``test_operating_mode_constitutional_invariant``).

The mode flows from the operator's prompt-screen choice through
``task_payload["operating_mode"]`` into
:class:`acc.cognitive_core.CognitiveCore`, which forwards it to
:func:`acc.capability_dispatch.dispatch_invocations`.  A role can
declare its own preferred default via
``role.default_operating_mode`` so the Nucleus Apply form can
prefill it.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Mode constants
# ---------------------------------------------------------------------------

MODE_AUTO: Final[str] = "AUTO"
MODE_PLAN: Final[str] = "PLAN"
MODE_ACCEPT_EDITS: Final[str] = "ACCEPT_EDITS"
MODE_ASK_PERMISSIONS: Final[str] = "ASK_PERMISSIONS"

ALL_MODES: Final[tuple[str, ...]] = (
    MODE_AUTO,
    MODE_PLAN,
    MODE_ACCEPT_EDITS,
    MODE_ASK_PERMISSIONS,
)


def normalise(mode: str | None) -> str:
    """Coerce a mode string to canonical form (upper-case, defaulted
    to AUTO).  Unknown modes fall back to AUTO so a typo can't
    accidentally weaken the gate (AUTO still respects Cat-A)."""
    if not mode:
        return MODE_AUTO
    candidate = str(mode).strip().upper()
    if candidate in ALL_MODES:
        return candidate
    return MODE_AUTO


# ---------------------------------------------------------------------------
# Write-action classifier (for ACCEPT_EDITS)
# ---------------------------------------------------------------------------


# Substrings on the invocation target that mark it as a "write"
# action under ACCEPT_EDITS.  Case-insensitive substring match.
# Errs on the safe side — when in doubt, the action is gated.  Roles
# that need a write action to bypass the gate should be running in
# AUTO mode for that task.
_WRITE_MARKERS: Final[tuple[str, ...]] = (
    "write", "edit", "delete", "destroy", "drop", "rm",
    "create", "spawn", "publish", "send", "post",
    "modify", "update", "patch", "remove", "kill",
    "execute", "exec_", "run_", "shell", "system",
    "deploy", "rollout",
)


def is_write_action(kind: str, target: str) -> bool:
    """Heuristic — does *target* look like a side-effecting action?

    Used by ACCEPT_EDITS to decide which invocations need the
    oversight gate.  AUTO never calls this; ASK_PERMISSIONS gates
    every action regardless; PLAN never reaches the dispatch.

    Conservative — when in doubt, returns True so the operator
    decides.  Roles that legitimately need a target like
    ``execute_query`` to bypass the gate can switch to AUTO for
    that task.

    Note: only ``target`` is checked.  ``kind`` is excluded
    because the literal word ``"skill"`` substring-matches the
    ``"kill"`` marker, flagging every skill invocation as a
    write action.  ``kind`` carries no action semantic anyway
    (it just disambiguates the resolver path).
    """
    haystack = str(target or "").lower()
    return any(marker in haystack for marker in _WRITE_MARKERS)


# ---------------------------------------------------------------------------
# Gate categories (`20260902-assistant-autonomy-prompt-pane-approvals` 1.2)
# ---------------------------------------------------------------------------
#
# Orthogonal to ``risk_level``.  A curated infuse or a specialist hand-off
# executes under AUTO (1.1); what the operator IS asked about is anything
# that reaches the host or acts in the operator's name.  A manifest declares
# ``system_access`` / ``acts_on_behalf`` explicitly; one that declares
# neither inherits from the name table below, so a third-party skill cannot
# escape the gate by omission.  A declared ``false`` opts out (a read-only
# skill whose name happens to contain "send").

CATEGORY_SYSTEM_ACCESS: Final[str] = "system_access"
CATEGORY_ACTS_ON_BEHALF: Final[str] = "acts_on_behalf"

_SYSTEM_ACCESS_MARKERS: Final[tuple[str, ...]] = (
    "shell", "exec", "fs_write", "system", "deploy", "rollout", "sudo", "kill",
)
_ACTS_ON_BEHALF_MARKERS: Final[tuple[str, ...]] = (
    "send", "post", "publish", "mail", "message", "reply", "tweet", "notify",
)


def _declared(manifest: object, attr: str) -> bool | None:
    """A manifest's explicit flag, or None when undeclared.  Anything that is
    not a real bool (a MagicMock, a string) counts as undeclared."""
    val = getattr(manifest, attr, None) if manifest is not None else None
    return val if isinstance(val, bool) else None


def gate_categories(kind: str, target: str, manifest: object = None) -> frozenset[str]:
    """Return the gate categories that apply to one invocation.

    Declared flags on *manifest* win; an undeclared flag falls back to the
    name table on *target* (``"shell_exec"``, ``"google_workspace.gmail_send"``).
    ``kind`` is accepted for symmetry with :func:`should_gate_invocation`
    and not consulted -- see :func:`is_write_action` for why.
    """
    del kind
    haystack = str(target or "").lower()
    system = _declared(manifest, CATEGORY_SYSTEM_ACCESS)
    if system is None:
        system = any(m in haystack for m in _SYSTEM_ACCESS_MARKERS)
    behalf = _declared(manifest, CATEGORY_ACTS_ON_BEHALF)
    if behalf is None:
        behalf = any(m in haystack for m in _ACTS_ON_BEHALF_MARKERS)
    cats = set()
    if system:
        cats.add(CATEGORY_SYSTEM_ACCESS)
    if behalf:
        cats.add(CATEGORY_ACTS_ON_BEHALF)
    return frozenset(cats)


# ---------------------------------------------------------------------------
# Destructive functions (`20260911-question-envelope`)
# ---------------------------------------------------------------------------
#
# A call that deletes or overwrites data is asked about in every mode but PLAN
# (operator, 2026-09-11: "super high critical functions like file deletion, data
# modification ... are double checked as questions to the user").  Three sources
# of evidence, strongest first:
#
# * the COMMAND an exec skill is about to run -- checked whatever the manifest
#   declares, because it is what will actually happen;
# * a declared flag: ``destructive`` on a skill, ``destructive_tools`` on an MCP;
# * the target's name, when nothing is declared (the gate_categories rule).

_EXEC_SKILLS: Final[frozenset[str]] = frozenset({"shell_exec", "ssh_exec", "python_exec"})

_DESTRUCTIVE_NAME_MARKERS: Final[tuple[str, ...]] = (
    "delete", "remove", "drop", "destroy", "purge", "wipe", "truncate",
    "rmdir", "unlink",
)

# Where a shell command word can start: the beginning, after a separator or a
# subshell opener, or after sudo / xargs and their flags.
_AT_CMD = r"(?:^|[;&|(`\n]|\$\()\s*(?:sudo\s+(?:-\S+\s+)*)?(?:xargs\s+(?:-\S+\s+)*)?"
# The rest of one simple command.
_REST = r"[^;&|\n]*"

_DESTRUCTIVE_COMMANDS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p) for p in (
        _AT_CMD + r"(?:\S*/)?(?:rm|rmdir|unlink|shred|wipefs|truncate|mkfs(?:\.\w+)?)\b" + _REST,
        r"\bfind\b" + r"[^;&|\n]*\s-delete\b",
        r"\bdd\b[^;&|\n]*\bof=" + _REST,
        r"\bgit\s+push\b[^;&|\n]*\s(?:--force(?:-with-lease)?|-f)\b" + _REST,
        r"\bgit\s+reset\b[^;&|\n]*\s--hard\b" + _REST,
        r"\bgit\s+clean\b[^;&|\n]*\s-\w*f" + _REST,
        r"\bgit\s+branch\b[^;&|\n]*\s(?:-[dD]|--delete)\b" + _REST,
        r"\b(?:kubectl|oc)\b[^;&|\n]*\sdelete\b" + _REST,
        r"\b(?:podman|docker)\b[^;&|\n]*\s(?:rm|rmi|prune)\b" + _REST,
        r"(?i)\bdrop\s+(?:table|database|schema|index|view)\b" + _REST,
        r"(?i)\btruncate\s+table\b" + _REST,
        r"(?i)\bdelete\s+from\b" + _REST,
        r"(?i)\bupdate\s+\S+\s+set\b" + _REST,
        r"\bos\.(?:remove|unlink|rmdir|removedirs)\s*\(" + _REST,
        r"\bshutil\.rmtree\s*\(" + _REST,
        r"\.(?:unlink|rmdir)\s*\(" + _REST,
    )
)


def _command_text(target: str, args: object) -> str:
    """What an exec skill is about to run: ``cmd``, ``argv`` or ``code``."""
    if not isinstance(args, dict):
        return ""
    if target == "python_exec":
        return str(args.get("code") or "")
    argv = args.get("argv")
    if isinstance(argv, list) and argv:
        return " ".join(str(a) for a in argv)
    return str(args.get("cmd") or "")


def destructive_evidence(
    kind: str, target: str, args: object = None, manifest: object = None,
) -> str:
    """Why this call deletes or overwrites data, or ``""`` when it does not.

    The evidence is what the question shows the operator: the matched command
    (``rm -rf build/``), or the function and why it counts."""
    name = str(target or "")
    if kind == "skill" and name in _EXEC_SKILLS:
        text = _command_text(name, args)
        for pattern in _DESTRUCTIVE_COMMANDS:
            m = pattern.search(text)
            if m:
                found = m.group(0).strip().lstrip(";&|(`$").strip()
                return found if len(found) <= 120 else found[:117] + "..."
    tool = name.partition(".")[2] if kind == "mcp" else name
    if kind == "mcp":
        declared_tools = getattr(manifest, "destructive_tools", None) if manifest is not None else None
        if isinstance(declared_tools, list) and tool in declared_tools:
            return f"{name} (declared destructive by its manifest)"
    else:
        declared = _declared(manifest, "destructive")
        if declared is True:
            return f"{name} (declared destructive by its manifest)"
        if declared is False:
            return ""
    if any(m in tool.lower() for m in _DESTRUCTIVE_NAME_MARKERS):
        return f"{name} (its name says it deletes)"
    return ""


# ---------------------------------------------------------------------------
# Per-mode gate decision
# ---------------------------------------------------------------------------


def should_gate_invocation(
    mode: str,
    *,
    kind: str,
    target: str,
    risk_level: str = "MEDIUM",
    categories: frozenset[str] | None = None,
) -> bool:
    """Return True iff this invocation must be funneled through the
    human oversight queue under *mode*.

    Args:
        mode: Operating mode string.  Unknown modes fall back to AUTO.
        kind: Invocation kind (``"skill"`` / ``"mcp"``).
        target: Invocation target (skill_id / mcp tool name).
        risk_level: Manifest-declared risk level.  CRITICAL is gated in
            every mode.
        categories: The invocation's gate categories from
            :func:`gate_categories`.  ``None`` computes the name-table
            default from *target* (no manifest).  Non-empty → gated in
            AUTO and ACCEPT_EDITS.

    Returns:
        ``True`` → submit to the oversight queue and block until
        APPROVE / REJECT.
        ``False`` → dispatch immediately.
    """
    mode = normalise(mode)
    if mode == MODE_ASK_PERMISSIONS:
        return True
    if categories is None:
        categories = gate_categories(kind, target)
    critical = str(risk_level).upper() == "CRITICAL"
    if mode == MODE_ACCEPT_EDITS:
        return is_write_action(kind, target) or critical or bool(categories)
    # PLAN never reaches this — dispatch is skipped before the call.
    # AUTO: CRITICAL, plus the two gate categories (1.2).
    return critical or bool(categories)
