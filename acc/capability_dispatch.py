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
yield a :class:`InvocationOutcome` whose ``error`` is an operator-
readable "skipped a malformed tool call …" note (it carries the decode
detail for debugging); the marker is NOT dispatched.

Tool-name grammar for MCP markers: dot-separated identifier where the
first segment is the ``server_id`` and the remainder is the
``tool_name`` (which can itself contain dots, e.g.
``echo_server.fs.read``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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

# Marker HEADS only.  The argument object is decoded separately, by a real
# JSON scanner, because an inline args group has to be greedy to reach the
# closing brace and therefore ran to the LAST brace on the line: two adjacent
# markers collapsed into one unparseable match and every marker after the
# first was lost (`20260913-the-marker-channel`, MC-04).  Measured on
# lighthouse: a reply with five markers dispatched one.
_SKILL_HEAD_RE = re.compile(r"\[SKILL:\s*([a-z][a-z0-9_]*)\s*")

# Captures: (server_id, tool_name).  server_id matches the same lowercase_snake
# convention as MCPManifest.server_id; tool_name allows dots so nested
# namespacing (`fs.read`) is preserved.
_MCP_HEAD_RE = re.compile(
    r"\[MCP:\s*([a-z][a-z0-9_]*)\.([a-zA-Z0-9_.\-]+)\s*"
)

_DECODER = json.JSONDecoder()


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
    # `20260911-question-envelope` -- the call went through a destructive or
    # CRITICAL gate and was let through: a "critical function", marked in the
    # transcript and the session trace so they can be tracked.
    critical: bool = False
    # The question the gate asked and how it ended (oversight_id, text,
    # evidence, status, answer, approver_id).  Empty when nothing was asked.
    question: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _read_marker_tail(text: str, pos: int) -> tuple[dict, str, int]:
    """Read one marker's arguments and its closing bracket, starting at *pos*.

    Returns ``(args, args_error, end_index)``; ``end_index`` is -1 when this is
    not a marker after all (no closing bracket), so the caller can skip it.

    The arguments are decoded with a real JSON scanner rather than matched by
    a regex, which is the whole point of MC-04: a pattern that reaches the
    closing brace is greedy to the LAST brace on the line, so
    ``[MCP: a.b {}][MCP: a.c {}]`` became one match with unparseable arguments
    and the second call was never dispatched.  The decoder knows where a value
    ends -- nesting, strings and escapes included.
    """
    length = len(text)
    idx = pos
    while idx < length and text[idx] in " 	":
        idx += 1
    if idx < length and text[idx] == "]":
        return {}, "", idx + 1
    if idx >= length or text[idx] != "{":
        # Not arguments and not a close: let the caller drop this head.
        closing = text.find("]", idx)
        return {}, "", -1 if closing == -1 else -1
    try:
        value, offset = _DECODER.raw_decode(text, idx)
    except ValueError as exc:
        # Keep the audit line the operator already knows, and resynchronise on
        # the next bracket so one bad marker cannot swallow the ones after it.
        closing = text.find("]", idx)
        return (
            {},
            f"skipped a malformed tool call -- its arguments weren't valid "
            f"JSON ({exc}); expected e.g. [SKILL:name {{\"arg\": \"value\"}}]",
            -1 if closing == -1 else closing + 1,
        )
    if not isinstance(value, dict):
        closing = text.find("]", idx)
        return (
            {},
            "skipped a malformed tool call -- its arguments must be a JSON "
            "object; expected e.g. [SKILL:name {{\"arg\": \"value\"}}]",
            -1 if closing == -1 else closing + 1,
        )
    while offset < length and text[offset] in " 	":
        offset += 1
    if offset >= length or text[offset] != "]":
        return {}, "", -1
    return value, "", offset + 1


def parse_invocations(text: str) -> list[ParsedInvocation]:
    """Extract every ``[SKILL:...]`` and ``[MCP:...]`` marker from *text*.

    Order is preserved: skills and MCPs interleave by their position
    in the source string, which matters when an LLM expects a
    sequence (e.g. "first echo, then call fs.read").  Empty input
    returns ``[]``.

    Markers may sit next to one another on one line -- a model batching calls
    is ordinary behaviour, and until MC-04 every marker after the first was
    silently dropped.
    """
    if not text:
        return []

    found: list[tuple[int, ParsedInvocation]] = []

    for match in _SKILL_HEAD_RE.finditer(text):
        args, err, end = _read_marker_tail(text, match.end())
        if end == -1 and not err:
            continue
        found.append((
            match.start(),
            ParsedInvocation(
                kind="skill",
                target=match.group(1),
                args=args,
                args_error=err,
                raw=text[match.start():end] if end > 0 else match.group(0),
            ),
        ))

    for match in _MCP_HEAD_RE.finditer(text):
        args, err, end = _read_marker_tail(text, match.end())
        if end == -1 and not err:
            continue
        found.append((
            match.start(),
            ParsedInvocation(
                kind="mcp",
                target=f"{match.group(1)}.{match.group(2)}",
                args=args,
                args_error=err,
                raw=text[match.start():end] if end > 0 else match.group(0),
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
        # Operator-facing: this string renders verbatim in the prompt
        # pane (via the TASK_COMPLETE invocation summary).  Say what
        # happened + how a marker should look, and keep the decode
        # detail for debugging.  (033 WS-A2 — no raw "json_decode" dump.)
        return {}, (
            "skipped a malformed tool call — its arguments weren't valid "
            f'JSON ({exc.msg} at col {exc.colno}); expected e.g. [SKILL:name {{"arg": "value"}}]'
        )
    if not isinstance(parsed, dict):
        return {}, (
            "skipped a malformed tool call — arguments must be a JSON "
            f'object like {{"arg": "value"}}, got {type(parsed).__name__}'
        )
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
    progress_callback: "Any | None" = None,
    operating_mode: str = "AUTO",
    requester_ceiling: str = "",
    requester: str = "",
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
        progress_callback: Optional sync callable that fires once per
            invocation BEFORE dispatch.  Receives a
            :class:`acc.progress.ProgressContext` whose
            ``current_step`` is the 1-based index in *invocations*,
            ``total_steps_estimated`` is the full list length, and
            ``step_label`` reads ``"Calling skill:echo"`` /
            ``"Calling mcp:fs.read"``.  The agent's task loop wraps
            this to publish TASK_PROGRESS — operators see one
            progress line per dispatched tool in the prompt-pane
            transcript, in addition to the trace lines emitted from
            the outcomes.  ``None`` (default) disables emission.
        requester_ceiling: The category ceiling stamped on the task by the
            surface that admitted its requester (``requester_ceiling``,
            D-014). An invocation whose manifest ``risk_level`` is above it
            is refused before any escalation or gate — the ceiling is a
            floor under human judgement, not a question for it. ``""``
            (an unattributed task) applies no ceiling.

    Returns:
        One :class:`InvocationOutcome` per input marker, in the same
        order.  Empty input returns ``[]``.
    """
    # PR-L (D-003) — PLAN mode short-circuits the entire dispatch.
    # Each invocation is turned into a "would-have-fired" outcome
    # (ok=True, error="PLAN_MODE: not executed").  The agent's reply
    # downstream sees these as if they had run, which lets the LLM
    # describe its plan honestly.  No skill / MCP code runs; no
    # oversight items are submitted.  Cat-A guardrails do not apply
    # here because nothing executes — the constitutional invariant is
    # preserved trivially.
    from acc.operating_modes import (  # noqa: PLC0415
        MODE_PLAN, normalise, should_gate_invocation,
    )
    mode = normalise(operating_mode)
    if mode == MODE_PLAN:
        return [
            InvocationOutcome(
                parsed=inv, ok=True,
                error="PLAN_MODE: not executed",
                result={"plan_mode": True, "would_have_called": inv.target},
            )
            for inv in invocations
        ]

    outcomes: list[InvocationOutcome] = []
    total = len(invocations)
    for idx, inv in enumerate(invocations, start=1):
        if progress_callback is not None:
            try:
                from acc.progress import ProgressContext  # noqa: PLC0415
                progress_callback(ProgressContext(
                    current_step=idx,
                    total_steps_estimated=total,
                    step_label=f"Calling {inv.kind}:{inv.target}",
                    elapsed_ms=0,
                    estimated_remaining_ms=0,
                    deadline_ms=0,
                    confidence=0.5,
                    confidence_trend="STABLE",
                    llm_calls_so_far=0,
                    tokens_in_so_far=0,
                    tokens_out_so_far=0,
                    token_budget_remaining=0,
                    over_budget=False,
                    over_token_budget=False,
                ))
            except Exception:
                logger.exception(
                    "capability_dispatch: progress callback raised "
                    "for %s — continuing", inv.target,
                )
        # F3 -- whose task this call serves, for a credential minted per person
        # (an ``auth: oauth`` MCP). Scoped to the call: agents run tasks
        # concurrently.
        from acc.credentials.live import serving  # noqa: PLC0415

        with serving(requester):
            outcomes.append(await _dispatch_one(
                inv, core, role,
                oversight_queue=oversight_queue,
                task_id=task_id,
                operating_mode=mode,
                requester_ceiling=requester_ceiling,
                requester=requester,
            ))
    return outcomes


async def _dispatch_one(
    inv: ParsedInvocation,
    core: "CognitiveCore",
    role: "RoleDefinitionConfig",
    *,
    oversight_queue: "Any | None" = None,
    task_id: str = "",
    operating_mode: str = "AUTO",
    requester_ceiling: str = "",
    requester: str = "",
) -> InvocationOutcome:
    """Run one marker; convert every exception path to an
    :class:`InvocationOutcome` with a populated ``error``.

    PR-L (D-003) — the gate decision is now mode-aware via
    :func:`acc.operating_modes.should_gate_invocation`:

    * AUTO — only CRITICAL invocations are gated (Phase 4.5
      behaviour, preserved).
    * ACCEPT_EDITS — CRITICAL invocations AND write actions
      (classified by :func:`is_write_action`) are gated.
    * ASK_PERMISSIONS — every invocation is gated.
    * PLAN — never reaches here; ``dispatch_invocations``
      short-circuits.

    Cat-A constitutional rules still fire inside ``invoke_skill`` /
    ``invoke_mcp_tool`` regardless of mode — modes only adjust what
    HUMAN review applies to, not what the constitutional engine
    decides.

    `20260911-question-envelope` — a call that deletes or overwrites data
    (:func:`acc.operating_modes.destructive_evidence`) is asked as a question
    in every mode that reaches here, and refused when there is no queue to ask.
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
    from acc.operating_modes import (  # noqa: PLC0415
        destructive_evidence,
        gate_categories,
        should_gate_invocation,
    )
    risk_level = (
        getattr(manifest, "risk_level", "LOW") if manifest is not None else "LOW"
    )

    # D-014 -- the requester's category ceiling.  Checked FIRST: above it the
    # call is refused outright, never escalated (1.2b would ask the operator
    # to widen the role for a call the admission already forbade) and never
    # gated (an approval could not make it allowed).  ``effective = role
    # grants ∩ principal ceiling`` -- the role side is the guard below.
    from acc.identity import exceeds_ceiling  # noqa: PLC0415
    if exceeds_ceiling(str(risk_level), requester_ceiling):
        reason = (
            f"refused: {inv.kind} {inv.target!r} is {str(risk_level).upper()} "
            f"-- above the requester's ceiling {requester_ceiling.upper()}"
        )
        logger.warning("capability_dispatch: %s (task %s)", reason, task_id)
        return InvocationOutcome(parsed=inv, ok=False, error=reason)

    # A destructive call is a question for the operator, whatever the mode
    # would otherwise do with it -- and with nobody to ask, it does not run.
    # A call the capability's own sandbox refuses (denied_tools) is never asked
    # about: no answer could make it run, and the guard below refuses it.
    question = None
    record: dict[str, Any] = {}
    evidence = destructive_evidence(inv.kind, inv.target, inv.args, manifest)
    if evidence and _sandboxed(inv, manifest, role, core):
        evidence = ""
    if evidence:
        if oversight_queue is None:
            reason = (
                f"refused: {inv.kind} {inv.target!r} deletes or overwrites data "
                f"({evidence}) and there is no oversight queue to ask"
            )
            logger.warning("capability_dispatch: %s (task %s)", reason, task_id)
            return InvocationOutcome(parsed=inv, ok=False, error=reason)
        from acc.question import destructive_confirm  # noqa: PLC0415
        question = destructive_confirm(inv.kind, inv.target, evidence)
    critical_fn = question is not None or str(risk_level).upper() == "CRITICAL"

    # 1.2b -- escalation.  An invocation the role-side A-017 / A-018 guard
    # would refuse (not in allowed_skills / allowed_mcps, a missing
    # requires_action, or above the role's risk ceiling) becomes a question
    # instead of a bare refusal: "allow for this task?".  On APPROVE the role
    # is widened for THIS ONE CALL (D-011, operator 2026-09-02); refusal stays
    # the default -- no queue, REJECT, EXPIRED and headless all still refuse.
    # A manifest's own sandbox (denied_tools) is never escalated.
    escalated = False
    if oversight_queue is not None:
        denial = _guard_denial(inv, manifest, role, core)
        if denial:
            gate_outcome = await _gate_on_oversight(
                inv, manifest, role, oversight_queue, task_id,
                escalation=denial, question=question, record=record, core=core,
                requester=requester, requester_ceiling=requester_ceiling,
            )
            if gate_outcome is not None:
                gate_outcome.question = record
                return gate_outcome
            role = _role_with_grant(role, inv, manifest)
            escalated = True

    # 1.2 -- system access / acting on the operator's behalf are asked in
    # AUTO and ACCEPT_EDITS regardless of risk_level.  An approved escalation
    # already covered this exact call (and asked its question); don't ask twice.
    categories = gate_categories(inv.kind, inv.target, manifest)
    needs_gate = (
        not escalated
        and oversight_queue is not None
        and (question is not None or should_gate_invocation(
            operating_mode, kind=inv.kind, target=inv.target,
            risk_level=str(risk_level), categories=categories,
        ))
    )
    if needs_gate:
        # In AUTO mode we still need a manifest to gate (CRITICAL is
        # the only trigger); in ACCEPT_EDITS / ASK_PERMISSIONS we
        # synthesise a minimal stand-in so the oversight item carries
        # the same shape downstream.
        gate_manifest = manifest if manifest is not None else _SyntheticManifest(
            risk_level=str(risk_level),
            target=inv.target,
        )
        gate_outcome = await _gate_on_oversight(
            inv, gate_manifest, role, oversight_queue, task_id,
            categories=categories, question=question, record=record, core=core,
            requester=requester, requester_ceiling=requester_ceiling,
        )
        if gate_outcome is not None:
            # Either rejected or timed out — surface and skip dispatch.
            gate_outcome.question = record
            return gate_outcome
        # APPROVED — fall through to normal dispatch below.

    # A call a destructive or CRITICAL gate let through is a critical function.
    critical = critical_fn and (escalated or needs_gate)
    try:
        if inv.kind == "skill":
            result = await core.invoke_skill(inv.target, inv.args, role)
            return InvocationOutcome(
                parsed=inv, ok=True, result=result, critical=critical, question=record,
            )
        if inv.kind == "mcp":
            server_id, _, tool_name = inv.target.partition(".")
            result = await core.invoke_mcp_tool(server_id, tool_name, inv.args, role)
            return InvocationOutcome(
                parsed=inv, ok=True, result=result, critical=critical, question=record,
            )
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
        return InvocationOutcome(
            parsed=inv, ok=False, error=err, critical=critical, question=record,
        )


# ---------------------------------------------------------------------------
# Oversight gate (blocks CRITICAL invocations on a human approver)
# ---------------------------------------------------------------------------


def _oversight_headless() -> bool:
    """Unattended-oversight switch (``ACC_OVERSIGHT_HEADLESS``).

    When on, a CRITICAL invocation that would otherwise **block awaiting a
    human decision** is auto-REJECTED immediately (the request is still
    submitted so the audit trail records it).  This is the safe default for a
    headless / e2e / edge agent with no reviewer: without it a single gated
    tool marker wedges the agent's *serial* task loop for the full
    ``oversight_timeout_s`` (300s) — and, with several markers in one reply,
    for a multiple of it — before timing out to the same reject.  Off (default)
    preserves the interactive block-and-wait so a human CAN approve.

    Denying an unattended CRITICAL action is strictly safer than executing it,
    and the timed-out path already ends in a reject — so this only removes the
    dead wait, it does not change the security outcome.
    """
    return os.environ.get("ACC_OVERSIGHT_HEADLESS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


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


# Reasons the role-side guard produces.  Anything else (the manifest's own
# allowed_tools / denied_tools sandbox) is the capability's sandbox, not the
# role's grant, and a human cannot widen it per task.
_ROLE_SIDE_DENIALS: tuple[str, ...] = (
    "not in role.",
    "missing from role.allowed_actions",
    "exceeds role ceiling",
)


def _guard_reason(
    inv: ParsedInvocation,
    manifest: "Any",
    role: "RoleDefinitionConfig",
    core: "CognitiveCore",
) -> str:
    """Why A-017 / A-018 would refuse *inv*, or ``""`` when it passes or there
    is no enforcing guard / manifest."""
    guard = getattr(core, "_capability_guard", None)
    if guard is None or manifest is None:
        return ""
    try:
        if inv.kind == "skill":
            decision = guard.check_skill_invocation(role, manifest)
        elif inv.kind == "mcp":
            _server, _, tool = inv.target.partition(".")
            decision = guard.check_mcp_invocation(role, manifest, tool)
        else:
            return ""
    except Exception:  # noqa: BLE001 -- the real call will surface it
        return ""
    if getattr(decision, "allowed", True):
        return ""
    return str(getattr(decision, "reason", "") or "") or "refused"


def _guard_denial(
    inv: ParsedInvocation,
    manifest: "Any",
    role: "RoleDefinitionConfig",
    core: "CognitiveCore",
) -> str:
    """The reason A-017 / A-018 would refuse *inv* on the role's side, or
    ``""`` when it passes, there is no enforcing guard / manifest, or the
    refusal is the manifest's own sandbox (never escalated)."""
    reason = _guard_reason(inv, manifest, role, core)
    return reason if any(m in reason for m in _ROLE_SIDE_DENIALS) else ""


def _sandboxed(
    inv: ParsedInvocation,
    manifest: "Any",
    role: "RoleDefinitionConfig",
    core: "CognitiveCore",
) -> bool:
    """The capability's own sandbox (``denied_tools`` / ``allowed_tools``)
    refuses *inv* -- a refusal no approval and no escalation can lift."""
    reason = _guard_reason(inv, manifest, role, core)
    return bool(reason) and not any(m in reason for m in _ROLE_SIDE_DENIALS)


def _role_with_grant(
    role: "RoleDefinitionConfig",
    inv: ParsedInvocation,
    manifest: "Any",
) -> "RoleDefinitionConfig":
    """Widen *role* for one approved call: the target, the actions the
    manifest requires, and the risk ceiling if the manifest is above it.
    A copy -- the role definition itself is untouched."""
    from acc.governance_capabilities import _risk_exceeds  # noqa: PLC0415

    risk = str(getattr(manifest, "risk_level", "LOW") or "LOW").upper()
    required = [
        a for a in (getattr(manifest, "requires_actions", None) or [])
        if a not in role.allowed_actions
    ]
    update: dict[str, Any] = {"allowed_actions": [*role.allowed_actions, *required]}
    if inv.kind == "skill":
        if inv.target not in role.allowed_skills:
            update["allowed_skills"] = [*role.allowed_skills, inv.target]
        if _risk_exceeds(risk, getattr(role, "max_skill_risk_level", "MEDIUM")):
            update["max_skill_risk_level"] = risk
    else:
        server_id = inv.target.partition(".")[0]
        if server_id not in role.allowed_mcps:
            update["allowed_mcps"] = [*role.allowed_mcps, server_id]
        if _risk_exceeds(risk, getattr(role, "max_mcp_risk_level", "MEDIUM")):
            update["max_mcp_risk_level"] = risk
    return role.model_copy(update=update)


async def _gate_on_oversight(
    inv: ParsedInvocation,
    manifest: "Any",
    role: "RoleDefinitionConfig",
    queue: "Any",
    task_id: str,
    *,
    categories: frozenset[str] = frozenset(),
    escalation: str = "",
    question: "Any | None" = None,
    record: "dict[str, Any] | None" = None,
    core: "Any | None" = None,
    requester: str = "",
    requester_ceiling: str = "",
) -> "InvocationOutcome | None":
    """Submit an oversight item and block on its resolution.

    *question* (an :class:`acc.question.Question`) rides on the row; how it
    ended is written into *record* (oversight_id, text, evidence, status,
    answer, approver_id) for the outcome and the session trace.

    Returns:
        ``None`` when the operator APPROVED — caller proceeds with
        normal dispatch.

        :class:`InvocationOutcome` with ``ok=False`` and a populated
        ``error`` field for REJECT / EXPIRED / queue failure — caller
        surfaces this directly without dispatching the adapter.
    """
    destructive = bool(getattr(question, "destructive", False))
    summary = _build_oversight_summary(
        inv, manifest, categories, escalation=escalation, destructive=destructive,
    )
    role_label = getattr(role, "domain_id", "") or "agent"
    # A category gate or an escalation carries the manifest's own risk (HIGH
    # for shell_exec, MEDIUM for telegram_send); every other gate keeps the
    # historical CRITICAL label.  A destructive call is at least HIGH, whatever
    # its manifest says -- deleting data is not a LOW-risk act.
    manifest_risk = str(getattr(manifest, "risk_level", "") or "").upper()
    risk_level = (
        manifest_risk if (categories or escalation) and manifest_risk else "CRITICAL"
    )
    if destructive:
        risk_level = manifest_risk if manifest_risk in ("HIGH", "CRITICAL") else "HIGH"

    record = record if record is not None else {}
    submit_kwargs: dict[str, Any] = {}
    if question is not None:
        submit_kwargs["question"] = question.to_dict()
        record.update(text=question.text, evidence=question.evidence)
    # UX-03 -- what this call actually does, so the panel can show it instead
    # of asking the operator to trust the summary.  Computed from the parsed
    # call and the manifest; nothing is executed to produce it.
    evidence_lines = list(call_evidence(inv.kind, inv.target, inv.args, manifest))
    # UX-03 Phase 2 -- when the capability DECLARES a dry run, run it once and
    # show what it would do.  Declared, never inferred; journalled as its own
    # act; a failure is shown as a failure and resolves nothing.
    if core is not None:
        evidence_lines.extend(
            await run_preview(core, inv, manifest, role, task_id=task_id)
        )
    if evidence_lines:
        submit_kwargs["evidence"] = evidence_lines
    # UX-09 -- whose work this is and under whose authority it runs.
    if requester:
        submit_kwargs["requester"] = requester
    if requester_ceiling:
        submit_kwargs["ceiling"] = requester_ceiling
    try:
        oversight_id = await queue.submit(
            task_id=task_id,
            risk_level=risk_level,
            summary=summary,
            role_id=role_label,
            **submit_kwargs,
        )
    except Exception as exc:
        err = f"oversight_submit_failed: {type(exc).__name__}: {exc}"
        logger.warning("capability_dispatch: %s — %s", inv.raw, err)
        return InvocationOutcome(parsed=inv, ok=False, error=err)
    if question is not None:
        record["oversight_id"] = oversight_id

    # Headless / unattended: no reviewer will ever resolve this, so blocking
    # up to oversight_timeout_s only wedges the agent's serial task loop before
    # the wait times out to the same reject.  Auto-reject now (the submit above
    # already recorded the request for the audit trail).
    if _oversight_headless():
        reason = "auto-rejected: unattended CRITICAL action (ACC_OVERSIGHT_HEADLESS)"
        try:
            await queue.reject(oversight_id, "headless-auto", reason)
        except Exception:  # noqa: BLE001 — audit-record best-effort; still deny
            logger.debug(
                "capability_dispatch: headless auto-reject record failed",
                exc_info=True,
            )
        logger.warning(
            "capability_dispatch: oversight AUTO-REJECTED (headless) "
            "oversight_id=%s kind=%s target=%s",
            oversight_id, inv.kind, inv.target,
        )
        if question is not None:
            record.update(status="REJECTED", approver_id="headless-auto")
        return InvocationOutcome(
            parsed=inv,
            ok=False,
            error=f"oversight_auto_rejected_headless: id={oversight_id}",
        )

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
    if status == "PENDING":
        # The wait hit its cap and the call is refused, so the row must not
        # stay approvable: expire it.  If a decision landed between the last
        # poll and now, that decision stands and is honoured instead.
        expire = getattr(queue, "expire", None)
        if expire is not None:
            try:
                if await expire(oversight_id):
                    status = "EXPIRED"
                else:
                    item = await queue.wait_for_decision(oversight_id, timeout_s=0) or item
                    status = getattr(item, "status", "PENDING")
            except Exception:  # noqa: BLE001 -- the call is refused either way
                logger.debug("capability_dispatch: expiring %s failed", oversight_id, exc_info=True)
    if question is not None:
        record.update(
            status=status,
            answer=str(getattr(item, "answer", "") or ""),
            approver_id=str(getattr(item, "approver_id", "") or ""),
        )
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
    # Still PENDING and it could not be expired (a queue without expire(), or
    # the store failed) — refused all the same.
    return InvocationOutcome(
        parsed=inv,
        ok=False,
        error=f"oversight_timeout: id={oversight_id} still PENDING after wait",
    )


def _build_oversight_summary(
    inv: ParsedInvocation,
    manifest: "Any",
    categories: frozenset[str] = frozenset(),
    *,
    escalation: str = "",
    destructive: bool = False,
) -> str:
    """One-line description shown to the human approver in the TUI.

    Format::

        CRITICAL <kind> <target>: <manifest.purpose>
            args=<json args, truncated to 200 chars>

    A category gate (1.2) leads with the category instead, so the operator
    reads WHY they are asked::

        SYSTEM-ACCESS skill shell_exec: Run a process ...

    An escalation (1.2b) leads with ESCALATION and names the missing grant::

        ESCALATION skill shell_exec: Run a process ...
            not granted: skill 'shell_exec' not in role.allowed_skills (...)

    A destructive call leads with DESTRUCTIVE, whatever else applies — the
    row's question says what will be destroyed.
    """
    purpose = getattr(manifest, "purpose", "")
    args_repr = json.dumps(inv.args, separators=(",", ":"), default=str)
    if len(args_repr) > 200:
        args_repr = args_repr[:197] + "..."
    if destructive:
        tag = "DESTRUCTIVE"
    elif escalation:
        tag = "ESCALATION"
    elif categories:
        tag = "+".join(c.upper().replace("_", "-") for c in sorted(categories))
    else:
        tag = "CRITICAL"
    lines = [f"{tag} {inv.kind} {inv.target}: {purpose}"]
    if escalation:
        lines.append(f"    not granted: {escalation}")
    lines.append(f"    args={args_repr}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PR-L — synthetic manifest stand-in
# ---------------------------------------------------------------------------


class _SyntheticManifest:
    """Minimal stand-in when a real manifest isn't available but a
    PR-L operating-mode gate still needs to fire.

    Carries just enough attributes for ``_gate_on_oversight`` /
    ``_summarise_invocation_for_oversight`` to render a sensible
    pending-item card: ``risk_level``, ``purpose``, ``target``.
    Used by ACCEPT_EDITS / ASK_PERMISSIONS modes where the gate
    decision doesn't depend on the manifest at all — operating mode
    is the trigger.
    """

    def __init__(self, *, risk_level: str, target: str) -> None:
        self.risk_level = risk_level
        self.target = target
        self.purpose = (
            f"(synthesised — gated by operating mode, "
            f"target={target!r})"
        )
# ---------------------------------------------------------------------------
# The tool-result turn (`20260912-the-tool-result-turn`, MC-03)
# ---------------------------------------------------------------------------

NEWLINE: str = chr(10)

MAX_RESULT_CHARS: int = 2000
"""How much of one tool's result the model is shown."""

MAX_RESULTS_BLOCK_CHARS: int = 6000
"""Ceiling on the whole block, so a chatty server cannot evict the conversation."""

_RESULTS_HEADER = (
    "Tool results -- you called these, and the requester has not seen them:"
)
_RESULTS_FOOTER = (
    "Answer the request using these results. Do not emit any [SKILL: ...] or "
    "[MCP: ...] markers in this reply -- they will not run."
)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " ... (truncated)"


def render_tool_results(
    outcomes: list[InvocationOutcome],
    *,
    max_result_chars: int = MAX_RESULT_CHARS,
    max_block_chars: int = MAX_RESULTS_BLOCK_CHARS,
) -> str:
    """Render *outcomes* as the content of a follow-up turn.

    ACC dispatches a marker after the reply is already final, so without this
    the model answers a lookup by inventing the answer while the tool it called
    sits on the TASK_COMPLETE payload (measured against midojo: `get_weather`
    succeeded 8/8 and every reply was invented).  This renders what came back
    so a second turn can use it.

    A failure is rendered too, as ``error: ...``.  "I could not look that up"
    is a better answer than a confident invention, and a refused call is the
    one the model most needs to know about.

    Returns ``""`` when there is nothing to say, which the caller treats as
    "no follow-up turn".
    """
    if not outcomes:
        return ""
    lines: list[str] = [_RESULTS_HEADER, ""]
    budget = max_block_chars
    rendered_any = False
    for outcome in outcomes:
        parsed = outcome.parsed
        try:
            args = json.dumps(parsed.args or {}, default=str)
        except Exception:  # noqa: BLE001 -- a renderer must never raise
            args = "{}"
        if outcome.ok:
            try:
                body = json.dumps(outcome.result, default=str)
            except Exception:  # noqa: BLE001
                body = str(outcome.result)
        else:
            body = f"error: {outcome.error}"
        entry = (f"  {parsed.target} {args}" + NEWLINE
                 + f"    {_clip(body, max_result_chars)}")
        if len(entry) > budget:
            lines.append("  ... (further results omitted -- block budget)")
            break
        budget -= len(entry)
        lines.append(entry)
        rendered_any = True
    if not rendered_any:
        return ""
    lines.extend(["", _RESULTS_FOOTER])
    return NEWLINE.join(lines)


FOLLOW_UP_FLAG: str = "acc_tool_result_turn"
"""Marks a payload as already being the follow-up, so it can never start one."""


def should_take_tool_result_turn(
    role: Any,
    outcomes: list[InvocationOutcome],
    task_payload: dict,
) -> bool:
    """Whether this task gets one more turn with its tool results.

    Three conditions, each of which is the answer to a real question:
    the role opted in (it costs an extra LLM call), something actually came
    back to show the model, and this is not already the follow-up -- the last
    is what bounds the feature at exactly one extra turn.
    """
    if not getattr(role, "tool_result_turn", False):
        return False
    if not outcomes:
        return False
    return not task_payload.get(FOLLOW_UP_FLAG)


def follow_up_payload(task_payload: dict, results_block: str) -> dict:
    """The task again, with the tool results appended to its content.

    A copy: the original payload is what the tracelog and TASK_COMPLETE still
    describe.  The content is where the results go so they pass the same
    ``pre_llm`` guardrails as any other input -- tool output is untrusted
    input, and a follow-up that smuggled it in as system text would skip the
    injection check the marker design has been getting for free.
    """
    follow_up = dict(task_payload)
    follow_up[FOLLOW_UP_FLAG] = True
    follow_up["content"] = (
        str(task_payload.get("content", "") or "") + NEWLINE + NEWLINE + results_block
    )
    return follow_up
# ---------------------------------------------------------------------------
# Evidence for the decision panel (`20260913-evidence-in-the-panel`, UX-03)
# ---------------------------------------------------------------------------

MAX_EVIDENCE_VALUE: int = 160
"""How much of one argument value the operator is shown."""

MAX_EVIDENCE_LINE: int = 300
"""Cap per rendered line, so one long argument cannot push the options away."""

_SECRET_KEY_MARKERS: tuple = (
    "key", "token", "secret", "password", "passwd", "credential", "auth",
    "cookie", "session", "bearer", "private",
)


def _mask_secrets(args: object) -> dict:
    """Copy *args* with secret-shaped values replaced by ``***``.

    The panel is a screen an operator may be sharing, and a gated call is
    exactly where a credential shows up as an argument.  Matching is on the
    key, not the value: a value that merely looks random is usually an id, and
    hiding ids would make the evidence useless.
    """
    if not isinstance(args, dict):
        return {}
    out = {}
    for key, value in args.items():
        name = str(key).lower()
        if any(marker in name for marker in _SECRET_KEY_MARKERS):
            out[key] = "***"
            continue
        text = value if isinstance(value, (int, float, bool)) or value is None else str(value)
        if isinstance(text, str) and len(text) > MAX_EVIDENCE_VALUE:
            text = text[:MAX_EVIDENCE_VALUE] + " ..."
        out[key] = text
    return out


def call_evidence(
    kind: str, target: str, args: object = None, manifest: object = None,
) -> tuple:
    """What this call actually does, as lines for the decision panel.

    The panel said a call was gated and why the category applied; it did not
    say what would run, so deciding meant trusting the summary rather than
    checking the call.  ACC already had the material -- the dispatcher computes
    the destructive evidence and then nothing rendered it.

    Nothing here executes: these are the parsed call and the manifest, not a
    dry run.  Whether a preview may run part of the work before approval is an
    open operator question (UX-00 5.3), and Phase 1 deliberately does not
    prejudge it.
    """
    from acc.operating_modes import (  # noqa: PLC0415
        destructive_evidence,
    )

    lines: list = []
    name = str(target or "")
    shown = _mask_secrets(args)
    try:
        rendered = json.dumps(shown, default=str, sort_keys=True) if shown else ""
    except Exception:  # noqa: BLE001 -- evidence must never break a dispatch
        rendered = ""
    call = f"{name} {rendered}".strip() if rendered else name
    if call:
        lines.append(f"runs: {_clip(call, MAX_EVIDENCE_LINE)}")

    destructive = destructive_evidence(kind, target, args, manifest)
    if destructive:
        lines.append(f"deletes or overwrites: {_clip(destructive, MAX_EVIDENCE_LINE)}")

    if kind == "mcp" and manifest is not None:
        url = str(getattr(manifest, "url", "") or "")
        transport = str(getattr(manifest, "transport", "") or "")
        if url:
            lines.append(f"reaches: {transport} {url}".strip())
    return tuple(lines)
# ---------------------------------------------------------------------------
# The preview (`20260913-preview-in-the-panel`, UX-03 Phase 2)
# ---------------------------------------------------------------------------

PREVIEW_TIMEOUT_S: float = 10.0
"""How long a preview may take before the gate stops waiting for it."""

MAX_PREVIEW_OUTPUT: int = 800
"""How much of a preview's output the panel shows."""


def preview_args_for(manifest: object) -> dict:
    """The arguments that turn this call into a dry run, or ``{}``.

    Declared, never inferred.  The operator's answer to UX-00 5.3 permits
    executing a real ``--dry-run``; the risk they accepted is that a dry-run
    flag is the *capability's* claim and not ACC's guarantee.  Keeping the
    claim in the manifest means it is written down, reviewed and -- for a
    packaged capability -- signed, instead of ACC pattern-matching a command
    string and hoping.
    """
    declared = getattr(manifest, "preview_args", None)
    return dict(declared) if isinstance(declared, dict) and declared else {}


async def run_preview(
    core: "Any",
    inv: ParsedInvocation,
    manifest: object,
    role: "Any",
    *,
    task_id: str = "",
    timeout_s: float | None = None,
) -> tuple:
    """Run the declared dry run and render it for the panel.

    Returns evidence lines (empty when the capability declares no preview).

    The preview goes through the same adapter path as the real call, so the
    role's own guard (A-017 / A-018) still applies -- a preview must not reach
    anything the call itself could not.  It does NOT re-enter the oversight
    gate: a preview that asked for approval would be a loop.

    A preview is an execution, and it is journalled as one.  A failure is
    rendered as a failure and changes nothing else: it never resolves the
    decision, never pre-approves, and never blocks the gate.
    """
    declared = preview_args_for(manifest)
    if not declared:
        return ()
    args = dict(inv.args or {})
    args.update(declared)
    limit = PREVIEW_TIMEOUT_S if timeout_s is None else float(timeout_s)

    ok, output, error = True, "", ""
    try:
        if inv.kind == "skill":
            coro = core.invoke_skill(inv.target, args, role)
        elif inv.kind == "mcp":
            server_id, _, tool_name = inv.target.partition(".")
            coro = core.invoke_mcp_tool(server_id, tool_name, args, role)
        else:
            return ()
        result = await asyncio.wait_for(coro, timeout=limit)
        try:
            output = json.dumps(result, default=str)
        except Exception:  # noqa: BLE001
            output = str(result)
    except asyncio.TimeoutError:
        ok, error = False, f"timed out after {limit:g}s"
    except Exception as exc:  # noqa: BLE001 -- a preview must never break the gate
        ok, error = False, f"{type(exc).__name__}: {exc}"

    _journal_preview(inv, args, ok, output, error, task_id)

    if not ok:
        return (f"preview failed: {_clip(error, MAX_PREVIEW_OUTPUT)}",)
    # An adapter that returns nothing, an empty string or an empty container
    # has told the operator nothing; say that instead of rendering `""`.
    if not output or output.strip() in ('""', "{}", "[]", "null"):
        return ("preview ran, no output",)
    return tuple(
        f"preview: {line}" if i == 0 else f"  {line}"
        for i, line in enumerate(_clip(output, MAX_PREVIEW_OUTPUT).splitlines() or [""])
    )


def _journal_preview(
    inv: ParsedInvocation, args: dict, ok: bool, output: str, error: str, task_id: str,
) -> None:
    """Record the preview as its own act.  Best-effort; never raises."""
    logger.info(
        "capability_dispatch: preview ran %s %s (ok=%s)%s",
        inv.kind, inv.target, ok, "" if ok else f" -- {error}",
    )
    try:
        from acc import tracelog  # noqa: PLC0415
        if not tracelog.tracelog_enabled():
            return
        tracelog.log_tool_call(
            task_id or "unknown",
            task_id=task_id,
            kind=f"{inv.kind}:preview",
            target=inv.target,
            args=_mask_secrets(args),
            ok=ok,
            output=_clip(output, MAX_PREVIEW_OUTPUT),
            error=error,
            preview=True,
        )
    except Exception:  # noqa: BLE001 -- journalling must not break a dispatch
        logger.debug("capability_dispatch: preview journal failed", exc_info=True)

