"""Phase F — cross-collective A2A federation.

OpenSpec: ``openspec/changes/20260604-a2a-federation/`` (this Phase F slice).
Builds on the Phase 1-5 A2A substrate:

* :func:`acc.a2a.card.build_agent_card` — what each agent advertises.
* :mod:`acc.a2a.server` — serves the card at ``/.well-known/agent-card.json``.
* :func:`acc.a2a.client.call_peer` — outbound JSON-RPC.
* :func:`acc.a2a.client.select_transport` — A2A vs NATS routing matrix.

Phase F adds a **federation discovery cache** on top: given an
``AgentConfig`` carrying ``peer_collectives`` + ``peer_a2a_urls``, fetch
each peer's agent card once, refresh on TTL, and expose a query helper
so the orchestrator can ask "which peer collective hosts a `data_engineer`
role?" without an extra round-trip per dispatch.

Three pieces:

* :class:`PeerCardEntry` — one peer collective's last-known card +
  fetched-at timestamp.
* :class:`FederationCache` — in-memory dict keyed by collective_id.
* :func:`discover_peer_cards` — async fan-out fetch.  Returns a fresh
  :class:`FederationCache`; callers swap atomically.

This module deliberately does NOT touch dispatch logic — the
orchestrator's existing ``[DELEGATE:cid:reason]`` path through
:meth:`acc.agent.Agent._delegate_task` consumes the cache when it's
present (Phase F.2 wiring; not in this file).

Failure handling: a peer that 404s or times out is recorded with
``card=None`` + ``error=str(exc)`` so the orchestrator can decide to
fall back to NATS bridge without re-trying the failing peer every
dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Refresh peer cards every 5 minutes by default — peer agents rarely
# change their advertised skill set within a single operator session.
_DEFAULT_TTL_S = 300.0


@dataclass(frozen=True)
class PeerCardEntry:
    """One peer collective's discovery record.

    ``card`` is the raw A2A v1 dict from
    :func:`acc.a2a.card.build_agent_card`; ``error`` is a one-line
    diagnostic when the fetch failed.  Exactly one of the two is set.
    """

    collective_id: str
    a2a_url: str
    card: Optional[dict[str, Any]]
    fetched_at_s: float
    error: Optional[str] = None

    @property
    def is_reachable(self) -> bool:
        return self.card is not None

    def skill_ids(self) -> list[str]:
        """A2A card v1 lists skills under top-level ``skills``."""
        if self.card is None:
            return []
        return [str(s.get("id", "")) for s in self.card.get("skills", []) if s.get("id")]


@dataclass
class FederationCache:
    """In-memory peer-card cache.

    Threading: this dataclass is read-mostly.  Callers that mutate
    (Phase F.2 swap-on-refresh) replace the whole instance atomically
    on the owning ``Agent`` rather than mutating in place.
    """

    entries: dict[str, PeerCardEntry] = field(default_factory=dict)
    ttl_s: float = _DEFAULT_TTL_S

    def get(self, collective_id: str) -> Optional[PeerCardEntry]:
        return self.entries.get(collective_id)

    def reachable_peers(self) -> list[PeerCardEntry]:
        return [e for e in self.entries.values() if e.is_reachable]

    def find_skill(self, skill_id: str) -> list[PeerCardEntry]:
        """Peers advertising ``skill_id``.  Empty when nobody does."""
        return [e for e in self.reachable_peers() if skill_id in e.skill_ids()]

    def is_stale(self, now_s: Optional[float] = None) -> bool:
        """True when ANY entry is older than ``ttl_s`` — caller should
        re-fan-out :func:`discover_peer_cards`."""
        if not self.entries:
            return True
        now = now_s if now_s is not None else time.monotonic()
        return any(now - e.fetched_at_s > self.ttl_s for e in self.entries.values())


async def _fetch_one(
    collective_id: str,
    a2a_url: str,
    *,
    timeout: float,
    session: Any = None,
) -> PeerCardEntry:
    """Fetch ``GET /.well-known/agent-card.json`` from a single peer.

    aiohttp is imported lazily so the rest of ACC keeps working when
    the ``acc[a2a]`` extra isn't installed.  Tests pass in their own
    fake session, in which case aiohttp is never imported.
    """
    url = a2a_url.rstrip("/") + "/.well-known/agent-card.json"
    now = time.monotonic()
    owns_session = session is None
    if owns_session:
        import aiohttp  # type: ignore

        session = aiohttp.ClientSession()
        get_kwargs: dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=timeout)}
    else:
        get_kwargs = {}
    try:
        try:
            async with session.get(url, **get_kwargs) as r:
                if r.status != 200:
                    return PeerCardEntry(
                        collective_id=collective_id,
                        a2a_url=a2a_url,
                        card=None,
                        fetched_at_s=now,
                        error=f"http {r.status}",
                    )
                card = await r.json()
        finally:
            if owns_session:
                await session.close()
        return PeerCardEntry(
            collective_id=collective_id,
            a2a_url=a2a_url,
            card=card,
            fetched_at_s=now,
        )
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:  # pragma: no cover
        return PeerCardEntry(
            collective_id=collective_id,
            a2a_url=a2a_url,
            card=None,
            fetched_at_s=now,
            error=f"{type(exc).__name__}: {exc}",
        )


async def discover_peer_cards(
    peer_a2a_urls: dict[str, str],
    *,
    timeout: float = 5.0,
    ttl_s: float = _DEFAULT_TTL_S,
    session: Any = None,
) -> FederationCache:
    """Fan-out fetch every configured peer.

    Returns a fresh :class:`FederationCache`.  Peers with an empty URL
    or a fetch failure are still recorded (with ``card=None``) so the
    orchestrator can distinguish "no peer configured" from "peer down".
    Order does not matter; identical collective_ids in the input dict
    collapse to one entry (dict semantics).
    """
    if not peer_a2a_urls:
        return FederationCache(entries={}, ttl_s=ttl_s)

    tasks = [
        _fetch_one(cid, url, timeout=timeout, session=session)
        for cid, url in peer_a2a_urls.items()
        if url  # skip empty URLs; they go in as "not configured"
    ]
    results = await asyncio.gather(*tasks)
    entries = {e.collective_id: e for e in results}
    logger.info(
        "a2a.federation: discovered %d peer(s), %d reachable",
        len(entries),
        sum(1 for e in entries.values() if e.is_reachable),
    )
    return FederationCache(entries=entries, ttl_s=ttl_s)
