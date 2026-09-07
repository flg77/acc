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
PROPOSAL_ROLE_GAP = "role_gap"  # Proposal 019 PR-OP4 — a finding, not a mutation
# 20260823-attributed-memory Phase 4 — let one memory note cross from the
# context it was distilled in into another.  Structurally a ROLE_UPDATE: it
# changes what agents will say next, so it is approved, never applied.
PROPOSAL_PUBLISH = "publish"

PROPOSAL_KINDS: frozenset[str] = frozenset({
    PROPOSAL_SPAWN, PROPOSAL_ROLE_UPDATE, PROPOSAL_ROUTE, PROPOSAL_INFUSE,
    PROPOSAL_ROLE_GAP, PROPOSAL_PUBLISH,
})


# Default risk classification per kind.  Operators / Cat-A evaluator
# can override per-proposal via the ``risk_level`` attribute; the
# defaults set the dispatch-to-queue baseline.
DEFAULT_RISK_LEVEL: dict[str, str] = {
    PROPOSAL_SPAWN: "MEDIUM",        # structural; non-reversible until terminate
    PROPOSAL_ROLE_UPDATE: "HIGH",    # changes role definition system-wide
    PROPOSAL_ROUTE: "LOW",           # reversible by next prompt
    PROPOSAL_INFUSE: "HIGH",         # filesystem state; reversible only by uninstall
    PROPOSAL_ROLE_GAP: "LOW",        # informational finding; no mutation on its own
    PROPOSAL_PUBLISH: "HIGH",        # moves information across a context boundary
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
        goal_text:    The originating operator request text (B4 / proposal
                      044 O1) — carried so an infuse-continuation re-trigger
                      can restate the goal to the Assistant without a fresh
                      prompt or a turn-memory lookup.
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
    goal_text: str = ""

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
            "collective_id", "agent_id", "task_id", "goal_text",
        ) if k in raw}
        return cls(**valid)


# ---------------------------------------------------------------------------
# Mode gating — what to do with a fresh proposal
# ---------------------------------------------------------------------------


# ACCEPT_EDITS auto-executes the kinds that put a specialist onto the
# operator's task: ROUTE (reversible by the next prompt), SPAWN (a worker
# promoted from the dormant pool) and INFUSE (a signed pack from the
# catalog).  ROLE_UPDATE stays queued -- it changes what a role MAY DO,
# which is the one structural mutation a specialist hand-off never needs.
# `20260902-assistant-autonomy-prompt-pane-approvals` Phase 1.1.
_ACCEPT_EDITS_AUTOEXEC: frozenset[str] = frozenset({
    PROPOSAL_ROUTE, PROPOSAL_SPAWN, PROPOSAL_INFUSE,
})


#: Kinds that always reach a human, whatever the operating mode.
#:
#: PROPOSAL_INFUSE used to be here (Stage 1.4, `7f49a9e`: "always Compliance
#: pane over AUTO may infuse autonomously", with a dev-mode escape).  That
#: decision predates the signing floor being enforced at install
#: (``acc.pkg.install_infuse``: ``allow_unsigned`` only in dev, prod strict).
#: `20260902-assistant-autonomy-prompt-pane-approvals` reverses it on
#: purpose: infusing a curated role is the Assistant's central function, the
#: trust anchor is the catalog's ``required_signer`` verified at install, and
#: a human click cannot make an unsigned pack signed.  A floor failure is
#: therefore REFUSED (``_dispatch_infuse`` returns False and publishes
#: ``proposal_dispatch_failed``), never queued for a human to override.
#:
#: PROPOSAL_PUBLISH has no escape hatch in any mode.  The point of a
#: publication is that someone read the note and decided it may cross -- an
#: auto-executing publication is not a faster version of that, it is the
#: absence of it.  There is a test asserting the absence, because the
#: dangerous version of this feature is the one that promotes helpfully.
#: Approver values that name nobody.  `"default"` is the AssistantProposal
#: field default; `"tui:anonymous"` is what the decision subscriber falls back
#: to when a surface sends no approver_id.
_ANONYMOUS_APPROVERS: frozenset[str] = frozenset({"default", "tui:anonymous", "unattributed"})


