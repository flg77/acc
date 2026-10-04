"""Spawn, then hand over (pure core + a small Redis-backed store).

`20261003-assistant-orchestrated-infusion` Phase 3.  When one assistant reply
both spawns a role and routes the task to it, the route used to fire at once,
at a role no agent held yet; the operator had to say "confirmed" a turn later
so the assistant would route again.  Now the route is **parked** and released
when the role is actually held:

* a heartbeat from an agent in that role, state ACTIVE (a promoted worker, or
  one that was already running), or a reconcile that reports the role
  already active, releases it, through the same execute-or-queue decision the
  route was given in the first place, so the console still gates it in the
  modes that ask;
* a reconcile that found no dormant worker asks once for one more worker
  (``PROPOSE_LIFECYCLE:scale``, phase 2) and keeps waiting;
* no signing key, a refused scale, or the deadline drops it, and the operator
  is told why in the thread.  It never routes to a role no agent holds.

Everything that decides lives here and is pure; the agent wires it to NATS.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from acc.assistant_proposal import (
    PROPOSAL_ROUTE,
    PROPOSAL_SPAWN,
    AssistantProposal,
)

#: How long a parked hand-off waits for its role when the spawn went straight
#: through.  A promoted worker reports ACTIVE within a couple of heartbeats.
DEFAULT_DEADLINE_S = 120.0
#: ...and when the spawn itself waits for the console (a human may take a while).
DEFAULT_QUEUED_DEADLINE_S = 1800.0

DISPATCH_EXECUTE = "execute"
DISPATCH_QUEUE = "queue"

# What a parked hand-off can turn into.
RELEASE = "release"
DROP = "drop"
SCALE = "scale"

# Drop reasons (operator-facing text lives in acc.tui.outcomes).
DROP_DEADLINE = "deadline"
DROP_NO_SIGNING_KEY = "no_signing_key"
DROP_SCALE_REFUSED = "scale_refused"


@dataclass
class ParkedHandover:
    """A route waiting for its role to be held."""

    role: str
    route: dict[str, Any]          # AssistantProposal.to_payload() of the ROUTE
    dispatch: str                  # how the route was classified: execute | queue
    operating_mode: str
    task_id: str
    spawn_proposal_id: str
    parked_at: float
    deadline: float
    scale_requested: bool = False

    @property
    def key(self) -> str:
        return str(self.route.get("proposal_id") or "")

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "ParkedHandover":
        return cls(**json.loads(raw))


@dataclass(frozen=True)
class Decision:
    action: str                    # RELEASE | DROP | SCALE
    handover: ParkedHandover
    reason: str = ""


# ---------------------------------------------------------------------------
# Pairing: which routes in one reply wait for a spawn in the same reply
# ---------------------------------------------------------------------------


def pair_spawn_and_route(
    executed: list[AssistantProposal],
    queued: list[AssistantProposal],
    *,
    operating_mode: str,
    task_id: str,
    now: float | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
    queued_deadline_s: float = DEFAULT_QUEUED_DEADLINE_S,
) -> tuple[list[AssistantProposal], list[AssistantProposal], list[ParkedHandover]]:
    """Pull out every ROUTE whose target a SPAWN in the same reply brings up.

    Returns ``(executed, queued, parked)``; the SPAWNs stay where they were.
    A route with no matching spawn is left alone: its role is either running
    already or the route is the assistant's own call to make.
    """
    t = time.time() if now is None else now
    spawns = {
        str(p.params.get("role") or ""): (p, p in queued)
        for p in [*executed, *queued] if p.kind == PROPOSAL_SPAWN
    }
    parked: list[ParkedHandover] = []

    def _keep(p: AssistantProposal, dispatch: str) -> bool:
        if p.kind != PROPOSAL_ROUTE:
            return True
        role = str(p.params.get("target_role") or "")
        if role not in spawns:
            return True
        spawn, spawn_queued = spawns[role]
        parked.append(ParkedHandover(
            role=role,
            route=p.to_payload(),
            dispatch=dispatch,
            operating_mode=operating_mode,
            task_id=task_id or p.task_id,
            spawn_proposal_id=spawn.proposal_id,
            parked_at=t,
            deadline=t + (queued_deadline_s if spawn_queued else deadline_s),
        ))
        return False

    executed_out = [p for p in executed if _keep(p, DISPATCH_EXECUTE)]
    queued_out = [p for p in queued if _keep(p, DISPATCH_QUEUE)]
    return executed_out, queued_out, parked


# ---------------------------------------------------------------------------
# Events → decisions
# ---------------------------------------------------------------------------


def on_heartbeat(data: dict, parked: Iterable[ParkedHandover]) -> list[Decision]:
    """An agent reporting ACTIVE in a role releases every hand-off waiting for it."""
    if str(data.get("state") or "") != "ACTIVE" or data.get("dormant"):
        return []
    role = str(data.get("role") or "")
    return [Decision(RELEASE, h) for h in parked if h.role == role]


def on_outcome(data: dict, parked: Iterable[ParkedHandover]) -> list[Decision]:
    """What an arbiter / broker outcome means for the hand-offs waiting on it."""
    trigger = str(data.get("trigger") or "")
    out: list[Decision] = []
    if trigger == "reconcile_result":
        role = str(data.get("role") or "")
        unmet = set(str(r) for r in (data.get("unmet") or []))
        for h in parked:
            if h.role != role and h.role not in unmet:
                continue
            if h.role in unmet:
                if str(data.get("reason") or "") == DROP_NO_SIGNING_KEY:
                    out.append(Decision(DROP, h, DROP_NO_SIGNING_KEY))
                elif not h.scale_requested:
                    out.append(Decision(SCALE, h))
            elif int(data.get("already_active") or 0) > 0 and h.role == role:
                out.append(Decision(RELEASE, h))
    elif trigger == "lifecycle_result" and data.get("action") == "scale" and not data.get("ok"):
        role = str(data.get("role") or "")
        out.extend(
            Decision(DROP, h, f"{DROP_SCALE_REFUSED}:{data.get('reason') or ''}")
            for h in parked if h.role == role and h.scale_requested
        )
    return out


def expired(parked: Iterable[ParkedHandover], now: float) -> list[Decision]:
    return [Decision(DROP, h, DROP_DEADLINE) for h in parked if now >= h.deadline]


# ---------------------------------------------------------------------------
# Store: Redis when there is one (survives an assistant restart), else memory
# ---------------------------------------------------------------------------


@dataclass
class HandoverStore:
    collective_id: str
    redis: Any = None
    _mem: dict[str, ParkedHandover] = field(default_factory=dict)

    def _rkey(self, key: str) -> str:
        return f"acc:{self.collective_id}:pending_handover:{key}"

    def put(self, h: ParkedHandover) -> None:
        self._mem[h.key] = h
        if self.redis is not None:
            try:
                ttl = max(60, int(h.deadline - time.time()) + 60)
                self.redis.setex(self._rkey(h.key), ttl, h.to_json())
            except Exception:  # noqa: BLE001 -- memory still holds it
                pass

    def remove(self, key: str) -> None:
        self._mem.pop(key, None)
        if self.redis is not None:
            try:
                self.redis.delete(self._rkey(key))
            except Exception:  # noqa: BLE001
                pass

    def all(self) -> list[ParkedHandover]:
        return list(self._mem.values())

    def load(self) -> int:
        """Re-read parked hand-offs after a restart; returns how many."""
        if self.redis is None:
            return 0
        try:
            for k in self.redis.scan_iter(self._rkey("*")):
                raw = self.redis.get(k)
                if raw:
                    h = ParkedHandover.from_json(raw)
                    self._mem[h.key] = h
        except Exception:  # noqa: BLE001
            pass
        return len(self._mem)
