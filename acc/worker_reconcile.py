"""Worker-pool reconcile (PR-M, J-2) — match dormant workers to
desired roles from ``collective.yaml`` and emit signed ROLE_ASSIGN.

PR-J (D-001) shipped the agent-side primitive: a dormant worker
boots without a CognitiveCore and promotes itself on a valid signed
ROLE_ASSIGN.  PR-M closes the loop on the arbiter side: it diffs the
desired agentset (``collective.yaml``) against a roster of live
agents and produces the set of ROLE_ASSIGN payloads needed to reach
the desired state.

The reconcile is split into a **pure** decision function
(:func:`compute_assignments`) and a **signing** helper
(:func:`build_role_assign_payloads`).  The arbiter glue in
``acc.agent`` wires a roster (built from HEARTBEAT tracking) and the
arbiter's private key into these, then publishes the payloads.  The
pure split means the matching algorithm is unit-testable without a
NATS bus or cryptography keys.

Matching algorithm (greedy, deterministic):

1. Expand the spec's ``agents`` list into desired *slots* — one per
   replica.  ``AgentSpec(role="coding_agent", replicas=3)`` → three
   slots all wanting ``coding_agent``.
2. Subtract slots already satisfied by ACTIVE agents whose role
   (and cluster_id, when the slot specifies one) match.  An agent
   already running the right role counts against the desired count.
3. Assign the remaining slots to DORMANT workers, lowest agent_id
   first (deterministic), one slot per worker.
4. Slots with no free dormant worker are reported as ``unmet`` so
   the caller can surface "pool exhausted — bring up more dormant
   workers" to the operator.

Idempotent: running reconcile twice with the same roster + spec
produces no new assignments the second time (the now-promoted
workers count as ACTIVE on the next pass).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("acc.worker_reconcile")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterEntry:
    """One agent the arbiter currently knows about (from HEARTBEATs)."""

    agent_id: str
    role: str
    state: str  # "ACTIVE" | "DORMANT" | "REGISTERING" | "DRAINING"
    cluster_id: str = ""


@dataclass(frozen=True)
class Assignment:
    """One dormant worker → desired role mapping the reconcile picked."""

    target_agent_id: str
    role: str
    cluster_id: str = ""
    purpose: str = ""


@dataclass
class ReconcileResult:
    """Output of :func:`compute_assignments`."""

    assignments: list[Assignment] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    """Roles (one entry per unmet slot) that wanted a worker but
    found no free dormant one.  E.g. ``["coding_agent",
    "coding_agent"]`` means two coding_agent slots couldn't be
    filled."""

    already_satisfied: int = 0
    """Count of desired slots already covered by ACTIVE agents — no
    assignment needed.  Surfaced so the arbiter can log
    "3 desired, 2 already running, 1 assigned"."""


# ---------------------------------------------------------------------------
# Pure decision function
# ---------------------------------------------------------------------------


def compute_assignments(spec, roster: list[RosterEntry]) -> ReconcileResult:
    """Diff desired agentset (*spec*) against *roster*; return the
    ROLE_ASSIGN assignments needed to reach the desired state.

    Args:
        spec: An ``acc.collective.CollectiveSpec`` (duck-typed — only
            ``.agents`` is read, each with ``.role``, ``.replicas``,
            ``.cluster_id``, ``.purpose``).
        roster: Live agents the arbiter knows about.

    Returns:
        A :class:`ReconcileResult`.  ``assignments`` is the list to
        sign + publish; ``unmet`` flags pool exhaustion.
    """
    # 1. expand desired slots.
    #    Each slot is (role, cluster_id, purpose).
    desired_slots: list[tuple[str, str, str]] = []
    for agent in getattr(spec, "agents", []) or []:
        role = str(getattr(agent, "role", "") or "")
        if not role:
            continue
        replicas = int(getattr(agent, "replicas", 1) or 0)
        cluster_id = str(getattr(agent, "cluster_id", "") or "")
        purpose = str(getattr(agent, "purpose", "") or "")
        for _ in range(replicas):
            desired_slots.append((role, cluster_id, purpose))

    # 2. subtract slots already covered by ACTIVE agents.
    #    An ACTIVE agent matching (role, cluster_id) consumes one
    #    slot.  cluster_id "" in the slot matches any cluster.
    active = [r for r in roster if r.state == "ACTIVE"]
    consumed_active: set[int] = set()  # indices into `active`
    remaining_slots: list[tuple[str, str, str]] = []
    already = 0
    for (role, cluster_id, purpose) in desired_slots:
        match_idx = None
        for i, a in enumerate(active):
            if i in consumed_active:
                continue
            if a.role != role:
                continue
            if cluster_id and a.cluster_id != cluster_id:
                continue
            match_idx = i
            break
        if match_idx is not None:
            consumed_active.add(match_idx)
            already += 1
        else:
            remaining_slots.append((role, cluster_id, purpose))

    # 3. assign remaining slots to dormant workers, lowest id first.
    dormant = sorted(
        (r for r in roster if r.state == "DORMANT"),
        key=lambda r: r.agent_id,
    )
    assignments: list[Assignment] = []
    unmet: list[str] = []
    dormant_iter = iter(dormant)
    for (role, cluster_id, purpose) in remaining_slots:
        worker = next(dormant_iter, None)
        if worker is None:
            unmet.append(role)
            continue
        assignments.append(Assignment(
            target_agent_id=worker.agent_id,
            role=role,
            cluster_id=cluster_id,
            purpose=purpose,
        ))

    return ReconcileResult(
        assignments=assignments,
        unmet=unmet,
        already_satisfied=already,
    )


# ---------------------------------------------------------------------------
# Signing helper
# ---------------------------------------------------------------------------


def build_role_assign_payloads(
    assignments: list[Assignment],
    *,
    approver_id: str,
    private_key_b64: str,
    role_definition_for: "callable",
) -> list[dict]:
    """Sign each assignment into a ROLE_ASSIGN payload ready to publish.

    Args:
        assignments: Output of :func:`compute_assignments`.
        approver_id: The arbiter's agent_id.
        private_key_b64: Base64 Ed25519 private key the arbiter
            signs with.
        role_definition_for: Callable ``role_name -> dict`` that
            returns the ``RoleDefinitionConfig.model_dump()`` for a
            role (typically wraps ``RoleLoader``).  An assignment
            whose role can't be resolved (returns None / empty) is
            skipped with a WARNING — better to leave the worker
            dormant than to assign it an empty role.

    Returns:
        One signed payload dict per resolvable assignment.
    """
    from acc.role_assign import sign_role_assign  # noqa: PLC0415

    payloads: list[dict] = []
    for a in assignments:
        role_def = role_definition_for(a.role)
        if not role_def:
            logger.warning(
                "worker_reconcile: cannot resolve role %r for worker %s "
                "— skipping assignment",
                a.role, a.target_agent_id,
            )
            continue
        # Stamp the role name into the definition so the worker's
        # _promote_from_dormant can recover it.
        role_def = dict(role_def)
        role_def.setdefault("name", a.role)
        try:
            payloads.append(sign_role_assign(
                approver_id=approver_id,
                target_agent_id=a.target_agent_id,
                role_definition=role_def,
                cluster_id=a.cluster_id,
                purpose=a.purpose,
                private_key_b64=private_key_b64,
            ))
        except Exception:
            logger.exception(
                "worker_reconcile: failed to sign ROLE_ASSIGN for %s",
                a.target_agent_id,
            )
    return payloads
