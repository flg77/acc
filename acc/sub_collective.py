"""Sub-collective registry + lifecycle helpers — AoA Phase 3.

Proposal `20260530-assistant-agent-of-agents` Phase 3.

The hub collective hosts the Assistant + baseline observer / arbiter
infrastructure; each knowledge domain runs as its own sub-collective
that spins up on demand and hibernates when idle.  This module owns:

* :class:`SubCollectiveRegistry` — runtime view of the hub's
  managed sub-collectives.  Methods to register from a
  :class:`acc.collective.CollectiveSpec`, look up by cid or domain,
  and render the seed-context block the Assistant's prompt builder
  consumes so the LLM knows the routing surface.
* :func:`route_for_domain` — pure domain-to-cid lookup.  The
  Assistant's reasoning is the *policy*; this is the lookup table.
* :func:`build_seed_context_block` — short paragraph the Assistant
  prepends to its system prompt: "you have sub-collectives X (domain
  Y, role_templates Z) — use ``[DELEGATE:X:<reason>]`` to route".
  Reuses the existing ACC-9 ``[DELEGATE:cid:reason]`` marker so no
  new parser is needed (PR-V6's ``_parse_delegation`` handles it).
* :func:`encode_lifecycle_payload` /
  :func:`decode_lifecycle_payload` — wire format for the lifecycle
  signal subject.  The actual consumer (apply-watcher integration)
  is a sibling Phase-3 follow-up; the model + the subject helper
  land here so SIP-P2 and AoA-P6 can build on a stable shape.

Phase 3 is **pure Python** — no podman, no NATS interaction beyond
the subject helpers.  The host-side `acc-deploy.sh resume <cid>` /
`hibernate <cid>` subcommands and the bus → script bridge ship in a
follow-up proposal once we have lighthouse miles.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

logger = logging.getLogger("acc.sub_collective")


# Lifecycle actions on the sub-collective control subject.
LIFECYCLE_RESUME = "resume"
LIFECYCLE_HIBERNATE = "hibernate"

LIFECYCLE_ACTIONS: frozenset[str] = frozenset({
    LIFECYCLE_RESUME, LIFECYCLE_HIBERNATE,
})


@dataclass
class _RegisteredSubCollective:
    """In-registry view of a single sub-collective entry.

    Lightweight wrapper around :class:`acc.collective.SubCollectiveSpec`
    that adds a stable cid field + runtime "last activity" timestamp.
    The timestamp is the seam the hibernation policy reads from:
    actual eviction lives in the host-side lifecycle handler, not
    here.
    """

    cid: str
    domain: str
    role_templates: tuple[str, ...]
    idle_hibernate_minutes: int
    model: Optional[str]
    description: str
    last_active_ts: float = 0.0


class SubCollectiveRegistry:
    """Runtime catalogue of the hub's managed sub-collectives.

    Built once at Assistant boot from
    :attr:`CollectiveSpec.managed_sub_collectives`; consulted on every
    Assistant prompt to:

    * inject the seed-context block (so the LLM knows what's
      available),
    * resolve a domain → cid for routing decisions,
    * stamp the activity timestamp when a delegation actually fires.

    Thread-safety: methods are not coroutines and the underlying dict
    is mutated only from the Assistant's task loop, which is
    single-flighted by the agent task_loop.  No locking needed today.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _RegisteredSubCollective] = {}

    # ------------------------------------------------------------------
    # Build the registry from a CollectiveSpec
    # ------------------------------------------------------------------

    def register_from_spec(self, managed: dict) -> None:
        """Populate the registry from
        :attr:`CollectiveSpec.managed_sub_collectives`.

        Idempotent — entries already present keep their
        ``last_active_ts``; new entries land with ``ts = 0``.  This
        matters across config reloads: an idle hibernation policy
        shouldn't reset just because the operator edited the YAML.
        """
        if not managed:
            return
        for cid, sub in managed.items():
            cid_str = str(cid).strip()
            if not cid_str:
                continue
            prior = self._entries.get(cid_str)
            self._entries[cid_str] = _RegisteredSubCollective(
                cid=cid_str,
                domain=str(getattr(sub, "domain", "") or ""),
                role_templates=tuple(
                    str(rt) for rt in getattr(sub, "role_templates", []) or []
                ),
                idle_hibernate_minutes=int(
                    getattr(sub, "idle_hibernate_minutes", 30) or 30
                ),
                model=getattr(sub, "model", None),
                description=str(getattr(sub, "description", "") or ""),
                last_active_ts=(
                    prior.last_active_ts if prior is not None else 0.0
                ),
            )

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, cid: str) -> Optional[_RegisteredSubCollective]:
        return self._entries.get(cid)

    def cids(self) -> list[str]:
        """Stable-sorted list of registered sub-collective cids."""
        return sorted(self._entries.keys())

    def by_domain(self, domain: str) -> list[_RegisteredSubCollective]:
        """All sub-collectives owning the given domain.

        Returns a list because operators can declare multiple
        sub-collectives per domain (e.g. a primary + a reviewer
        sub-collective for software_engineering).  Routing picks
        the head by default; future credit-shared routing in
        SIP-P3+ can use the full list.
        """
        target = (domain or "").strip().lower()
        if not target:
            return []
        return [
            entry for entry in self._entries.values()
            if entry.domain.strip().lower() == target
        ]

    def mark_active(self, cid: str, ts: Optional[float] = None) -> None:
        """Stamp the registry entry's ``last_active_ts``.

        Called by the Assistant's task loop whenever a
        ``[DELEGATE:<cid>:…]`` marker successfully fires for this cid.
        Idle-eviction policy reads from this; a sub-collective that
        hasn't been touched in ``idle_hibernate_minutes`` is a
        hibernation candidate.
        """
        entry = self._entries.get(cid)
        if entry is None:
            return
        entry.last_active_ts = float(ts if ts is not None else time.time())

    def stale_cids(self, now_ts: Optional[float] = None) -> list[str]:
        """Return cids whose idle timer has elapsed.

        Pure read — the actual hibernation publish lives in the
        host-side lifecycle handler.  We just answer "what's stale".
        Sub-collectives that have never been activated (``last_active_
        ts == 0``) are explicitly NOT considered stale — they're
        candidates for resume, not eviction.
        """
        now = float(now_ts if now_ts is not None else time.time())
        out: list[str] = []
        for entry in self._entries.values():
            if entry.last_active_ts <= 0.0:
                continue
            window_s = entry.idle_hibernate_minutes * 60
            if now - entry.last_active_ts >= window_s:
                out.append(entry.cid)
        return sorted(out)