_NEVER_AUTOEXEC: frozenset[str] = frozenset({
    PROPOSAL_ROLE_GAP, PROPOSAL_PUBLISH,
})


def _operator_mode_env() -> str:
    """Security-floor mode (proposal 034) from the environment; 'prod' default."""
    import os  # noqa: PLC0415

    return (os.environ.get("ACC_OPERATOR_MODE") or "prod").strip().lower() or "prod"


def decide_dispatch(operating_mode: str, kind: str, *, operator_mode: str | None = None) -> str:
    """Return one of ``DISPATCH_PLAN`` / ``DISPATCH_QUEUE`` / ``DISPATCH_EXECUTE``.

    Pure function; safe for unit tests + the cognitive core's per-task
    decision tree.  Unknown ``kind`` defaults to QUEUE (safest: human
    in the loop before any mutation we don't recognise).

    Kinds in :data:`_NEVER_AUTOEXEC` (``PROPOSAL_ROLE_GAP``,
    ``PROPOSAL_PUBLISH``) always queue -- even in AUTO.  Everything else
    executes in AUTO; ACCEPT_EDITS executes :data:`_ACCEPT_EDITS_AUTOEXEC`;
    ASK_PERMISSIONS queues everything (asked in the Prompt pane).

    ``operator_mode`` is accepted for call-site compatibility and ignored:
    the dev/prod distinction now lives where it belongs, at the signing
    floor inside the install (``_infuse_allow_unsigned``), not in the
    dispatch decision.
    """
    del operator_mode  # see docstring
    mode = normalise(operating_mode or "")
    if kind not in PROPOSAL_KINDS:
        return DISPATCH_QUEUE
    if mode == MODE_PLAN:
        return DISPATCH_PLAN
    if kind in _NEVER_AUTOEXEC:
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
# INFUSE: first group captures @scope/name with an OPTIONAL @constraint
# (no colon, no whitespace — constraint ranges like ">=1.2 <2.0" are NOT
# supported in markers; use a caret/tilde/exact in the LLM's output).
#
# The constraint is optional on purpose.  Requiring it was the single
# biggest cause of dropped infusion markers: the retry directive shows
# the shape "@scope/pack@constraint", and models routinely emit
# "[PROPOSE_INFUSE:@acc/research-roles:reason]" — dropping the version
# they were never told a concrete value for.  The strict regex silently
# skipped those, the operator saw "I did not take a concrete action",
# and the intent was lost.  A missing constraint now means "any version"
# (_INFUSE_DEFAULT_CONSTRAINT) and the resolver picks the newest match —
# which is what an unversioned request means anyway.
_RE_INFUSE = re.compile(
    r"\[PROPOSE_INFUSE:(@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9_-]*(?:@[^\s:\]]+)?):([^\]]+)\]"
)

# "Any version" for a marker that omitted the constraint.  ">=0.0.0"
# rather than "*" because acc.pkg._semver has no wildcard atom; this
# form matches every release including pre-releases.
_INFUSE_DEFAULT_CONSTRAINT = ">=0.0.0"

