"""Per-role perception snapshot — the Observe step in the cognitive pipeline.

OpenSpec history:
  * `20260531-assistant-action-loop` Phase 1 (v0.3.43, PR #9) shipped
    the Assistant-only proof-of-concept.
  * `20260531-role-perception-profiles` Phase 1 (v0.3.45, this file)
    generalises it: any role with ``RoleDefinitionConfig.perception_profile
    != "none"`` gets a tailored ``## Currently available`` block before
    its LLM call.  Seven standard profiles, each rendered + validated by a
    profile-specific function registered below.

The Assistant gatekeeper (proposal `20260530-assistant-agent-of-agents`)
shipped every downstream primitive — CapabilityIndex (v0.3.42), AoA-P2b
proposal queue (v0.3.27), mode-aware dispatcher (v0.3.26), sub-collective
registry (v0.3.29), identity (v0.3.34), policy learning (v0.3.30) — but
its cognitive_core had no **Observe step**.  Today's lighthouse trace
(2026-05-31 18:51-18:56, small llama-3.2-3B model under AUTO mode)
showed the symptom:

  * Assistant told the operator to "use the `worker-pool` role" — a role
    that doesn't exist in ``roles/``.
  * Assistant asked "which roles do you want to run?" while a
    ``coding_agent`` was already running in the baseline roster.
  * Assistant's reasoning said "I will execute Option A, spawning the
    agentset" but no ``[PROPOSE_SPAWN]`` marker was emitted.

All three are symptoms of the same single missing step.  This module is
the fix: a per-task snapshot of capability + roster + sub-collectives
that the cognitive_core injects into the system prompt before the LLM
call.  Phase 1 of the proposal is purely additive — non-Assistant roles
are untouched.

Design constraints (from `proposal.md`):

  * **Stale-OK > stale-block.**  100ms hard budget on the snapshot;
    annotate stale data in the prompt rather than blocking the task.
  * **Backward compatible.**  Existing legacy bracket markers
    (``[PROPOSE_SPAWN:role:cluster:reason]``) keep working; this module
    only ADDS information to the system prompt.
  * **Fallback to local filesystem.**  When the orchestrator isn't
    deployed or times out, fall back to a synchronous scan of
    ``roles/`` so the Assistant still gets *some* grounding.

Phase 2 adds dual-format reasoning + JSON action emission.  Phase 3
enforces the AUTO contract.  Phase 4 makes the roster snapshot push-based.
Phase 5 reconciles reasoning vs. dispatched markers.

Marker dispatch validation (in cognitive_core, fed by this module):
``[PROPOSE_SPAWN:role:cluster:reason]`` markers whose ``role`` isn't in
the snapshot's known-roles set are rejected + logged.  This is the line
of defence against today's "use the worker-pool role" hallucination.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("acc.perception")


# ---------------------------------------------------------------------------
# Snapshot model
# ---------------------------------------------------------------------------


class PerceptionSnapshot(BaseModel):
    """What the Assistant sees just before composing its system prompt.

    Built per task; **never blocks the hot path > 100ms**.  When a source
    times out the snapshot still returns — degraded but non-empty — with
    the ``stale`` flag set and a per-source ``stale_<name>`` flag for the
    affected slice.  The cognitive_core renders a ``[stale]`` annotation
    so the LLM knows confidence is lower.
    """

    model_config = ConfigDict(extra="forbid")

    # Roster of currently registered agents grouped by role.
    # Shape: ``{"assistant": ["assistant-1"], "coding_agent": ["coding-1"], ...}``.
    roster: dict[str, list[str]] = Field(default_factory=dict)

    # Available roles from the capability catalog: list of dicts shaped
    # like ``CapabilityMatch.model_dump()`` (kind / name / summary /
    # metadata).  Kept as plain dicts here to avoid a circular import.
    available_roles: list[dict[str, Any]] = Field(default_factory=list)

    # Available MCPs — same shape.
    available_mcps: list[dict[str, Any]] = Field(default_factory=list)

    # Available skills — same shape.  Populated when the orchestrator's
    # CapabilityIndex was constructed with a SkillRegistry; empty
    # otherwise (workspace renderer falls back to role.allowed_skills
    # verbatim in that case).
    available_skills: list[dict[str, Any]] = Field(default_factory=list)

    # Sub-collective registry (from CollectiveSpec.managed_sub_collectives).
    # Shape: ``{"sol-code": {"domain": "...", "description": "..."}, ...}``.
    sub_collectives: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Provenance + freshness markers.
    snapshot_ts: float = Field(default_factory=time.time)
    capability_revision: int = 0
    stale: bool = False
    stale_capability: bool = False
    stale_roster: bool = False


# ---------------------------------------------------------------------------
# Configuration knobs (env-tunable)
# ---------------------------------------------------------------------------


# Per-snapshot hard timeout in seconds.  Phase 1 budget is 100ms;
# operators can raise this for slow networks via ``ACC_PERCEPTION_TIMEOUT_S``.
_DEFAULT_TIMEOUT_S = float(os.environ.get("ACC_PERCEPTION_TIMEOUT_S", "0.1") or "0.1")

# TTL for the in-process cache.  Defaults to 30s — same cadence as
# heartbeats, so the cache lives roughly as long as the data underneath
# would change.
_DEFAULT_TTL_S = float(os.environ.get("ACC_PERCEPTION_TTL_S", "30") or "30")

# Token budget for the ``## Currently available`` block in the system
# prompt.  Used by the cognitive_core's renderer (not this module
# directly); we expose it via env so operators can shrink/grow without a
# code change.  600 tokens is a reasonable default for a ~30-role catalog.
PERCEPTION_PROMPT_TOKEN_BUDGET = int(
    os.environ.get("ACC_PERCEPTION_PROMPT_TOKENS", "600") or "600"
)

# OpenSpec `20260602-assistant-blindspots` Phase 1.2 — how many catalog
# entries the control profile renders with their full summary line
# before falling back to a single comma-joined tail line.  At ~8 tokens
# per detailed entry + ~2 tokens per tail name, 40 detailed + 100 tail
# names ≈ 320 + 200 = 520 tokens, comfortably under the default block
# budget.
_DETAILED_ROLE_CAP = int(
    os.environ.get("ACC_PERCEPTION_DETAILED_ROLE_CAP", "40") or "40"
)


# ---------------------------------------------------------------------------
# Filesystem fallback — synchronous; used when the orchestrator is
# unreachable so the Assistant still gets *some* grounding.
# ---------------------------------------------------------------------------


def _fallback_roles_from_disk(roles_root: str | os.PathLike) -> list[dict[str, Any]]:
    """Scan ``roles/`` directly when the orchestrator can't be reached.

    Returns the same dict shape ``CapabilityMatch.model_dump()`` emits so
    the cognitive_core renderer doesn't need to branch on source.
    """
    out: list[dict[str, Any]] = []
    root = Path(roles_root)
    if not root.is_dir():
        return out
    try:
        import yaml  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return out
    for role_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        yaml_path = role_dir / "role.yaml"
        if not yaml_path.exists():
            continue
        try:
            data = yaml.safe_load(yaml_path.read_text()) or {}
        except Exception:  # pragma: no cover (defensive)
            continue
        rd = data.get("role_definition", data) if isinstance(data, dict) else {}
        purpose = (rd.get("purpose") or "").strip()
        out.append({
            "kind": "role",
            "name": role_dir.name,
            "summary": purpose[:140] or f"role {role_dir.name}",
            "metadata": {
                "persona": rd.get("persona", "") or "",
                "task_types": list(rd.get("task_types") or []),
                "version": rd.get("version", "") or "",
            },
        })
    return out


# ---------------------------------------------------------------------------
# Snapshot orchestration — parallel fan-out under the timeout budget.
# ---------------------------------------------------------------------------


async def _query_capabilities(
    bus: Any,
    cid: str,
    *,
    timeout_s: float,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    bool,
]:
    """Ask the orchestrator's capability_query subject for roles + MCPs
    + skills.

    Returns (roles, mcps, skills, capability_revision, stale_flag).
    Stale=True when no orchestrator source replied within the budget —
    skills alone failing is silent because skills are an additive hint
    for the workspace profile, not a critical signal.
    """
    import msgpack  # noqa: PLC0415
    from acc.signals import subject_capability_query  # noqa: PLC0415

    subject = subject_capability_query(cid)
    roles: list[dict[str, Any]] = []
    mcps: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    revision = 0
    stale = False

    async def _one(kind: str) -> list[dict[str, Any]]:
        payload = msgpack.packb({"kind": kind, "limit": 200})
        try:
            reply = await asyncio.wait_for(
                bus.request(subject, payload),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            return []
        except Exception as exc:  # pragma: no cover (bus-specific failures)
            logger.debug("perception: capability_query(%s) failed: %s", kind, exc)
            return []
        try:
            data = msgpack.unpackb(_payload_bytes(reply), raw=False)
        except Exception:
            return []
        matches = data.get("matches") or []
        nonlocal revision
        revision = max(revision, int(data.get("catalog_revision") or 0))
        return matches

    # Parallel fan-out — three queries under one combined budget.
    try:
        roles_task = asyncio.create_task(_one("role"))
        mcps_task = asyncio.create_task(_one("mcp"))
        skills_task = asyncio.create_task(_one("skill"))
        roles = await roles_task
        mcps = await mcps_task
        skills = await skills_task
    except asyncio.TimeoutError:
        stale = True
    # Stale iff *all* sources fell back to empty (skills alone may
    # legitimately be empty — orchestrator runs without a SkillRegistry
    # in some deployments).
    if not roles and not mcps:
        stale = True
    return roles, mcps, skills, revision, stale


async def _query_roster(
    bus: Any,
    cid: str,
    *,
    timeout_s: float,
) -> tuple[dict[str, list[str]], bool]:
    """Ask the arbiter's roster_snapshot subject for the live roster.

    Returns (roster_by_role, stale_flag).
    """
    import msgpack  # noqa: PLC0415
    from acc.signals import subject_roster_snapshot  # noqa: PLC0415

    subject = subject_roster_snapshot(cid)
    try:
        reply = await asyncio.wait_for(
            bus.request(subject, b""),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return {}, True
    except Exception as exc:  # pragma: no cover
        logger.debug("perception: roster_snapshot failed: %s", exc)
        return {}, True
    try:
        data = msgpack.unpackb(_payload_bytes(reply), raw=False)
    except Exception:
        return {}, True
    # Expected shape: ``{"roster": {role: [agent_id, ...]}, ...}``.
    roster = data.get("roster") if isinstance(data, dict) else None
    if not isinstance(roster, dict):
        return {}, True
    return roster, False


def _payload_bytes(msg: Any) -> bytes:
    """Best-effort extraction of bytes from a NATS-style reply object."""
    data = getattr(msg, "data", None)
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(msg, (bytes, bytearray, memoryview)):
        return bytes(msg)
    return b""


# ---------------------------------------------------------------------------
# Public entry — what the cognitive_core calls.
# ---------------------------------------------------------------------------


async def snapshot_for_role(
    *,
    bus: Any,
    cid: str,
    profile: str = "control",
    role: Any = None,
    sub_collectives: dict[str, dict[str, Any]] | None = None,
    roles_root: str | os.PathLike | None = None,
    timeout_s: float | None = None,
) -> PerceptionSnapshot:
    """Build a fresh PerceptionSnapshot tailored to *profile*.

    Each profile selects which sources are queried:

    +-------------+-----------+--------+----------------+
    | Profile     | Catalog?  | Roster?| Sub-collectives|
    +=============+===========+========+================+
    | control     | yes       | yes    | yes            |
    | workspace   | yes       | yes    | no             |
    | domain      | partial   | yes    | yes            |
    | reviewer    | no        | yes    | no             |
    | output      | no        | yes    | no             |
    | customer    | partial   | yes    | no             |
    | queue       | no        | yes    | no             |
    +-------------+-----------+--------+----------------+

    The Phase 1 implementation runs the same parallel fan-out for both
    ``control`` and ``workspace`` (the source set is the same; the
    rendering differs).  Later phases narrow per profile to save tokens
    when a profile genuinely doesn't need a source.
    """
    return await _snapshot(
        bus=bus,
        cid=cid,
        sub_collectives=sub_collectives,
        roles_root=roles_root,
        timeout_s=timeout_s,
    )


async def snapshot_for_assistant(
    *,
    bus: Any,
    cid: str,
    sub_collectives: dict[str, dict[str, Any]] | None = None,
    roles_root: str | os.PathLike | None = None,
    timeout_s: float | None = None,
) -> PerceptionSnapshot:
    """v0.3.43 backward-compat shim.  Delegates to ``snapshot_for_role``
    with ``profile="control"`` so existing callers keep working
    byte-identically."""
    return await snapshot_for_role(
        bus=bus,
        cid=cid,
        profile="control",
        sub_collectives=sub_collectives,
        roles_root=roles_root,
        timeout_s=timeout_s,
    )


async def _snapshot(
    *,
    bus: Any,
    cid: str,
    sub_collectives: dict[str, dict[str, Any]] | None = None,
    roles_root: str | os.PathLike | None = None,
    timeout_s: float | None = None,
) -> PerceptionSnapshot:
    """Build a fresh PerceptionSnapshot.

    Args:
        bus:  The agent's NATS-style signaling backend.  Must expose a
              ``request(subject, payload, *, timeout=...)`` coroutine
              that resolves to a reply object with a ``.data`` byte
              field (the standard nats-py shape).  When ``bus`` is
              ``None`` (used by tests + when the bus isn't connected
              yet), only the filesystem fallback runs.
        cid:  Collective ID; used to derive bus subject names.
        sub_collectives:  Pass-through of CollectiveSpec's managed
              registry shape.  ``None`` when the Assistant runs in a
              single-collective hub.
        roles_root:  Filesystem path to scan when the orchestrator
              times out.  Defaults to ``/app/roles`` (in-container).
        timeout_s:  Per-source budget.  Default 100ms.

    Returns:
        A populated :class:`PerceptionSnapshot`.  Stale flags signal
        which sources fell back / timed out.
    """
    timeout_s = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
    snap = PerceptionSnapshot()
    snap.sub_collectives = dict(sub_collectives or {})

    if bus is not None and hasattr(bus, "request"):
        # Parallel fan-out: capability + roster under the same budget.
        cap_task = asyncio.create_task(
            _query_capabilities(bus, cid, timeout_s=timeout_s)
        )
        roster_task = asyncio.create_task(
            _query_roster(bus, cid, timeout_s=timeout_s)
        )
        try:
            cap_result, roster_result = await asyncio.gather(
                cap_task, roster_task, return_exceptions=False,
            )
            roles, mcps, skills, revision, cap_stale = cap_result
            roster, roster_stale = roster_result
            snap.available_roles = roles
            snap.available_mcps = mcps
            snap.available_skills = skills
            snap.capability_revision = revision
            snap.roster = roster
            snap.stale_capability = cap_stale
            snap.stale_roster = roster_stale
        except Exception as exc:  # pragma: no cover (defensive)
            logger.warning("perception: snapshot fan-out failed: %s", exc)
            snap.stale_capability = True
            snap.stale_roster = True

    # Filesystem fallback when the orchestrator path didn't fill anything.
    if not snap.available_roles:
        fallback_root = roles_root if roles_root is not None else os.environ.get(
            "ACC_ROLES_ROOT", "/app/roles"
        )
        fallback_roles = _fallback_roles_from_disk(fallback_root)
        if fallback_roles:
            snap.available_roles = fallback_roles
            # Capability is stale but we have *something* — don't pretend
            # to be fresh; the renderer will mark this block "[fallback]".
            snap.stale_capability = True

    snap.snapshot_ts = time.time()
    snap.stale = snap.stale_capability or snap.stale_roster
    return snap


# ---------------------------------------------------------------------------
# Marker validation — fed by the snapshot; called from cognitive_core.
# ---------------------------------------------------------------------------


def validate_marker_target(
    snapshot: PerceptionSnapshot,
    target_role: str,
) -> bool:
    """Reject hallucinated ``target_role`` values before they hit dispatch.

    Returns True when ``target_role`` is present in the snapshot's
    running roster OR available-roles catalog.  False otherwise (the
    classic case: an LLM emitting ``[PROPOSE_SPAWN:worker-pool:...]``
    when no ``worker-pool`` role exists).

    Kept for backward compatibility with the v0.3.43 Assistant-only
    code-path.  New callers should prefer :func:`validate_marker`,
    which dispatches per profile.
    """
    if target_role in snapshot.roster:
        return True
    return any(r.get("name") == target_role for r in snapshot.available_roles)


def validate_marker(
    profile: str,
    snapshot: PerceptionSnapshot,
    marker: Any,
    *,
    role: Any = None,
) -> bool:
    """Profile-aware marker validation.

    * ``control``  — ``target_role`` must be in snapshot.roster or
                     available_roles (matches today's behaviour).
    * ``workspace``— ``[USE_SKILL:name:...]`` must name a skill in
                     ``role.allowed_skills``; ``[USE_MCP:name:...]`` must
                     name an MCP in ``role.allowed_mcps``.  Other marker
                     kinds pass through unchanged (workspace roles
                     should not be emitting [PROPOSE_SPAWN]).
    * Other profiles fall back to control-style ``target_role`` check.

    ``marker`` is the parsed proposal object (has ``kind`` and
    ``target_role`` attributes when emitted by
    :func:`parse_proposal_markers`); for workspace markers the
    ``target_role`` field carries the skill / MCP name.
    """
    target = getattr(marker, "target_role", "") or ""
    kind = getattr(marker, "kind", "") or ""

    if profile == "workspace":
        if kind == "USE_SKILL" and role is not None:
            allowed = set(getattr(role, "allowed_skills", []) or [])
            return target in allowed
        if kind == "USE_MCP" and role is not None:
            allowed = set(getattr(role, "allowed_mcps", []) or [])
            return target in allowed
        # Workspace roles emitting other marker kinds — accept (no
        # snapshot-side data to validate against in Phase 1).
        return True

    # control / fallback profiles — match v0.3.43 semantics.
    if not target:
        return True
    return validate_marker_target(snapshot, target)


# ---------------------------------------------------------------------------
# Prompt block renderers — profile dispatch via _PROFILE_RENDERERS.
# ---------------------------------------------------------------------------


def _render_control(snapshot: PerceptionSnapshot, role: Any = None) -> str:
    """``control`` profile — the v0.3.43 Assistant ``## Currently available``
    block.  Surfaces full roster + catalog + sub-collectives.  Token
    budget ~1 KB on the lighthouse 6-role baseline."""
    lines: list[str] = ["## Currently available"]

    if snapshot.stale:
        flag = []
        if snapshot.stale_capability:
            flag.append("capability stale")
        if snapshot.stale_roster:
            flag.append("roster stale")
        lines.append(f"_[snapshot: {' + '.join(flag)} — proceed but with reduced confidence]_")

    if snapshot.roster:
        lines.append("")
        lines.append("**Running agents** (use these first if they fit):")
        for role_name, agent_ids in sorted(snapshot.roster.items()):
            ids = ", ".join(sorted(agent_ids))
            lines.append(f"- {role_name} → {ids}")

    running = set(snapshot.roster.keys())
    other_roles = [r for r in snapshot.available_roles
                   if r.get("name") and r["name"] not in running]
    if other_roles:
        lines.append("")
        lines.append("**Available roles** (can be spawned via `[PROPOSE_SPAWN:role:cluster:reason]`):")
        # OpenSpec `20260602-assistant-blindspots` Phase 1.2 — kill the
        # "... and N more" cliff.  Today's lighthouse trace truncated
        # the catalog at 25 roles and the Assistant proposed a
        # hallucinated `research_agent` because it couldn't see the real
        # tail.  Detailed entries cap at ``_DETAILED_ROLE_CAP``; any
        # overflow gets emitted as a single comma-joined name-only line
        # so the LLM still sees the names and can ask follow-up.
        for r in other_roles[:_DETAILED_ROLE_CAP]:
            summary = (r.get("summary") or "").strip()
            lines.append(f"- {r['name']}: {summary}")
        if len(other_roles) > _DETAILED_ROLE_CAP:
            tail_names = [
                r["name"] for r in other_roles[_DETAILED_ROLE_CAP:]
                if r.get("name")
            ]
            if tail_names:
                lines.append(
                    "- (also available, ask if relevant): "
                    + ", ".join(tail_names)
                )

    if snapshot.available_mcps:
        lines.append("")
        lines.append("**Available MCPs** (tool servers):")
        for m in snapshot.available_mcps[:15]:
            summary = (m.get("summary") or "").strip()
            risk = (m.get("metadata") or {}).get("risk_level", "UNKNOWN")
            lines.append(f"- {m['name']} ({risk}): {summary}")

    if snapshot.sub_collectives:
        lines.append("")
        lines.append("**Managed sub-collectives** (delegate via `[DELEGATE:cid:reason]`):")
        for cid, meta in sorted(snapshot.sub_collectives.items()):
            domain = (meta or {}).get("domain", "")
            desc = (meta or {}).get("description", "")
            lines.append(f"- {cid}: domain={domain or '?'} — {desc}")

    lines.append("")
    lines.append(
        "**Important:** when you recommend or spawn a role, it MUST appear above. "
        "Roles not in this list do not exist in this collective."
    )

    return "\n".join(lines)


def _render_workspace(snapshot: PerceptionSnapshot, role: Any = None) -> str:
    """``workspace`` profile — tailored to roles like ``coding_agent`` /
    ``ingester`` / ``analyst`` whose action surface is *their own
    workspace + allowed skills/MCPs*, not the full roster.

    Surfaces:
      * Workspace path (env-derived; the agent's bind-mounted dir).
      * Allowed skills intersected with the live catalog.
      * Allowed MCPs intersected with the live catalog (with risk).
      * Sibling workers (other agents sharing this role).

    Token budget ~400 B — roughly half the control block, since most
    workspace roles only care about their own tools.
    """
    lines: list[str] = ["## Currently available"]

    if snapshot.stale:
        flag = []
        if snapshot.stale_capability:
            flag.append("capability stale")
        if snapshot.stale_roster:
            flag.append("roster stale")
        lines.append(f"_[snapshot: {' + '.join(flag)} — proceed but with reduced confidence]_")

    # Workspace path — env-derived, mounted by the operator at agent boot.
    ws = os.environ.get("ACC_WORKSPACE_BASE") or os.environ.get(
        "ACC_WORKSPACE_DIR", ""
    )
    if ws:
        lines.append("")
        lines.append(f"**Your workspace:** `{ws}`")

    role_name = getattr(role, "role_label", "") if role is not None else ""

    # Intersect role-declared skills with the live catalog so the LLM
    # only sees what's actually deployable RIGHT NOW.  Falls back to
    # role.allowed_skills verbatim when the catalog has no skills
    # (orchestrator running without a SkillRegistry).
    allowed_skills = list(getattr(role, "allowed_skills", []) or []) if role else []
    if allowed_skills:
        catalog_skills = {s.get("name") for s in snapshot.available_skills}
        if catalog_skills:
            visible = [s for s in allowed_skills if s in catalog_skills]
        else:
            visible = list(allowed_skills)
        if visible:
            lines.append("")
            lines.append(
                "**Your allowed skills** (call via `[USE_SKILL:name:args]`):"
            )
            for s in visible[:15]:
                lines.append(f"- {s}")

    allowed_mcps = list(getattr(role, "allowed_mcps", []) or []) if role else []
    if allowed_mcps:
        # Build a name → (summary, risk) view of the catalog for any
        # MCPs we do see; otherwise list verbatim.
        catalog_mcps = {m.get("name"): m for m in snapshot.available_mcps}
        visible_mcps = [m for m in allowed_mcps if not catalog_mcps or m in catalog_mcps]
        if visible_mcps:
            lines.append("")
            lines.append(
                "**Your allowed MCPs** (call via `[USE_MCP:name:args]`):"
            )
            for m in visible_mcps[:10]:
                meta = catalog_mcps.get(m) or {}
                risk = (meta.get("metadata") or {}).get("risk_level", "UNKNOWN")
                summary = (meta.get("summary") or "").strip()
                if summary:
                    lines.append(f"- {m} ({risk}): {summary}")
                else:
                    lines.append(f"- {m}")

    # Sibling workers — other agents on the same role.
    if role_name and role_name in snapshot.roster:
        siblings = [a for a in snapshot.roster.get(role_name, [])
                    if a]
        if siblings:
            lines.append("")
            lines.append(
                f"**Sibling workers in cluster** ({role_name}): "
                + ", ".join(sorted(siblings))
            )

    return "\n".join(lines)


# Registry of profile → renderer.  Profiles without a renderer
# (Phase 1: every value except ``control`` / ``workspace``) fall back
# to the control renderer until their dedicated implementation lands
# in subsequent phases.
_PROFILE_RENDERERS: dict[str, Any] = {
    "control": _render_control,
    "workspace": _render_workspace,
}


def render_for_role(snapshot: PerceptionSnapshot, role: Any) -> str:
    """Dispatch on ``role.perception_profile`` and call the matching
    renderer.  Returns an empty string when the profile is ``none``
    (the default for legacy roles) — caller should skip injection.
    """
    profile = getattr(role, "perception_profile", "none") if role else "none"
    if profile == "none":
        return ""
    renderer = _PROFILE_RENDERERS.get(profile, _render_control)
    return renderer(snapshot, role)


def render_currently_available_block(snapshot: PerceptionSnapshot) -> str:
    """v0.3.43 backward-compat wrapper — emits the control-profile block
    so existing callers (and the Assistant-only code path) keep their
    byte-identical output."""
    return _render_control(snapshot, None)
