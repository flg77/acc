"""ObserverHub — per-collective NATSObserver lifecycle + WebSocket fan-out.

The single most important architectural decision of acc-webgui
(proposal §4): the web backend **reuses** `acc.tui.client.NATSObserver`
and `acc.tui.models.CollectiveSnapshot` rather than reimplementing the
NATS signal layer.  Feature parity with the TUI is therefore
structural — a new signal type handled by the TUI's observer is picked
up by the web frontend with no extra work.

`ObserverHub` runs one `NATSObserver` per observed collective (exactly
as `acc.tui.app` does), drains each observer's snapshot queue, keeps
the latest snapshot, and fans every update to all WebSocket clients
subscribed to that collective.  One observer per *collective* — not
per client — so observer count is bounded by collectives, not users.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from acc.webgui.serialize import snapshot_to_dict

logger = logging.getLogger("acc.webgui.observers")

_QUEUE_MAX = 50  # matches acc.tui.app._QUEUE_MAX


class ObserverHub:
    """Owns the NATSObservers and the live snapshot fan-out.

    Args:
        nats_url: NATS server URL.
        collective_ids: collectives to observe.
        nkey_seed_path: optional `tui`/`observer` NKey seed (proposal 013).
    """

    def __init__(
        self,
        nats_url: str,
        collective_ids: list[str],
        nkey_seed_path: str | None = None,
    ) -> None:
        self._nats_url = nats_url
        self._collective_ids = list(collective_ids)
        self._nkey_seed_path = nkey_seed_path or None
        self._observers: dict[str, Any] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._drain_tasks: list[asyncio.Task] = []
        # cid -> latest snapshot dict; cid -> set of WebSocket clients.
        self._latest: dict[str, dict] = {}
        self._ws_clients: dict[str, set] = {cid: set() for cid in collective_ids}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect + subscribe every observer; start the drain tasks."""
        from acc.tui.client import NATSObserver  # noqa: PLC0415 — reuse

        for cid in self._collective_ids:
            queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
            obs = NATSObserver(
                nats_url=self._nats_url,
                collective_id=cid,
                update_queue=queue,
                nkey_seed_path=self._nkey_seed_path,
            )
            await obs.connect()
            await obs.subscribe()
            self._observers[cid] = obs
            self._queues[cid] = queue
            self._drain_tasks.append(
                asyncio.create_task(self._drain(cid, queue), name=f"webgui-drain-{cid}")
            )
        logger.info("webgui: observing %d collective(s): %s",
                    len(self._collective_ids), ", ".join(self._collective_ids))

    async def stop(self) -> None:
        """Cancel drain tasks and close every NATS connection."""
        for task in self._drain_tasks:
            task.cancel()
        for obs in self._observers.values():
            try:
                await obs.close()
            except Exception:
                logger.exception("webgui: observer close failed")

    async def _drain(self, cid: str, queue: asyncio.Queue) -> None:
        """Drain one collective's snapshot queue → cache + fan to WS."""
        while True:
            snapshot = await queue.get()
            try:
                data = snapshot_to_dict(snapshot)
            except Exception:
                logger.exception("webgui: snapshot serialise failed (%s)", cid)
                continue
            self._latest[cid] = data
            await self._broadcast(cid, data)

    async def _broadcast(self, cid: str, data: dict) -> None:
        """Push *data* to every WebSocket client for *cid*; drop dead ones."""
        clients = self._ws_clients.get(cid, set())
        if not clients:
            return
        dead = []
        for ws in list(clients):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    # ------------------------------------------------------------------
    # Accessors used by the routes / WS endpoint
    # ------------------------------------------------------------------

    def collective_ids(self) -> list[str]:
        return list(self._collective_ids)

    def latest(self, cid: str) -> dict | None:
        """The most recent snapshot dict for *cid*, or None if none yet."""
        return self._latest.get(cid)

    def observer(self, cid: str):
        """The live `NATSObserver` for *cid* — used by the action layer
        (proposal 015 PR-3: `WebPromptChannel`, role infusion, etc.)."""
        return self._observers.get(cid)

    def register_ws(self, cid: str, ws) -> bool:
        """Register a WebSocket client for *cid*.  Returns False for an
        unknown collective."""
        if cid not in self._ws_clients:
            return False
        self._ws_clients[cid].add(ws)
        return True

    def unregister_ws(self, cid: str, ws) -> None:
        self._ws_clients.get(cid, set()).discard(ws)