# Placeholder tokens from the marker's own documentation.  A model that
# echoes the template verbatim ("@scope/pack", "@scope/name@constraint")
# produces a syntactically VALID spec for a pack that cannot exist, which
# would otherwise reach the oversight queue as a real install request.
# Reject at parse time so the operator gets "no marker" (and the retry
# path) rather than a bogus proposal to approve.
_INFUSE_PLACEHOLDER_NAMES = frozenset({
    "@scope/pack", "@scope/name", "@scope/role", "@acc/pack", "@acc/name",
})


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
            raw = spec.strip()
            # Constraint omitted (".../name" with no trailing "@x") —
            # treat as "any version".  Guard on the LAST "@": the scope
            # prefix always contributes one, so a bare "@scope/name" has
            # exactly one and no "@" after the "/".
            if "@" not in raw.split("/", 1)[-1]:
                raw = f"{raw}@{_INFUSE_DEFAULT_CONSTRAINT}"
            name, constraint = parse_required_package(raw)
            if name.lower() in _INFUSE_PLACEHOLDER_NAMES:
                logger.warning(
                    "assistant_proposal: PROPOSE_INFUSE echoed the literal "
                    "placeholder %r - skipping (not a real pack)", name,
                )
                continue
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
    # Proposal 019 PR-OP4 — ROLE_GAP findings.  Delegate the marker parse
    # to acc.assistant.gap_analysis (which owns the balanced-brace JSON
    # extraction) and wrap each finding as a role_gap proposal so it
    # flows through the same queue → Compliance surface as the others.
    # A finding is informational: _NEVER_AUTOEXEC keeps it queued for
    # operator acknowledgement; its dispatch is an audit no-op (authoring
    # the actual role/extension is a separate, human-driven step).
    try:
        from acc.assistant.gap_analysis import (  # noqa: PLC0415
            parse_role_gap_markers,
        )
        for finding in parse_role_gap_markers(text):
            kind_label = finding.gap_kind.replace("_", " ")
            best = finding.best_match_role or "no match"
            out.append(AssistantProposal(
                kind=PROPOSAL_ROLE_GAP,
                params=finding.to_dict(),
                summary=(
                    f"Role gap ({kind_label}): best match {best} "
                    f"@ {finding.best_match_confidence:.2f}"
                ),
                rationale=finding.goal_summary,
            ))
    except Exception:  # noqa: BLE001
        logger.warning("assistant_proposal: ROLE_GAP parse failed", exc_info=True)
    return out


# ---------------------------------------------------------------------------
# Dispatch — publish the underlying mutation on approval / auto-execute
# ---------------------------------------------------------------------------