# ---------------------------------------------------------------------------
# Pure domain → cid routing
# ---------------------------------------------------------------------------


def route_for_domain(
    registry: SubCollectiveRegistry,
    domain: str,
) -> Optional[str]:
    """Pick a sub-collective cid for ``domain``, or ``None``.

    Phase 3 ships the simple-first policy: the lexicographically
    smallest cid among sub-collectives matching the domain.  Stable +
    deterministic; SIP-P3 / AoA-P6 can layer a load-balanced /
    learned policy on top.
    """
    matches = registry.by_domain(domain)
    if not matches:
        return None
    return sorted(m.cid for m in matches)[0]


# ---------------------------------------------------------------------------
# Seed-context block — the Assistant's view of the routing surface
# ---------------------------------------------------------------------------


def build_seed_context_block(registry: SubCollectiveRegistry) -> str:
    """Render a short paragraph the Assistant prepends to its
    system prompt.

    Empty when the registry has no entries (single-collective
    deployments behave exactly as today).  Format:

    >  Managed sub-collectives you can delegate to via
    >  [DELEGATE:<cid>:<reason>]:
    >  - sol-code (domain: software_engineering;
    >    role_templates: coding_agent, coding_agent_tester) —
    >    Code generation, review, testing.
    >  - sol-medical (domain: clinical_research; …)
    """
    cids = registry.cids()
    if not cids:
        return ""
    lines = [
        "Managed sub-collectives you can delegate to via "
        "[DELEGATE:<cid>:<reason>]:"
    ]
    for cid in cids:
        entry = registry.get(cid)
        if entry is None:  # defensive
            continue
        tail = ""
        if entry.description:
            tail = f" — {entry.description}"
        role_list = ", ".join(entry.role_templates)
        if not role_list:
            role_list = "(none declared)"
        lines.append(
            f"- {cid} (domain: {entry.domain or '(unset)'}; "
            f"role_templates: {role_list}){tail}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lifecycle signal wire format
# ---------------------------------------------------------------------------


def encode_lifecycle_payload(
    *,
    action: str,
    sub_cid: str,
    reason: str = "",
    operator_id: str = "default",
    ts: Optional[float] = None,
) -> dict[str, Any]:
    """Build the payload published on
    :func:`acc.signals.subject_sub_collective_lifecycle`.

    Raises ``ValueError`` on an unknown action so the host-side
    handler never gets a typo'd verb.
    """
    if action not in LIFECYCLE_ACTIONS:
        raise ValueError(
            f"unknown sub-collective lifecycle action {action!r} — "
            f"expected one of {sorted(LIFECYCLE_ACTIONS)}"
        )
    return {
        "action": action,
        "sub_cid": str(sub_cid).strip(),
        "reason": str(reason or "").strip(),
        "operator_id": str(operator_id or "default").strip(),
        "ts": float(ts if ts is not None else time.time()),
    }


def decode_lifecycle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalise a payload read off the lifecycle subject.

    Returns the cleaned dict with defaults filled in; raises
    ``ValueError`` when ``action`` or ``sub_cid`` is missing /
    invalid so the host-side handler can log + drop the bad
    request rather than acting on garbage.
    """
    action = str((payload or {}).get("action") or "").strip().lower()
    if action not in LIFECYCLE_ACTIONS:
        raise ValueError(
            f"lifecycle payload action {action!r} not in "
            f"{sorted(LIFECYCLE_ACTIONS)}"
        )
    sub_cid = str((payload or {}).get("sub_cid") or "").strip()
    if not sub_cid:
        raise ValueError("lifecycle payload missing sub_cid")
    return {
        "action": action,
        "sub_cid": sub_cid,
        "reason": str(payload.get("reason", "") or ""),
        "operator_id": str(payload.get("operator_id", "default") or "default"),
        "ts": float(payload.get("ts", time.time()) or time.time()),
    }


def write_lifecycle_request(
    apply_dir: Any,
    *,
    action: str,
    sub_cid: str,
    reason: str = "",
    operator_id: str = "default",
    ts: Optional[float] = None,
) -> Any:
    """Atomically write a lifecycle request file the host-side watcher
    polls.

    Phase 3b consumer:
    ``scripts/acc-lifecycle-watcher.sh`` polls
    ``<apply_dir>/sub_collective.request`` for change.  The bus-side
    publisher (Assistant's cognitive loop on cold delegation; idle
    scanner on stale_cids) writes through this helper.  Writing to a
    `.tmp` then `os.replace` guarantees the watcher never reads a
    half-written file.

    Returns the request-file Path.
    """
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    payload = encode_lifecycle_payload(
        action=action, sub_cid=sub_cid, reason=reason,
        operator_id=operator_id, ts=ts,
    )
    target_dir = Path(apply_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    req = target_dir / "sub_collective.request"
    tmp = req.with_name(".sub_collective.request.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, req)
    return req


__all__ = [
    "LIFECYCLE_RESUME",
    "LIFECYCLE_HIBERNATE",
    "LIFECYCLE_ACTIONS",
    "SubCollectiveRegistry",
    "route_for_domain",
    "build_seed_context_block",
    "encode_lifecycle_payload",
    "decode_lifecycle_payload",
    "write_lifecycle_request",
]
