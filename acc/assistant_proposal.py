"""ASSISTANT_PROPOSAL — gatekeeper mutations gated by Compliance queue.

Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 2 (sub-phase 2a).

The Assistant is the operator's gatekeeper.  When his reasoning concludes
"I should spawn a coding_agent" / "I should infuse this role.yaml diff" /
"I should route this prompt to coding_agent_reviewer", we don't want him
to act blindly.  Each such mutation lands as a **proposal** that the
operator either approves via the Compliance queue (default) or that
auto-executes when the operating mode allows it.

Three proposal kinds (Phase 2a):

* **`spawn`** — bring a new role-bound agent online (publishes
  `collective.reconcile` on approval).
* **`role_update`** — adjust a role's `RoleDefinitionConfig` fields
  (publishes ROLE_UPDATE on approval).
* **`route`** — re-dispatch the current prompt to a different role
  (publishes TASK_ASSIGN on approval).

Mode gating (reuses PR-L's :mod:`acc.operating_modes`):

* **PLAN** — emit the would-be proposal as a reasoning trace; no
  mutation lands.
* **ASK_PERMISSIONS** *(default)* — every proposal queues in the
  Compliance surface; operator approves to execute.
* **ACCEPT_EDITS** — small mutations (`route` only in P2a) auto-execute;
  structural ones (`spawn`, `role_update`) still queue.
* **AUTO** — auto-execute every proposal kind.  Cat-A/B/C still gate
  (that's the safety floor — frozen-in-AUTO from the SIP proposal's
  rail 6 also intersects here).

The marker parser (`parse_proposal_markers`) reads
``[PROPOSE_SPAWN:role:cluster_id:reason]`` /
``[PROPOSE_ROLE_UPDATE:role:field=value:reason]`` /
``[PROPOSE_ROUTE:target_role:reason]`` from Assistant LLM output.
Phase 2b wires this parser into the cognitive core; Phase 2a ships the
parser + the dispatcher + the mode-gating logic so 2b is a thin call.

Reward harness pickup: on operator approve/reject, the existing
``subject_oversight_decision_all`` carries the verdict — the policy-
layer's :class:`acc.policy_layer.RewardHarness` (SIP-P1) already
subscribes to that subject.  So *no new wiring* is needed for
approvals to become reward signals; SIP-P2 reads from the EWMA the
same way it would for Cat-A items.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from acc.operating_modes import (
    MODE_ACCEPT_EDITS,
    MODE_ASK_PERMISSIONS,
    MODE_AUTO,
    MODE_PLAN,
    normalise,
)

logger = logging.getLogger("acc.assistant_proposal")


# Proposal kinds — kept as module-level constants so callers + tests
# pin a stable string contract (the wire payload uses these verbatim).
PROPOSAL_SPAWN = "spawn"
PROPOSAL_ROLE_UPDATE = "role_update"
PROPOSAL_ROUTE = "route"
PROPOSAL_INFUSE = "infuse"  # Stage 1.4 — install an @scope/name@constraint pkg

PROPOSAL_KINDS: frozenset[str] = frozenset({
    PROPOSAL_SPAWN, PROPOSAL_ROLE_UPDATE, PROPOSAL_ROUTE, PROPOSAL_INFUSE,
})


# Default risk classification per kind.  Operators / Cat-A evaluator
# can override per-proposal via the ``risk_level`` attribute; the
# defaults set the dispatch-to-queue baseline.
DEFAULT_RISK_LEVEL: dict[str, str] = {
    PROPOSAL_SPAWN: "MEDIUM",        # structural; non-reversible until terminate
    PROPOSAL_ROLE_UPDATE: "HIGH",    # changes role definition system-wide
    PROPOSAL_ROUTE: "LOW",           # reversible by next prompt
    PROPOSAL_INFUSE: "HIGH",         # filesystem state; reversible only by uninstall
}


# Dispatch decisions — what ``decide_dispatch`` returns and what the
# Assistant's cognitive loop acts on.
DISPATCH_PLAN = "plan"        # render as reasoning only; no execute
DISPATCH_QUEUE = "queue"      # enqueue on Compliance oversight surface
DISPATCH_EXECUTE = "execute"  # publish the underlying mutation immediately


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AssistantProposal:
    """A mutation the Assistant wants to perform on the collective.

    Wire-friendly: ``to_payload`` / ``from_payload`` round-trip through
    JSON-safe dicts so the same struct flies on the bus, lands in
    Redis (Compliance queue), and is consumed by the dispatch helper.

    Fields:
        proposal_id:  UUID4 string; primary key on the queue.
        kind:         One of ``PROPOSAL_KINDS``.
        params:       Kind-specific dict (see ``parse_proposal_markers``
                      for the documented shapes).
        risk_level:   "LOW" | "MEDIUM" | "HIGH" | "UNACCEPTABLE".
                      Defaults to ``DEFAULT_RISK_LEVEL[kind]``.
        summary:      Human-readable one-liner the Compliance UI shows.
        rationale:    Longer reasoning surfaced on the detail pane.
        operator_id:  Who the proposal acts on behalf of.  Defaults
                      to ``"default"`` until the multi-user proposal lands.
        proposed_at_ts: Epoch seconds when the LLM emitted the marker.
        collective_id: Target collective for the mutation.
        agent_id:     Assistant agent emitting the proposal.
        task_id:      Prompt that produced the proposal (for credit-share
                      in SIP-P2's rail 1).
    """

    proposal_id: str = ""
    kind: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    risk_level: str = ""
    summary: str = ""
    rationale: str = ""
    operator_id: str = "default"
    proposed_at_ts: float = 0.0
    collective_id: str = ""
    agent_id: str = ""
    task_id: str = ""

    def __post_init__(self) -> None:
        if not self.proposal_id:
            self.proposal_id = str(uuid.uuid4())
        if not self.proposed_at_ts:
            self.proposed_at_ts = time.time()
        if not self.risk_level:
            self.risk_level = DEFAULT_RISK_LEVEL.get(self.kind, "MEDIUM")

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> "AssistantProposal":
        # Defensive load: drop unknown keys so a future field addition
        # in a peer can't crash an older consumer.
        valid = {k: raw.get(k) for k in (
            "proposal_id", "kind", "params", "risk_level", "summary",
            "rationale", "operator_id", "proposed_at_ts",
            "collective_id", "agent_id", "task_id",
        ) if k in raw}
        return cls(**valid)


# ---------------------------------------------------------------------------
# Mode gating — what to do with a fresh proposal
# ---------------------------------------------------------------------------


# Phase 2a's "small mutation" set under ACCEPT_EDITS.  ROUTE is the
# only one classified as small today (reversible by the next prompt).
# SPAWN + ROLE_UPDATE are structural and stay queued.
_ACCEPT_EDITS_AUTOEXEC: frozenset[str] = frozenset({PROPOSAL_ROUTE})


# Stage 1.4 — operator decision: PROPOSE_INFUSE always routes through
# the Compliance pane regardless of operating mode.  Filesystem state
# is reversible only by uninstall; per the Stage 1 proposal's open
# decision Q2 the operator chose "always Compliance pane" over "AUTO
# may infuse autonomously".
_NEVER_AUTOEXEC: frozenset[str] = frozenset({PROPOSAL_INFUSE})


def decide_dispatch(operating_mode: str, kind: str) -> str:
    """Return one of ``DISPATCH_PLAN`` / ``DISPATCH_QUEUE`` / ``DISPATCH_EXECUTE``.

    Pure function; safe for unit tests + the cognitive core's per-task
    decision tree.  Unknown ``kind`` defaults to QUEUE (safest: human
    in the loop before any mutation we don't recognise).

    Stage 1.4: kinds in :data:`_NEVER_AUTOEXEC` (currently
    ``PROPOSAL_INFUSE``) always queue — even in AUTO mode.
    """
    mode = normalise(operating_mode or "")
    if kind not in PROPOSAL_KINDS:
        return DISPATCH_QUEUE
    if mode == MODE_PLAN:
        return DISPATCH_PLAN
    if kind in _NEVER_AUTOEXEC:
        # Filesystem-state mutations always go through Compliance pane.
        return DISPATCH_QUEUE
    if mode == MODE_AUTO:
        return DISPATCH_EXECUTE
    if mode == MODE_ACCEPT_EDITS and kind in _ACCEPT_EDITS_AUTOEXEC:
        return DISPATCH_EXECUTE
    # MODE_ASK_PERMISSIONS or ACCEPT_EDITS for a structural kind.
    return DISPATCH_QUEUE


# ---------------------------------------------------------------------------
# Marker parser — `[PROPOSE_*:…]` in Assistant LLM output
# ---------------------------------------------------------------------------


# Shapes (canonical):
#   [PROPOSE_SPAWN:<role>:<cluster_id>:<reason>]
#   [PROPOSE_ROLE_UPDATE:<role>:<field=value;field=value>:<reason>]
#   [PROPOSE_ROUTE:<target_role>:<reason>]
#   [PROPOSE_INFUSE:<@scope/name@constraint>:<reason>]      (Stage 1.4)
_RE_SPAWN = re.compile(
    r"\[PROPOSE_SPAWN:([^:\]]+):([^:\]]*):([^\]]+)\]"
)
_RE_ROLE_UPDATE = re.compile(
    r"\[PROPOSE_ROLE_UPDATE:([^:\]]+):([^:\]]*):([^\]]+)\]"
)
_RE_ROUTE = re.compile(
    r"\[PROPOSE_ROUTE:([^:\]]+):([^\]]+)\]"
)
# INFUSE: first group captures @scope/name@constraint (no colon, no
# whitespace — constraint ranges like ">=1.2 <2.0" are NOT supported
# in markers; use a caret/tilde/exact in the LLM's output instead).
_RE_INFUSE = re.compile(
    r"\[PROPOSE_INFUSE:(@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9_-]*@[^\s:\]]+):([^\]]+)\]"
)


# OpenSpec `20260602-role-proposal-assistant-blindspots` Phase 1.1 — marker-form
# tolerance.  Today's lighthouse trace shows the small Assistant LLM
# emitting a backtick-wrapped PROPOSE_SPAWN marker rather than the
# canonical square-bracket form; the strict regexes above silently
# drop it.  We pre-normalise three alternative delimiter forms back
# to the canonical shape so the parser + downstream `validate_marker`
# still catch hallucinated role names.
_RE_BACKTICK_MARKER = re.compile(
    r"`(PROPOSE_(?:SPAWN|ROLE_UPDATE|ROUTE|INFUSE):[^`\n]+)`"
)
_RE_BARE_LINE_MARKER = re.compile(
    r"(?m)^(PROPOSE_(?:SPAWN|ROLE_UPDATE|ROUTE|INFUSE):[^\n\[\]`]+)$"
)


def _normalize_marker_delimiters(text: str) -> str:
    """Rewrite backtick- and bare-line marker forms into the canonical
    square-bracket form so the strict per-marker regexes match.

    Idempotent on already-canonical input.  Touches only sequences that
    start with ``PROPOSE_SPAWN`` / ``PROPOSE_ROLE_UPDATE`` /
    ``PROPOSE_ROUTE`` — leaves prose mentioning other words alone.
    """
    if not text:
        return text
    text = _RE_BACKTICK_MARKER.sub(r"[\1]", text)
    text = _RE_BARE_LINE_MARKER.sub(r"[\1]", text)
    return text


def parse_proposal_markers(text: str) -> list[AssistantProposal]:
    """Extract every ``[PROPOSE_*:…]`` marker from ``text``.

    Empty list when nothing matches — the Assistant didn't propose
    anything (most prompts are answer-it-yourself).  Multiple markers
    can co-exist in one response; each becomes its own proposal.

    The parser tolerates three delimiter forms (per Phase 1.1 of
    `20260602-role-proposal-assistant-blindspots`):
    - canonical square-bracket: ``[PROPOSE_*:...]``
    - backtick-wrapped (small-LLM drift): see ``_RE_BACKTICK_MARKER``
    - bare ``PROPOSE_*:...`` on its own line

    Field semantics:
    - Spawn ``params``: ``{"role": str, "cluster_id": str}``.
    - Role-update ``params``: ``{"role": str, "fields": {key: value, ...}}``;
      fields parsed from ``key1=val1;key2=val2``.  Value strings are
      kept as-is (the arbiter validates types against
      :class:`acc.config.RoleDefinitionConfig`).
    - Route ``params``: ``{"target_role": str}``.
    """
    if not text:
        return []
    text = _normalize_marker_delimiters(text)
    out: list[AssistantProposal] = []
    for role, cluster_id, reason in _RE_SPAWN.findall(text):
        out.append(AssistantProposal(
            kind=PROPOSAL_SPAWN,
            params={"role": role.strip(), "cluster_id": cluster_id.strip()},
            summary=f"Spawn {role.strip()}"
                    + (f" in {cluster_id.strip()}" if cluster_id.strip() else ""),
            rationale=reason.strip(),
        ))
    for role, fields_blob, reason in _RE_ROLE_UPDATE.findall(text):
        fields: dict[str, str] = {}
        for kv in fields_blob.split(";"):
            kv = kv.strip()
            if not kv or "=" not in kv:
                continue
            k, _, v = kv.partition("=")
            k, v = k.strip(), v.strip()
            if k:
                fields[k] = v
        out.append(AssistantProposal(
            kind=PROPOSAL_ROLE_UPDATE,
            params={"role": role.strip(), "fields": fields},
            summary=f"Update {role.strip()} ({len(fields)} field(s))",
            rationale=reason.strip(),
        ))
    for target_role, reason in _RE_ROUTE.findall(text):
        out.append(AssistantProposal(
            kind=PROPOSAL_ROUTE,
            params={"target_role": target_role.strip()},
            summary=f"Route to {target_role.strip()}",
            rationale=reason.strip(),
        ))
    # Stage 1.4 — PROPOSE_INFUSE.  First group is "@scope/name@constraint";
    # parse via the same helper :class:`acc.collective` uses for
    # ``required_packages`` so the constraint syntax stays consistent.
    for spec, reason in _RE_INFUSE.findall(text):
        try:
            from acc.collective import parse_required_package  # noqa: PLC0415
            name, constraint = parse_required_package(spec.strip())
        except Exception:  # noqa: BLE001
            # Malformed spec — surface as a logged warning, skip the
            # marker.  Same posture as other malformed markers: we don't
            # let one bad marker block the rest of the prompt.
            logger.warning(
                "assistant_proposal: PROPOSE_INFUSE spec %r failed to parse",
                spec,
            )
            continue
        out.append(AssistantProposal(
            kind=PROPOSAL_INFUSE,
            params={"name": name, "constraint": constraint},
            summary=f"Install {name}@{constraint}",
            rationale=reason.strip(),
        ))
    return out


# ---------------------------------------------------------------------------
# Dispatch — publish the underlying mutation on approval / auto-execute
# ---------------------------------------------------------------------------


async def dispatch_approved_proposal(
    signaling,
    proposal: AssistantProposal,
) -> bool:
    """Publish the actual mutation that fulfils ``proposal``.

    Called by:
    - The Compliance queue's approve handler (after operator click).
    - The Assistant's cognitive loop in AUTO mode (or ACCEPT_EDITS for
      ROUTE proposals) — bypassing the queue.

    Returns ``True`` on a successful publish, ``False`` otherwise.  Best-
    effort: failures log + return False; callers can retry or surface
    a banner.  No exception escapes.
    """
    if not proposal or not proposal.kind:
        logger.warning("assistant_proposal: empty proposal — skipping dispatch")
        return False
    cid = proposal.collective_id
    if not cid:
        logger.warning(
            "assistant_proposal: no collective_id on proposal %s — skipping",
            proposal.proposal_id,
        )
        return False
    try:
        if proposal.kind == PROPOSAL_SPAWN:
            return await _dispatch_spawn(signaling, cid, proposal)
        if proposal.kind == PROPOSAL_ROLE_UPDATE:
            return await _dispatch_role_update(signaling, cid, proposal)
        if proposal.kind == PROPOSAL_ROUTE:
            return await _dispatch_route(signaling, cid, proposal)
        if proposal.kind == PROPOSAL_INFUSE:
            return await _dispatch_infuse(signaling, cid, proposal)
    except Exception:
        logger.exception(
            "assistant_proposal: dispatch failed for kind=%s id=%s",
            proposal.kind, proposal.proposal_id,
        )
        return False
    logger.warning(
        "assistant_proposal: unknown kind %r — no dispatcher", proposal.kind,
    )
    return False


async def _dispatch_spawn(signaling, cid: str, p: AssistantProposal) -> bool:
    """Publish a `collective.reconcile` nudge naming the new role.

    The arbiter's reconcile loop (PR-M) picks the next free dormant
    worker and publishes a signed ROLE_ASSIGN.  We don't sign here —
    the arbiter owns that authority; the Assistant only triggers.
    """
    from acc.signals import subject_collective_reconcile  # noqa: PLC0415
    payload = {
        "trigger": "assistant_proposal",
        "proposal_id": p.proposal_id,
        "role": p.params.get("role", ""),
        "cluster_id": p.params.get("cluster_id", ""),
        "ts": time.time(),
    }
    await signaling.publish(subject_collective_reconcile(cid), payload)
    logger.info(
        "assistant_proposal: spawn dispatched — role=%r cluster=%r",
        payload["role"], payload["cluster_id"],
    )
    return True


async def _dispatch_role_update(signaling, cid: str, p: AssistantProposal) -> bool:
    """Publish a ROLE_UPDATE carrying the requested field overrides.

    The arbiter / RoleStore countersigns and writes to Redis;
    subscribed agents hot-reload (PR-D-002 path).
    """
    from acc.signals import subject_role_update  # noqa: PLC0415
    payload = {
        "trigger": "assistant_proposal",
        "proposal_id": p.proposal_id,
        "role": p.params.get("role", ""),
        "fields": p.params.get("fields", {}),
        "ts": time.time(),
    }
    await signaling.publish(subject_role_update(cid), payload)
    logger.info(
        "assistant_proposal: role_update dispatched — role=%r fields=%s",
        payload["role"], list(payload["fields"].keys()),
    )
    return True


async def _dispatch_infuse(signaling, cid: str, p: AssistantProposal) -> bool:
    """Stage 1.4 — install the requested package via the catalog.

    Resolves ``params["name"]`` + ``params["constraint"]`` through
    :func:`acc.pkg.fetch.fetch_and_install`, which handles the catalog
    walk + download + cosign verify + install.  Idempotent: a
    re-approve of an already-installed pkg is a no-op.

    On success we publish a thin notification on the bus so the
    Marketplace / Capability surfaces can refresh; on failure we log
    the cosign / EC / dep error and return False (caller may retry
    or surface the error in the Compliance pane).
    """
    from acc.pkg.fetch import FetchError, fetch_and_install  # noqa: PLC0415
    from acc.signals import subject_assistant_proposal  # noqa: PLC0415

    name = p.params.get("name", "").strip()
    constraint = p.params.get("constraint", ">=0.0.0").strip() or ">=0.0.0"
    if not name:
        logger.warning(
            "assistant_proposal: infuse proposal %s has no name",
            p.proposal_id,
        )
        return False

    try:
        result = fetch_and_install(name, constraint)
    except FetchError as exc:
        logger.warning(
            "assistant_proposal: infuse %s@%s failed: %s",
            name, constraint, exc,
        )
        return False
    except Exception:  # noqa: BLE001 — surface in log, don't crash dispatch loop
        logger.exception(
            "assistant_proposal: infuse %s@%s crashed", name, constraint,
        )
        return False

    payload = {
        "trigger": "assistant_proposal",
        "proposal_id": p.proposal_id,
        "name": result.install.entry.name,
        "version": result.install.entry.version,
        "install_path": str(result.install.install_path),
        "was_already_installed": result.install.was_already_installed,
        "ts": time.time(),
    }
    try:
        await signaling.publish(subject_assistant_proposal(cid), payload)
    except Exception:  # noqa: BLE001 — bus failure is logged; install succeeded
        logger.exception(
            "assistant_proposal: failed to publish infuse-completed for %s",
            p.proposal_id,
        )
    logger.info(
        "assistant_proposal: infuse dispatched — %s@%s%s",
        payload["name"], payload["version"],
        " (idempotent)" if payload["was_already_installed"] else "",
    )
    return True


async def _dispatch_route(signaling, cid: str, p: AssistantProposal) -> bool:
    """Re-publish the originating TASK_ASSIGN targeting the new role.

    Phase 2a publishes a thin re-dispatch marker; Phase 2b in the
    cognitive core threads the original task_payload so the receiver
    gets the full prompt.  Today the receiver re-reads from the task
    record (Redis is the source of truth).
    """
    from acc.signals import SIG_TASK_ASSIGN, subject_task_assign  # noqa: PLC0415
    payload = {
        "signal_type": SIG_TASK_ASSIGN,
        "trigger": "assistant_proposal",
        "proposal_id": p.proposal_id,
        "task_id": p.task_id or p.proposal_id,
        "target_role": p.params.get("target_role", ""),
        "collective_id": cid,
        "rationale": p.rationale,
        "ts": time.time(),
    }
    await signaling.publish(subject_task_assign(cid), payload)
    logger.info(
        "assistant_proposal: route dispatched — target=%r task_id=%r",
        payload["target_role"], payload["task_id"],
    )
    return True


# ---------------------------------------------------------------------------
# Publish helpers — the queue + the bus event
# ---------------------------------------------------------------------------


async def publish_proposal_pending(signaling, proposal: AssistantProposal) -> None:
    """Announce a new pending proposal on the bus.

    The Compliance screen's snapshot consumer picks this up to render
    the queue row; the policy-layer reward harness (SIP-P1) reads the
    matching `OVERSIGHT_DECISION` once the operator acts.
    """
    from acc.signals import subject_assistant_proposal  # noqa: PLC0415
    try:
        await signaling.publish(
            subject_assistant_proposal(proposal.collective_id),
            proposal.to_payload(),
        )
    except Exception:
        logger.exception(
            "assistant_proposal: failed to publish pending for %s",
            proposal.proposal_id,
        )


__all__ = [
    "PROPOSAL_SPAWN",
    "PROPOSAL_ROLE_UPDATE",
    "PROPOSAL_ROUTE",
    "PROPOSAL_INFUSE",
    "PROPOSAL_KINDS",
    "DEFAULT_RISK_LEVEL",
    "DISPATCH_PLAN",
    "DISPATCH_QUEUE",
    "DISPATCH_EXECUTE",
    "AssistantProposal",
    "decide_dispatch",
    "parse_proposal_markers",
    "dispatch_approved_proposal",
    "publish_proposal_pending",
]
