"""Parser + dispatcher for skill / MCP-tool markers in LLM output.

When the LLM finishes a task it can request one or more capability
invocations by embedding markers in its response text:

    [SKILL: <skill_id> <json args>]
    [MCP: <server_id>.<tool_name> <json args>]

This module:

1. Extracts every marker from a result's ``output`` text via two
   strict regexes (no greedy outer brackets — the JSON payload may
   contain ``]`` inside strings, so the regex stops at the first
   *balanced* trailing ``]`` we can locate).
2. Dispatches each one through
   :meth:`acc.cognitive_core.CognitiveCore.invoke_skill` /
   :meth:`acc.cognitive_core.CognitiveCore.invoke_mcp_tool` so Cat-A
   A-017 and A-018 fire before the adapter runs.
3. Returns a list of :class:`InvocationOutcome` records the agent's
   task loop can fold into the ``TASK_COMPLETE`` payload (so the
   arbiter sees what tools fired and what they returned).

Why a separate module instead of folding into ``process_task``: the
parsing step is purely textual and the dispatch step is purely
governance — keeping both out of the LLM-call hot path means the
existing pipeline (PRE-GATE → LLM → POST-GATE → DRIFT) stays
untouched and we add capability execution as an *aftermarket* concern
the agent loop opts into per task.

Marker grammar (strict — case-sensitive, single-line per marker):

    [SKILL: lowercase_snake_id { ... json ... }]
    [SKILL: lowercase_snake_id]                  # args default to {}
    [MCP: server_id.tool.name { ... json ... }]
    [MCP: server_id.tool.name]                   # args default to {}

The JSON payload is parsed with the standard library — no
permissive single-quote handling, because LLMs that emit JSON-shaped
content reliably emit valid JSON when prompted.  Malformed payloads
yield a :class:`InvocationOutcome` with ``error="json_decode"`` and
the offending substring; the marker is NOT dispatched.

Tool-name grammar for MCP markers: dot-separated identifier where the
first segment is the ``server_id`` and the remainder is the
``tool_name`` (which can itself contain dots, e.g.
``echo_server.fs.read``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc.cognitive_core import CognitiveCore
    from acc.config import RoleDefinitionConfig

logger = logging.getLogger("acc.capability_dispatch")


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# Captures: (skill_id, optional " <args>").  The args portion is whatever
# follows the id up to the closing ']' on the same line.  We deliberately
# match a single line so an LLM that emits multiple markers in a row
# (one per line) gets each one parsed independently.
_SKILL_RE = re.compile(
    r"\[SKILL:\s*([a-z][a-z0-9_]*)\s*(\{[^\n]*\})?\s*\]",
)

# Captures: (server_id, tool_name, optional args).  server_id matches the
# same lowercase_snake convention as MCPManifest.server_id; tool_name
# allows dots so nested namespacing (`fs.read`) is preserved.
_MCP_RE = re.compile(
    r"\[MCP:\s*([a-z][a-z0-9_]*)\.([a-zA-Z0-9_.\-]+)\s*(\{[^\n]*\})?\s*\]",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParsedInvocation:
    """One marker extracted from LLM output, prior to dispatch.

    ``args`` is ``{}`` when the marker omits a JSON payload.
    ``args_error`` is set when the marker carried JSON that failed to
    parse — in which case the agent should skip dispatch and surface
    the error in audit logs.
    """

    kind: str               # "skill" or "mcp"
    target: str             # skill_id  OR  "server_id.tool_name"
    args: dict[str, Any] = field(default_factory=dict)
    args_error: str = ""    # empty string when args parsed cleanly
    raw: str = ""           # original marker text (for audit / debugging)


@dataclass
class InvocationOutcome:
    """Result of one dispatched marker.

    ``ok=True`` means the adapter returned a dict.  ``error`` carries
    a human-readable summary when ``ok=False`` — Cat-A blocks,
    JSON-decode failures, registry misses, and adapter exceptions all
    funnel through this single field so callers have one shape to
    render in TUI / audit logs.
    """

    parsed: ParsedInvocation
    ok: bool = False
    result: dict[str, Any] | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_invocations(text: str) -> list[ParsedInvocation]:
    """Extract every ``[SKILL:...]`` and ``[MCP:...]`` marker from *text*.

    Order is preserved: skills and MCPs interleave by their position
    in the source string, which matters when an LLM expects a
    sequence (e.g. "first echo, then call fs.read").  Empty input
    returns ``[]``.
    """
    if not text:
        return []

    # We collect (start_index, ParsedInvocation) tuples then sort by
    # start_index so the returned list mirrors source order.
    found: list[tuple[int, ParsedInvocation]] = []

    for match in _SKILL_RE.finditer(text):
        skill_id = match.group(1)
        args_text = (match.group(2) or "").strip()
        args, err = _parse_args(args_text)
        found.append((
            match.start(),
            ParsedInvocation(
                kind="skill",
                target=skill_id,
                args=args,
                args_error=err,
                raw=match.group(0),
            ),
        ))

    for match in _MCP_RE.finditer(text):
        server_id = match.group(1)
        tool_name = match.group(2)
        args_text = (match.group(3) or "").strip()
        args, err = _parse_args(args_text)
        found.append((
            match.start(),
            ParsedInvocation(
                kind="mcp",
                target=f"{server_id}.{tool_name}",
                args=args,
                args_error=err,
                raw=match.group(0),
            ),
        ))

    found.sort(key=lambda pair: pair[0])
    return [pi for _, pi in found]


def _parse_args(text: str) -> tuple[dict[str, Any], str]:
    """Decode a JSON object literal; return ``({}, '')`` for empty input.

    Returns ``({}, error_message)`` when *text* is non-empty but does
    not decode to a JSON object — caller is expected to skip dispatch
    and surface the error.
    """
    if not text:
        return {}, ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"json_decode: {exc.msg} at col {exc.colno}"
    if not isinstance(parsed, dict):
        return {}, f"json_not_object: got {type(parsed).__name__}"
    return parsed, ""


# ---------------------------------------------------------------------------
# Dispatching
# ---------------------------------------------------------------------------


async def dispatch_invocations(
    invocations: list[ParsedInvocation],
    core: "CognitiveCore",
    role: "RoleDefinitionConfig",
    *,
    oversight_queue: "Any | None" = None,
    task_id: str = "",
) -> list[InvocationOutcome]:
    """Execute each parsed marker through the cognitive core.

    Every marker is dispatched even if an earlier one fails — the
    agent often emits independent calls (echo + file-read), and a
    single Cat-A block on one shouldn't suppress the rest.  Errors
    are captured per-outcome and logged at WARNING.

    Args:
        invocations: Output of :func:`parse_invocations`.
        core: The agent's :class:`acc.cognitive_core.CognitiveCore`.
            Must have been constructed with a non-None
            ``skill_registry`` / ``mcp_registry`` for the
            corresponding marker kind to dispatch.
        role: Active role definition; passed into A-017 / A-018.
        oversight_queue: Optional :class:`acc.oversight.HumanOversightQueue`.
            When provided, every invocation whose manifest
            ``risk_level == "CRITICAL"`` is **blocked** on the queue:
            the dispatcher submits an oversight item, waits for an
            APPROVE / REJECT / EXPIRED resolution via
            :meth:`HumanOversightQueue.wait_for_decision`, and only
            runs the underlying adapter on APPROVE.  When ``None``
            (the default), CRITICAL invocations proceed immediately
            with a WARNING log — matching Phase 4.4 behaviour.
        task_id: Optional task identifier propagated into the
            oversight item's ``task_id`` field so the TUI Compliance
            screen can correlate the gated invocation back to the
            originating TASK_ASSIGN.  Empty string is fine.

    Returns:
        One :class:`InvocationOutcome` per input marker, in the same
        order.  Empty input returns ``[]``.
    """
    outcomes: list[InvocationOutcome] = []
    for inv in invocations:
        outcomes.append(await _dispatch_one(
            inv, core, role,
            oversight_queue=oversight_queue,
            task_id=task_id,
        ))
    return outcomes


async def _dispatch_one(
    inv: ParsedInvocation,
    core: "CognitiveCore",
    role: "RoleDefinitionConfig",
    *,
    oversight_queue: "Any | None" = None,
    task_id: str = "",
) -> InvocationOutcome:
    """Run one marker; convert every exception path to an
    :class:`InvocationOutcome` with a populated ``error``.

    CRITICAL invocations are gated on *oversight_queue* when one is
    supplied; see :func:`dispatch_invocations` for the contract.
    """
    if inv.args_error:
        logger.warning(
            "capability_dispatch: malformed args in %s — %s",
            inv.raw, inv.args_error,
        )
        return InvocationOutcome(parsed=inv, ok=False, error=inv.args_error)

    # Resolve the manifest BEFORE dispatching so we can read risk_level
    # for the oversight gate.  The registry round-trip is microseconds
    # and mirrors what invoke_skill / invoke_mcp_tool would do anyway.
    manifest = _resolve_manifest(inv, core)
    if (
        manifest is not None
        and oversight_queue is not None
        and getattr(manifest, "risk_level", "LOW") == "CRITICAL"
    ):
        gate_outcome = await _gate_on_oversight(
            inv, manifest, role, oversight_queue, task_id,
        )
        if gate_outcome is not None:
            # Either rejected or timed out — surface and skip dispatch.
            return gate_outcome
        # APPROVED — fall through to normal dispatch below.

    try:
        if inv.kind == "skill":
            result = await core.invoke_skill(inv.target, inv.args, role)
            return InvocationOutcome(parsed=inv, ok=True, result=result)
        if inv.kind == "mcp":
            server_id, _, tool_name = inv.target.partition(".")
            result = await core.invoke_mcp_tool(server_id, tool_name, inv.args, role)
            return InvocationOutcome(parsed=inv, ok=True, result=result)
        return InvocationOutcome(
            parsed=inv,
            ok=False,
            error=f"unknown_kind: {inv.kind!r}",
        )
    except Exception as exc:
        # Catch broadly here — the registry / guard error hierarchies
        # are all surfaced via .error so the caller has one place to
        # check.  Re-raising would crash the agent's task loop on a
        # single bad LLM marker, which is a worse failure mode than
        # logging and continuing.
        err = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "capability_dispatch: %s dispatch failed for %s: %s",
            inv.kind, inv.target, err,
        )
        return InvocationOutcome(parsed=inv, ok=False, error=err)


# ---------------------------------------------------------------------------
# Oversight gate (blocks CRITICAL invocations on a human approver)
# ---------------------------------------------------------------------------


def _resolve_manifest(
    inv: ParsedInvocation,
    core: "CognitiveCore",
) -> "Any | None":
    """Look up the manifest for *inv* without dispatching the call.

    Used by the oversight gate to read ``risk_level`` ahead of time.
    Returns ``None`` for any failure path (missing registry, unknown
    target, kind we don't recognise) — the caller falls through to
    normal dispatch which will surface the real error.
    """
    try:
        if inv.kind == "skill":
            reg = getattr(core, "_skill_registry", None)
            if reg is None:
                return None
            return reg.manifest(inv.target)
        if inv.kind == "mcp":
            reg = getattr(core, "_mcp_registry", None)
            if reg is None:
                return None
            server_id, _, _tool = inv.target.partition(".")
            return reg.manifest(server_id)
    except Exception:
        return None
    return None


async def _gate_on_oversight(
    inv: ParsedInvocation,
    manifest: "Any",
    role: "RoleDefinitionConfig",
    queue: "Any",
    task_id: str,
) -> "InvocationOutcome | None":
    """Submit an oversight item and block on its resolution.

    Returns:
        ``None`` when the operator APPROVED — caller proceeds with
        normal dispatch.

        :class:`InvocationOutcome` with ``ok=False`` and a populated
        ``error`` field for REJECT / EXPIRED / queue failure — caller
        surfaces this directly without dispatching the adapter.
    """
    summary = _build_oversight_summary(inv, manifest)
    role_label = getattr(role, "domain_id", "") or "agent"

    try:
        oversight_id = await queue.submit(
            task_id=task_id,
            risk_level="CRITICAL",
            summary=summary,
            role_id=role_label,
        )
    except Exception as exc:
        err = f"oversight_submit_failed: {type(exc).__name__}: {exc}"
        logger.warning("capability_dispatch: %s — %s", inv.raw, err)
        return InvocationOutcome(parsed=inv, ok=False, error=err)

    logger.warning(
        "capability_dispatch: blocking on oversight oversight_id=%s "
        "kind=%s target=%s — awaiting human decision",
        oversight_id, inv.kind, inv.target,
    )

    try:
        item = await queue.wait_for_decision(oversight_id)
    except Exception as exc:
        err = f"oversight_wait_failed: {type(exc).__name__}: {exc}"
        logger.warning("capability_dispatch: %s — %s", inv.raw, err)
        return InvocationOutcome(parsed=inv, ok=False, error=err)

    if item is None:
        # Item evaporated from storage before resolving (Redis TTL,
        # in-process restart).  Treat as a hard fail — we cannot prove
        # the operator approved, so we must not run the adapter.
        return InvocationOutcome(
            parsed=inv,
            ok=False,
            error=f"oversight_lost: id={oversight_id} disappeared before resolution",
        )

    status = getattr(item, "status", "PENDING")
    if status == "APPROVED":
        logger.info(
            "capability_dispatch: oversight APPROVED oversight_id=%s — proceeding",
            oversight_id,
        )
        return None
    if status == "REJECTED":
        reason = getattr(item, "rejection_reason", "")
        return InvocationOutcome(
            parsed=inv,
            ok=False,
            error=f"oversight_rejected: id={oversight_id} reason={reason!r}",
        )
    if status == "EXPIRED":
        return InvocationOutcome(
            parsed=inv,
            ok=False,
            error=f"oversight_expired: id={oversight_id} timed out before approval",
        )
    # Still PENDING after wait_for_decision returned — the wait hit
    # the timeout cap.  Treat as expired from the dispatcher's POV.
    return InvocationOutcome(
        parsed=inv,
        ok=False,
        error=f"oversight_timeout: id={oversight_id} still PENDING after wait",
    )


def _build_oversight_summary(
    inv: ParsedInvocation,
    manifest: "Any",
) -> str:
    """One-line description shown to the human approver in the TUI.

    Format::

        CRITICAL <kind> <target>: <manifest.purpose>
            args=<json args, truncated to 200 chars>
    """
    purpose = getattr(manifest, "purpose", "")
    args_repr = json.dumps(inv.args, separators=(",", ":"), default=str)
    if len(args_repr) > 200:
        args_repr = args_repr[:197] + "..."
    return (
        f"CRITICAL {inv.kind} {inv.target}: {purpose}\n"
        f"    args={args_repr}"
    )
