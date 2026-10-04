"""Container lifecycle for specialist pool workers (pure core).

`20261003-assistant-orchestrated-infusion` Phase 2.  The operator decided the
assistant may start, stop and pause containers to infuse roles.  The assistant
*decides*; it never executes.  The path is::

    PROPOSE_LIFECYCLE ─▶ proposal pipeline (risk, operating mode, console)
        ─▶ lifecycle.intent ─▶ arbiter validates + signs ─▶ lifecycle.request
        ─▶ broker (acc.lifecycle_broker) verifies, plans, executes, reports

This module holds everything that decides, so it can be tested without podman
or NATS: the request wire shape, its Ed25519 signature (the arbiter's key, the
same family as ``ROLE_ASSIGN``), and the broker's policy -- which containers may
be touched at all, what each action does to them, and when to refuse.

The vocabulary is closed.  A request names an action and a role; it cannot name
an image, a mount, an env var, a port or a privilege, because those fields do
not exist.  ``scale`` starts a pool worker that was created ahead of time (the
pool's size is the cap); it never creates a container, so a request cannot
introduce a container shape the operator did not deploy.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

ACTION_SCALE = "scale"    # start one pre-created, stopped pool worker
ACTION_STOP = "stop"      # stop the idle worker(s) holding a role
ACTION_PAUSE = "pause"    # freeze the idle worker(s) holding a role
ACTION_RESUME = "resume"  # unfreeze the paused worker(s) that held a role

ACTIONS: frozenset[str] = frozenset({ACTION_SCALE, ACTION_STOP, ACTION_PAUSE, ACTION_RESUME})

#: Words the assistant may use for an action.  "start" is how people say it.
ACTION_ALIASES: dict[str, str] = {"start": ACTION_SCALE}

# Refusal reasons -- published on the outcome so the operator sees why.
REASON_UNKNOWN_ACTION = "unknown_action"
REASON_NO_SIGNING_KEY = "no_signing_key"
REASON_UNKNOWN_ROLE = "unknown_role"
REASON_BAD_SIGNATURE = "bad_signature"
REASON_STALE = "stale_request"
REASON_REPLAY = "replayed_request"
REASON_WRONG_COLLECTIVE = "wrong_collective"
REASON_RATE_LIMITED = "rate_limited"
REASON_POOL_EXHAUSTED = "pool_exhausted"
REASON_NO_SUCH_WORKER = "no_worker_holds_role"
REASON_WORKER_BUSY = "worker_busy"
REASON_EXECUTION_FAILED = "execution_failed"

#: A request older than this is refused: a signed request is a capability, and
#: one that can be replayed tomorrow is a standing one.
MAX_REQUEST_AGE_S = 120.0

#: Container names the broker never touches, whatever a request says or a label
#: claims.  Defence in depth behind the ``acc.worker_pool`` label check: the
#: control plane is not a pool, and a mislabelled control-plane container must
#: still be refused.
PROTECTED_NAMES: frozenset[str] = frozenset({
    "acc-agent-arbiter", "acc-agent-assistant", "acc-agent-compliance-officer",
    "acc-agent-ingester", "acc-agent-analyst", "acc-agent-coding-agent",
    "acc-nats", "acc-redis", "acc-tui", "acc-webgui", "acc-lifecycle-broker",
})

#: Only containers with this name prefix are pool workers (``roles_to_compose``
#: names them ``acc-worker-<n>``).
WORKER_NAME_PREFIX = "acc-worker-"


def normalise_action(raw: str) -> str:
    """Canonical action for *raw*, or ``""`` when it is not in the vocabulary."""
    a = str(raw or "").strip().lower()
    a = ACTION_ALIASES.get(a, a)
    return a if a in ACTIONS else ""


class LifecycleRefused(Exception):
    """The request was refused.  ``reason`` is one of the ``REASON_*`` codes."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# Signed request (arbiter signs, broker verifies)