async def dispatch_approved_proposal(
    signaling,
    proposal: AssistantProposal,
    redis_client: Any = None,
    *,
    approver_tier: str = "",
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
        if proposal.kind == PROPOSAL_ROLE_GAP:
            return await _dispatch_role_gap(signaling, cid, proposal)
        if proposal.kind == PROPOSAL_PUBLISH:
            return await _dispatch_publish(
                signaling, cid, proposal, redis_client, approver_tier=approver_tier,
            )
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


class QuorumNotMet(Exception):
    """A note rests on too few people to be proposed for publication."""


def build_publish_proposal(
    note: Any,
    destination_scope: str,
    *,
    collective_id: str = "",
    agent_id: str = "",
    k: int | None = None,
    override_by: str = "",
) -> "AssistantProposal":
    """A proposal to let one note cross from its context into another.

    The summary names **both** contexts and the number of distinct people
    behind the note, because that is what an approver has to weigh: an approver
    who cannot see where a note came from, or how many people it represents,
    is clicking on prose.

    A single-source note is marked as such.  Not to block it -- the most
    valuable lessons are often exactly one person's ("the production database
    is being migrated Thursday") -- but so anyone reading it later knows it is
    one person's account.  The marking is the point; the permission is not the
    interesting half.
    """
    from acc.attribution import people_in  # noqa: PLC0415
    from acc.memory_reflection import QUORUM_DEFAULT  # noqa: PLC0415

    requesters = [str(r) for r in (getattr(note, "source_requesters", None) or [])]
    # PEOPLE, not requester strings: the same human in two rooms renders as two
    # requesters, and counting those separately is how a quorum of two gets
    # satisfied by one person talking to themselves.
    people = people_in(requesters)
    floor = QUORUM_DEFAULT if k is None else max(1, int(k))
    if len(people) < floor and not override_by:
        raise QuorumNotMet(
            f"{len(people)} distinct person(s) behind this note; {floor} required. "
            "An operator may override, and the note is then marked single-source.",
        )

    source_scope = str(getattr(note, "scope", "") or "")
    summary_text = str(getattr(note, "summary", "") or "")
    dissent = str(getattr(note, "dissent", "") or "")
    marker = " [single source]" if len(people) < 2 else ""
    if override_by:
        marker += f" [quorum overridden by {override_by}]"
    return AssistantProposal(
        kind=PROPOSAL_PUBLISH,
        params={
            "note_id": str(getattr(note, "note_id", "") or ""),
            "summary": summary_text,
            "role_label": str(getattr(note, "role_label", "") or ""),
            "source_scope": source_scope,
            # The information rule travels with the proposal: the destination
            # copy carries the note's ceiling, whoever approved it.
            "ceiling": str(getattr(note, "ceiling", "") or "CRITICAL"),
            "source_ids": [str(i) for i in (getattr(note, "source_ids", None) or [])],
            "source_requesters": requesters,
            "source_people": people,
            "destination_scope": destination_scope,
            "single_source": len(people) < 2,
            "quorum_override_by": override_by,
            "dissent": dissent,
        },
        summary=(
            f"Publish a lesson from {source_scope or 'an unnamed context'} "
            f"into {destination_scope} "
            f"({len(people)} distinct person(s)){marker}"
        ),
        rationale=summary_text,
        collective_id=collective_id,
        agent_id=agent_id,
    )


async def _dispatch_publish(
    signaling, cid: str, p: AssistantProposal, redis_client: Any = None,
    approver_tier: str = "",
) -> bool:
    """Make an approved note readable in the destination a human named.

    Directed, not broadcast: the note lands in exactly the context the approval
    named. That is what makes "may this fragment be retrieved here?" answerable
    without per-principal ceilings -- the approval record answers it.

    An approval with no destination is refused rather than defaulted. There is
    no sensible default for *where information may go*, and picking one would
    be the whole control lost to a convenience.
    """
    # The whole strength of this control is that a PERSON read the note and
    # decided it may cross.  "tui:anonymous" is the fallback when the decision
    # payload carried no approver, and accepting it would mean recording an
    # approval nobody can be held to -- which is the same as no approval.
    # Refused rather than warned: publication is new, so nothing depends on the
    # permissive behaviour, and a control that fails open on its first day
    # never gets tightened.
    approver = str(p.operator_id or "").strip()
    if not approver or approver in _ANONYMOUS_APPROVERS:
        logger.warning(
            "assistant_proposal: publish %s has no named approver (%r) — "
            "refusing; the surface that approved it must send approver_id",
            p.proposal_id, approver,
        )
        return False

    params = p.params or {}
    destination = str(params.get("destination_scope") or "").strip()
    summary = str(params.get("summary") or "").strip()
    role_label = str(params.get("role_label") or "").strip()
    if not destination or not summary or not role_label:
        logger.warning(
            "assistant_proposal: publish %s missing destination/summary/role "
            "— refusing", p.proposal_id,
        )
        return False

    from acc.memory_reflection import parse_destination, publish_note  # noqa: PLC0415
    # `20260906-enterprise-brain-hub-scope`: ``hub:<cid>`` lands the note in
    # the hub's enterprise tier under the hub's collective id -- the one
    # place every instance bound to that hub reads.  Anything else stays a
    # scope inside this collective, as before.
    dest_cid, dest_scope = parse_destination(destination)
    # Phase 2: a promotion INTO a hub needs an operator-tier approver
    # (HG-40.1 §2.5).  Fail closed: a decision from a surface that sends no
    # tier cannot promote into a hub; an ordinary scope keeps working as
    # before.  Journalled either way so the refusal is visible.
    if dest_cid and str(approver_tier or "").lower() != "operator":
        logger.warning(
            "assistant_proposal: publish %s into hub %s refused — approver %r is "
            "%s, operator tier required", p.proposal_id, dest_cid, approver,
            f"tier {approver_tier!r}" if approver_tier else "of unknown tier",
        )
        from acc.signals import subject_assistant_proposal as _sap  # noqa: PLC0415
        try:
            await signaling.publish(_sap(cid), {
                "trigger": "note_publish_refused", "proposal_id": p.proposal_id,
                "destination_scope": destination, "destination_collective": dest_cid,
                "approved_by": approver, "approver_tier": approver_tier or "",
                "reason": "operator tier required for a hub promotion", "ts": time.time(),
            })
        except Exception:  # noqa: BLE001
            logger.debug("assistant_proposal: refusal ack failed", exc_info=True)
        return False
    ok = publish_note(
        redis_client, dest_cid or cid, role_label, summary, dest_scope,
        dissent=str(params.get("dissent") or ""),
        ceiling=str(params.get("ceiling") or "CRITICAL"),
        source_requesters=[str(r) for r in (params.get("source_requesters") or [])],
        note_id=str(params.get("note_id") or ""),
    )

    from acc.signals import subject_assistant_proposal  # noqa: PLC0415
    try:
        # Journalled even when the write failed: "a human approved moving this
        # between contexts" is the fact worth keeping, and it is worth keeping
        # whether or not the cache took it.
        await signaling.publish(subject_assistant_proposal(cid), {
            "trigger": "note_published",
            "proposal_id": p.proposal_id,
            "note_id": params.get("note_id", ""),
            "source_scope": params.get("source_scope", ""),
            "destination_scope": destination,
            "destination_collective": dest_cid or cid,
            "ceiling": params.get("ceiling", ""),
            "source_requesters": params.get("source_requesters", []),
            "approved_by": p.operator_id,
            "written": ok,
            "ts": time.time(),
        })
    except Exception:  # noqa: BLE001
        logger.exception(
            "assistant_proposal: publish ack failed for %s", p.proposal_id,
        )
    return ok


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


async def _dispatch_role_gap(signaling, cid: str, p: AssistantProposal) -> bool:
    """Proposal 019 PR-OP4 — acknowledge an approved role-gap finding.

    A role-gap finding is informational: there is no mutation to apply
    on its own.  Operator "approval" means *the gap is acknowledged* —
    authoring the new role / extension is a separate, human-driven step
    (it goes through the normal role-package publish flow), deliberately
    out of scope here.  We publish a thin acknowledgement on the bus so
    a future autonomous-loop perceptor (proposal 016 Step 5) can
    aggregate acknowledged gaps, then return True.
    """
    from acc.signals import subject_assistant_proposal  # noqa: PLC0415
    payload = {
        "trigger": "role_gap_acknowledged",
        "proposal_id": p.proposal_id,
        "gap_kind": (p.params or {}).get("gap_kind", ""),
        "goal_id": (p.params or {}).get("goal_id", ""),
        "ts": time.time(),
    }
    try:
        await signaling.publish(subject_assistant_proposal(cid), payload)
    except Exception:  # noqa: BLE001
        logger.exception(
            "assistant_proposal: role_gap ack publish failed for %s",
            p.proposal_id,
        )
        return False
    logger.info(
        "assistant_proposal: role_gap acknowledged id=%s kind=%s",
        p.proposal_id, payload["gap_kind"],
    )
    return True


def _infuse_allow_unsigned() -> bool:
    """033 WS-G — ``operator_mode == "dev"`` relaxes the signing floor at
    infuse-install time (consistent with proposal 034); prod stays strict.

    Read from ``ACC_OPERATOR_MODE`` (the env var ``acc.config`` maps to
    ``operator_mode``) so the dispatch path stays hermetic — no disk read,
    no cwd coupling.  Unset / anything but ``dev`` => strict (False).
    """
    import os  # noqa: PLC0415
    return os.environ.get("ACC_OPERATOR_MODE", "").strip().lower() == "dev"


async def _dispatch_infuse(signaling, cid: str, p: AssistantProposal) -> bool:
    """Stage 1.4 / 033 WS-G Part 3 — actually install the requested package.

    Reconstructs ``@scope/name@constraint`` from ``params["name"]`` +
    ``params["constraint"]`` and hands off to
    :func:`acc.pkg.install_infuse.execute_infuse_install`, which resolves
    the spec against the layered catalog resolver and calls the installer
    (catalog walk + download + cosign verify + install).  This is the
    EXECUTE-path wiring that makes an approved/auto-executed infuse
    ACTUALLY install.  Idempotent: a re-approve of an already-installed
    pkg is a no-op (``already_satisfied``).

    On success we publish a thin notification on the bus so the
    Marketplace / Capability surfaces can refresh; on failure we log the
    cosign / EC / dep / resolve error and return False (caller may retry
    or surface the error in the Compliance pane).
    """
    from acc.pkg.install_infuse import execute_infuse_install  # noqa: PLC0415
    from acc.signals import subject_assistant_proposal  # noqa: PLC0415

    name = p.params.get("name", "").strip()
    constraint = p.params.get("constraint", ">=0.0.0").strip() or ">=0.0.0"
    if not name:
        logger.warning(
            "assistant_proposal: infuse proposal %s has no name",
            p.proposal_id,
        )
        return False

    spec = f"{name}@{constraint}"
    result = execute_infuse_install(
        spec, allow_unsigned=_infuse_allow_unsigned(),
    )
    if not result.ok:
        # Signing-floor / resolve / dep failure.  REFUSED, not queued: an
        # auto-executed infuse has no oversight row, and a human approval
        # could not make an unsigned pack signed anyway.  The notice is what
        # the Prompt pane / Compliance render so the refusal is visible.
        logger.warning(
            "assistant_proposal: infuse %s failed: %s", spec, result.error,
        )
        try:
            await signaling.publish(subject_assistant_proposal(cid), {
                "signal_type": "ASSISTANT_PROPOSAL_OUTCOME",
                "trigger": "proposal_dispatch_failed",
                "proposal_id": p.proposal_id,
                "task_id": p.task_id,
                "kind": PROPOSAL_INFUSE,
                "spec": spec,
                "reason": result.error,
                "ts": time.time(),
            })
        except Exception:  # noqa: BLE001 -- notice is best-effort
            logger.exception(
                "assistant_proposal: infuse-failed notice publish failed for %s",
                p.proposal_id,
            )
        return False

    payload = {
        "signal_type": "ASSISTANT_PROPOSAL_OUTCOME",
        "trigger": "infuse_completed",
        "proposal_id": p.proposal_id,
        "task_id": p.task_id,
        "name": result.name,
        "version": result.version,
        "install_path": result.install_path,
        "was_already_installed": result.already_satisfied,
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
    # B4 (proposal 044 O1) — finish the loop: on a GENUINE first install,
    # re-trigger the Assistant so it CONTINUES the original goal (spawn the
    # new role → route the task → summarise) without a fresh operator prompt.
    # Gated on ``not already_satisfied`` so a re-approve / idempotent re-run of
    # an already-installed pack can NEVER loop back into another continuation.
    if not result.already_satisfied:
        await _publish_infuse_continuation(signaling, cid, p, result)
    return True


async def _publish_infuse_continuation(signaling, cid: str, p: AssistantProposal, result) -> None:
    """B4 (proposal 044 O1) — re-trigger the Assistant after a successful infuse.

    Publishes a synthetic ``TASK_ASSIGN`` targeted at the Assistant (which
    ``is_wake_trigger`` wakes on ``target_role == "assistant"``), carrying the
    original ``task_id`` + ``goal_text`` so it resumes the goal with context.
    Runs the continuation under AUTO — the operator already confirmed the
    infuse (the GATE CARD), so 044's "confirm-once-then-drive" says the
    follow-on spawn/route should auto-execute.  Tagged ``trigger`` /
    ``_continuation_of`` so it's never mistaken for a fresh operator task; the
    prompt explicitly forbids re-infusing, and the ``already_satisfied`` gate in
    the caller is the hard loop-breaker.  Best-effort: a bus failure logs and
    leaves the install done (the operator can still drive manually)."""
    from acc.signals import SIG_TASK_ASSIGN, subject_task_assign  # noqa: PLC0415

    pkg = f"{result.name}@{result.version}"
    prompt = (
        f"[ACC CONTINUATION] The role pack {pkg} you proposed is now INSTALLED. "
        f"Do NOT propose infusing it again. Continue the original goal to a real "
        f"outcome: bring the needed role online with "
        f"[PROPOSE_SPAWN:<role>:<cluster>:why], then hand the task over with "
        f"[PROPOSE_ROUTE:<role>:why]; when the specialist replies, summarise the "
        f"result for the user and propose the highest-value follow-ups."
    )
    goal = (getattr(p, "goal_text", "") or "").strip()
    if goal:
        prompt += f"\n\nOriginal goal: {goal}"
    payload = {
        "signal_type": SIG_TASK_ASSIGN,
        "trigger": "infuse_continuation",
        "target_role": "assistant",
        "task_id": p.task_id or p.proposal_id,
        "operator_id": p.operator_id,
        "collective_id": cid,
        "content": prompt,
        # confirm-once-then-drive: the operator already approved the infuse, so
        # the follow-on spawn/route auto-executes.
        "operating_mode": "AUTO",
        "_continuation_of": p.proposal_id,
        "_infuse_completed": {"name": result.name, "version": result.version},
        "ts": time.time(),
    }
    try:
        await signaling.publish(subject_task_assign(cid), payload)
        logger.info(
            "assistant_proposal: infuse-continuation → assistant "
            "(goal task_id=%s, pkg=%s)", payload["task_id"], pkg,
        )
    except Exception:  # noqa: BLE001 — install already succeeded; drive manually
        logger.exception(
            "assistant_proposal: infuse-continuation publish failed for %s",
            p.proposal_id,
        )


async def _dispatch_route(signaling, cid: str, p: AssistantProposal) -> bool:
    """Re-publish the originating TASK_ASSIGN targeting the new role.

    Phase 2a publishes a thin re-dispatch marker; Phase 2b in the
    cognitive core threads the original task_payload so the receiver
    gets the full prompt.  Today the receiver re-reads from the task
    record (Redis is the source of truth).
    """
    from acc.signals import SIG_TASK_ASSIGN, subject_task_assign  # noqa: PLC0415
    target_role = p.params.get("target_role", "")
    payload = {
        "signal_type": SIG_TASK_ASSIGN,
        "trigger": "assistant_proposal",
        "proposal_id": p.proposal_id,
        "task_id": p.task_id or p.proposal_id,
        "target_role": target_role,
        "collective_id": cid,
        "rationale": p.rationale,
        # N4 — mark this as a HANDOVER so the receiving role knows it was
        # activated by an assistant handover (not ordinary work dispatch) and
        # can correlate its result back to this proposal.  The 25.6.26 finding
        # was that researcher roles were listed but no handover signal ever
        # activated them; this makes the activation explicit + traceable.
        "handover": True,
        "handover_id": p.proposal_id,
        "handover_announcement": handover_announcement(
            target_role, p.rationale, p.proposal_id
        ),
        "ts": time.time(),
    }
    await signaling.publish(subject_task_assign(cid), payload)
    logger.info(
        "assistant_proposal: HANDOVER dispatched — %s",
        payload["handover_announcement"],
    )
    return True


def handover_announcement(
    target_role: str, rationale: str, handover_id: str = ""
) -> str:
    """N4 — a human-readable announcement of an assistant handover.

    Surfaces the handover as a reasoned, visible event ("→ handing this to
    <role>: <reason>") instead of a silent re-dispatch — the 25.6.26 finding
    that researcher roles were listed but no handover signal ever raised, so
    the activated role never visibly joined.  Pure → unit-testable; carried in
    the dispatch payload so the Prompt/Compliance surfaces can render it.
    """
    role = (target_role or "?").strip() or "?"
    reason = (rationale or "").strip() or "specialist better suited to this task"
    tag = f" [{handover_id[:8]}]" if handover_id else ""
    return f"→ Handing this to '{role}'{tag}: {reason}"


# ---------------------------------------------------------------------------
# Publish helpers — the queue + the bus event
# ---------------------------------------------------------------------------


async def publish_proposal_pending(signaling, proposal: AssistantProposal) -> None:
    """Announce a new pending proposal on the bus.

    The Compliance screen's snapshot consumer picks this up to render
    the queue row; the policy-layer reward harness (SIP-P1) reads the
    matching `OVERSIGHT_DECISION` once the operator acts.
    """
    from acc.signals import (  # noqa: PLC0415
        SIG_ASSISTANT_PROPOSAL,
        subject_assistant_proposal,
    )
    try:
        # signal_type is what the TUI observer routes on (1.4: the Prompt
        # pane joins this payload -- rationale, goal_text, task_id -- to the
        # heartbeat's pending row).  from_payload() drops unknown keys.
        await signaling.publish(
            subject_assistant_proposal(proposal.collective_id),
            {**proposal.to_payload(), "signal_type": SIG_ASSISTANT_PROPOSAL},
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
    "PROPOSAL_PUBLISH",
    "build_publish_proposal",
    "QuorumNotMet",
    "publish_proposal_pending",
]
