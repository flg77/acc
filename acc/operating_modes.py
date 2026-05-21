"""Operating modes (PR-L, D-003) — operator-controlled autonomy gate.

Four modes, all of which respect Cat-A constitutional rules
unconditionally:

* ``AUTO``               — today's behaviour.  Cat-A blocks, Cat-B
  observes, every other invocation runs.  Default.
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
# Per-mode gate decision
# ---------------------------------------------------------------------------


def should_gate_invocation(
    mode: str,
    *,
    kind: str,
    target: str,
    risk_level: str = "MEDIUM",
) -> bool:
    """Return True iff this invocation must be funneled through the
    human oversight queue under *mode*.

    Args:
        mode: Operating mode string.  Unknown modes fall back to AUTO.
        kind: Invocation kind (``"skill"`` / ``"mcp"``).
        target: Invocation target (skill_id / mcp tool name).
        risk_level: Manifest-declared risk level.  In AUTO mode only
            CRITICAL is gated (today's behaviour); other modes adjust.

    Returns:
        ``True`` → submit to the oversight queue and block until
        APPROVE / REJECT.
        ``False`` → dispatch immediately.
    """
    mode = normalise(mode)
    if mode == MODE_ASK_PERMISSIONS:
        return True
    if mode == MODE_ACCEPT_EDITS:
        return is_write_action(kind, target) or str(risk_level).upper() == "CRITICAL"
    # PLAN never reaches this — dispatch is skipped before the call.
    # AUTO: only CRITICAL gated (matches Phase 4.5 behaviour).
    return str(risk_level).upper() == "CRITICAL"