# ---------------------------------------------------------------------------

_SIGNED_FIELDS = (
    "request_id", "collective_id", "action", "role", "cluster_id",
    "proposal_id", "task_id", "issued_at", "approver_id",
)


def _canonical(fields: dict) -> bytes:
    return json.dumps(
        {k: fields.get(k, "") for k in _SIGNED_FIELDS},
        sort_keys=True, separators=(",", ":"),
    ).encode()


def sign_request(
    *,
    collective_id: str,
    action: str,
    role: str,
    approver_id: str,
    private_key_b64: str,
    cluster_id: str = "",
    proposal_id: str = "",
    task_id: str = "",
    now: float | None = None,
    request_id: str = "",
) -> dict:
    """A signed ``LIFECYCLE_REQUEST`` payload, ready to publish.

    Raises :class:`LifecycleRefused` for an action outside the vocabulary or an
    unusable key, so the arbiter can report the refusal instead of publishing.
    """
    canonical_action = normalise_action(action)
    if not canonical_action:
        raise LifecycleRefused(REASON_UNKNOWN_ACTION, repr(action))
    if not private_key_b64:
        raise LifecycleRefused(REASON_NO_SIGNING_KEY)
    try:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    except Exception as exc:  # noqa: BLE001
        raise LifecycleRefused(REASON_NO_SIGNING_KEY, f"invalid key: {exc}") from exc
    fields = {
        "request_id": request_id or uuid.uuid4().hex,
        "collective_id": collective_id,
        "action": canonical_action,
        "role": str(role or "").strip(),
        "cluster_id": str(cluster_id or "").strip(),
        "proposal_id": proposal_id,
        "task_id": task_id,
        "issued_at": float(time.time() if now is None else now),
        "approver_id": approver_id,
    }
    return {
        "signal_type": "LIFECYCLE_REQUEST",
        **fields,
        "signature": base64.b64encode(key.sign(_canonical(fields))).decode("ascii"),
    }


def verify_request(
    payload: dict,
    *,
    verify_key_b64: str,
    collective_id: str,
    now: float | None = None,
    max_age_s: float = MAX_REQUEST_AGE_S,
) -> dict:
    """Return the verified fields of *payload*, or raise :class:`LifecycleRefused`.

    Checks, in order: shape, collective, signature, age.  Replay is the
    broker's job (:class:`ReplayGuard`), because it needs memory.
    """
    if not isinstance(payload, dict):
        raise LifecycleRefused(REASON_BAD_SIGNATURE, "payload is not an object")
    if normalise_action(payload.get("action", "")) != payload.get("action"):
        raise LifecycleRefused(REASON_UNKNOWN_ACTION, repr(payload.get("action")))
    if payload.get("collective_id") != collective_id:
        raise LifecycleRefused(REASON_WRONG_COLLECTIVE, str(payload.get("collective_id")))
    if not verify_key_b64:
        raise LifecycleRefused(REASON_BAD_SIGNATURE, "broker has no ACC_ARBITER_VERIFY_KEY")
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(verify_key_b64))
        public.verify(base64.b64decode(str(payload.get("signature", ""))), _canonical(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise LifecycleRefused(REASON_BAD_SIGNATURE, "signature does not match") from exc
    current = time.time() if now is None else now
    issued = float(payload.get("issued_at") or 0.0)
    if current - issued > max_age_s or issued - current > 30.0:
        raise LifecycleRefused(REASON_STALE, f"issued {current - issued:.0f}s ago")
    return {k: payload.get(k) for k in _SIGNED_FIELDS}


class ReplayGuard:
    """Refuses a request id seen before (within the request max age)."""

    def __init__(self, max_age_s: float = MAX_REQUEST_AGE_S) -> None:
        self._max_age_s = max_age_s
        self._seen: dict[str, float] = {}

    def check(self, request_id: str, now: float) -> None:
        self._seen = {k: t for k, t in self._seen.items() if now - t <= 2 * self._max_age_s}
        if not request_id or request_id in self._seen:
            raise LifecycleRefused(REASON_REPLAY, request_id or "no request_id")
        self._seen[request_id] = now


class RateLimiter:
    """At most ``max_actions`` executed requests per ``window_s`` (sliding)."""

    def __init__(self, max_actions: int = 6, window_s: float = 600.0) -> None:
        self.max_actions = max_actions
        self.window_s = window_s
        self._times: deque[float] = deque()

    def check(self, now: float) -> None:
        while self._times and now - self._times[0] > self.window_s:
            self._times.popleft()
        if len(self._times) >= self.max_actions:
            raise LifecycleRefused(
                REASON_RATE_LIMITED,
                f"{self.max_actions} actions in {self.window_s:.0f}s",
            )

    def record(self, now: float) -> None:
        self._times.append(now)


# ---------------------------------------------------------------------------
# Policy: what a verified request does to which containers
# ---------------------------------------------------------------------------

#: podman states a stopped-but-startable worker can be in.
_STARTABLE = frozenset({"exited", "created", "stopped", "configured"})


@dataclass(frozen=True)
class Container:
    """What the broker knows about one container (from the podman API)."""

    name: str
    state: str
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def agent_id(self) -> str:
        """``acc-worker-3`` runs agent ``worker-3`` (``roles_to_compose``)."""
        return self.name[len("acc-"):] if self.name.startswith("acc-") else self.name


@dataclass(frozen=True)
class WorkerView:
    """What the last heartbeat said about one agent."""

    agent_id: str
    role: str
    tasks_in_flight: int = 0


@dataclass(frozen=True)
class Op:
    """One podman operation: ``start`` | ``stop`` | ``pause`` | ``unpause``."""

    op: str
    container: str


@dataclass(frozen=True)
class Plan:
    ops: list[Op]
    skipped_busy: list[str] = field(default_factory=list)


def eligible(containers: Iterable[Container], collective_id: str) -> list[Container]:
    """The pool workers of *collective_id* the broker may touch at all."""
    out = []
    for c in containers:
        if c.name in PROTECTED_NAMES or not c.name.startswith(WORKER_NAME_PREFIX):
            continue
        if c.labels.get("acc.worker_pool") != "true":
            continue
        if c.labels.get("acc.collective_id") != collective_id:
            continue
        out.append(c)
    return sorted(out, key=lambda c: c.name)


def plan(
    request: dict,
    containers: Iterable[Container],
    workers: dict[str, WorkerView],
    *,
    collective_id: str,
) -> Plan:
    """The operations a verified *request* performs, or :class:`LifecycleRefused`."""
    action = request.get("action", "")
    role = str(request.get("role") or "")
    pool = eligible(containers, collective_id)

    if action == ACTION_SCALE:
        for c in pool:
            if c.state in _STARTABLE:
                return Plan(ops=[Op("start", c.name)])
        raise LifecycleRefused(
            REASON_POOL_EXHAUSTED,
            f"all {len(pool)} pool worker(s) already running",
        )

    if action not in (ACTION_STOP, ACTION_PAUSE, ACTION_RESUME):
        raise LifecycleRefused(REASON_UNKNOWN_ACTION, repr(action))

    holders = [c for c in pool if (w := workers.get(c.agent_id)) and w.role == role]
    if action == ACTION_RESUME:
        ops = [Op("unpause", c.name) for c in holders if c.state == "paused"]
        if not ops:
            raise LifecycleRefused(REASON_NO_SUCH_WORKER, f"no paused worker held {role!r}")
        return Plan(ops=ops)

    wanted = {"running"} if action == ACTION_PAUSE else {"running", "paused"}
    candidates = [c for c in holders if c.state in wanted]
    if not candidates:
        raise LifecycleRefused(REASON_NO_SUCH_WORKER, f"no running worker holds {role!r}")
    idle = [c for c in candidates if workers[c.agent_id].tasks_in_flight <= 0]
    busy = [c.name for c in candidates if c not in idle]
    if not idle:
        raise LifecycleRefused(REASON_WORKER_BUSY, ", ".join(busy))
    verb = "pause" if action == ACTION_PAUSE else "stop"
    return Plan(ops=[Op(verb, c.name) for c in idle], skipped_busy=busy)


# ---------------------------------------------------------------------------
# The outcome every step publishes (arbiter refusals, broker results)
# ---------------------------------------------------------------------------


def lifecycle_result_payload(
    request: dict,
    *,
    ok: bool,
    reason: str = "",
    detail: str = "",
    ops: Iterable[Op] = (),
    skipped_busy: Iterable[str] = (),
    by: str = "",
) -> dict:
    """``ASSISTANT_PROPOSAL_OUTCOME`` with ``trigger: lifecycle_result``.

    Rides the subject the Prompt pane and the work board already render, and
    carries ``task_id`` so it lands on the prompt that asked.
    """
    return {
        "signal_type": "ASSISTANT_PROPOSAL_OUTCOME",
        "trigger": "lifecycle_result",
        "ok": bool(ok),
        "action": str(request.get("action") or ""),
        "role": str(request.get("role") or ""),
        "proposal_id": str(request.get("proposal_id") or ""),
        "task_id": str(request.get("task_id") or ""),
        "request_id": str(request.get("request_id") or ""),
        "reason": reason,
        "detail": detail,
        "ops": [{"op": o.op, "container": o.container} for o in ops],
        "skipped_busy": list(skipped_busy),
        "by": by,
        "ts": time.time(),
    }


async def publish_lifecycle_result(signaling, collective_id: str, request: dict, **kw) -> None:
    """Best-effort publish of :func:`lifecycle_result_payload`."""
    from acc.signals import subject_assistant_proposal  # noqa: PLC0415

    try:
        await signaling.publish(
            subject_assistant_proposal(collective_id),
            lifecycle_result_payload(request, **kw),
        )
    except Exception:  # noqa: BLE001 -- an outcome notice must not raise
        import logging  # noqa: PLC0415

        logging.getLogger("acc.lifecycle").exception("lifecycle: result publish failed")


# ---------------------------------------------------------------------------
# Cluster: the same vocabulary, as replica changes on the AgentCollective
# ---------------------------------------------------------------------------
#
# `20261003-assistant-orchestrated-infusion` Phase 6.  On a cluster there is no
# dormant pool to start: each role in ``spec.agents`` is a Deployment and the
# ACC operator reconciles its ``replicas``.  The broker's Kubernetes runtime
# therefore patches ONE field family -- ``spec.agents[i].replicas`` of its own
# AgentCollective -- plus a paused-replicas annotation, and nothing else:
#
#   scale  -> replicas + 1 (a role already declared; adding a role stays a
#             human edit, the cluster counterpart of "never create a container")
#   stop   -> replicas 0         (refused while any pod of the role is busy:
#                                 a scale-down cannot choose which pod goes)
#   pause  -> replicas 0, the old count kept in the annotation
#   resume -> the count from the annotation
#
# Control-plane roles are never patched, whatever the request says.

REASON_PROTECTED_ROLE = "protected_role"
REASON_ROLE_NOT_DECLARED = "role_not_declared"
REASON_AUTOSCALED = "autoscaled"

#: Roles the cluster broker never scales or stops.
CONTROL_ROLES: frozenset[str] = frozenset({"arbiter", "assistant", "compliance_officer"})

#: The annotation that remembers a paused role's replica count.
PAUSED_ANNOTATION = "acc.redhat.io/paused-replicas"

DEFAULT_MAX_REPLICAS = 3


@dataclass(frozen=True)
class ClusterPlan:
    patch: list[dict]          # RFC 6902 JSON Patch for the AgentCollective
    ops: list[Op]              # what to report: Op(<verb>, "<role> replicas a->b")


def plan_cluster(
    request: dict,
    collective: dict,
    workers: dict[str, WorkerView],
    *,
    max_replicas: int = DEFAULT_MAX_REPLICAS,
) -> ClusterPlan:
    """The JSON Patch a verified *request* applies to an AgentCollective
    object (as returned by the API server), or :class:`LifecycleRefused`."""
    action = request.get("action", "")
    role = str(request.get("role") or "")
    if action not in ACTIONS:
        raise LifecycleRefused(REASON_UNKNOWN_ACTION, repr(action))
    if role in CONTROL_ROLES:
        raise LifecycleRefused(REASON_PROTECTED_ROLE, role)

    spec = collective.get("spec") or {}
    if (spec.get("scaling") or {}).get("enabled"):
        # KEDA owns the replica counts; a patch would be overwritten on the
        # next scaling decision, or fight it.
        raise LifecycleRefused(REASON_AUTOSCALED, "spec.scaling is enabled; KEDA owns the replicas")
    agents = list(spec.get("agents") or [])
    index = next((i for i, a in enumerate(agents) if a.get("role") == role), None)
    if index is None:
        raise LifecycleRefused(
            REASON_ROLE_NOT_DECLARED,
            f"{role!r} is not in the AgentCollective; adding a role is an operator edit",
        )
    entry = agents[index]
    has_replicas = "replicas" in entry
    current = int(entry.get("replicas", 1))  # CRD default is 1

    annotations = dict((collective.get("metadata") or {}).get("annotations") or {})
    try:
        paused = json.loads(annotations.get(PAUSED_ANNOTATION) or "{}")
        paused = paused if isinstance(paused, dict) else {}
    except (json.JSONDecodeError, TypeError):
        paused = {}

    busy = sorted(
        w.agent_id for w in workers.values() if w.role == role and w.tasks_in_flight > 0
    )

    if action == ACTION_SCALE:
        if role in paused:
            raise LifecycleRefused(REASON_NO_SUCH_WORKER, f"{role!r} is paused; resume it first")
        target = current + 1
        if target > max_replicas:
            raise LifecycleRefused(
                REASON_POOL_EXHAUSTED, f"{role!r} already at {current} of {max_replicas} replicas",
            )
    elif action == ACTION_RESUME:
        if role not in paused:
            raise LifecycleRefused(REASON_NO_SUCH_WORKER, f"{role!r} is not paused")
        target = max(1, int(paused.pop(role) or 1))
    else:  # stop / pause
        if current <= 0:
            raise LifecycleRefused(REASON_NO_SUCH_WORKER, f"{role!r} has no replicas")
        if busy:
            raise LifecycleRefused(REASON_WORKER_BUSY, ", ".join(busy))
        if action == ACTION_PAUSE:
            paused[role] = current
        else:
            paused.pop(role, None)
        target = 0

    path = f"/spec/agents/{index}/replicas"
    patch: list[dict] = (
        [{"op": "test", "path": path, "value": current}, {"op": "replace", "path": path, "value": target}]
        if has_replicas else
        [{"op": "add", "path": path, "value": target}]
    )
    new_paused = json.dumps(paused, sort_keys=True)
    if new_paused != (annotations.get(PAUSED_ANNOTATION) or "{}"):
        if annotations:
            patch.append({
                "op": "add",
                "path": "/metadata/annotations/" + PAUSED_ANNOTATION.replace("~", "~0").replace("/", "~1"),
                "value": new_paused,
            })
        else:
            patch.append({"op": "add", "path": "/metadata/annotations",
                          "value": {PAUSED_ANNOTATION: new_paused}})
    verb = {"scale": "scale", "stop": "stop", "pause": "pause", "resume": "resume"}[action]
    return ClusterPlan(patch=patch, ops=[Op(verb, f"{role} replicas {current}->{target}")])
