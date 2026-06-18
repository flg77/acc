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
import contextlib
import logging
from typing import Any

from acc.webgui.serialize import snapshot_to_dict

logger = logging.getLogger("acc.webgui.observers")

_QUEUE_MAX = 50  # matches acc.tui.app._QUEUE_MAX
# Background reconnect backoff (seconds) when NATS is unreachable at boot.
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0


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
        self._reconnect_tasks: list[asyncio.Task] = []
        # cid -> latest snapshot dict; cid -> set of WebSocket clients.
        self._latest: dict[str, dict] = {}
        self._ws_clients: dict[str, set] = {cid: set() for cid in collective_ids}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bring up one observer per collective.

        NATS being unreachable at boot is **non-fatal**: acc-webgui is a
        read-only observability surface, so a collective that fails to
        connect is retried in a background task (with backoff) instead of
        crashing the process.  ``/health`` and the SPA serve regardless —
        the UI shows the collective as not-yet-connected rather than the
        pod CrashLoopBackOff-ing on ``NoServersError``.

        When NATS *is* reachable (the normal path and the test fakes), the
        connect completes synchronously here, so ``observer(cid)`` is live
        the moment ``start()`` returns.
        """
        for cid in self._collective_ids:
            queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
            self._queues[cid] = queue
            if not await self._try_connect(cid, queue):
                self._reconnect_tasks.append(
                    asyncio.create_task(
                        self._reconnect_loop(cid, queue),
                        name=f"webgui-reconnect-{cid}",
                    )
                )
        logger.info("webgui: observing %d collective(s): %s",
                    len(self._collective_ids), ", ".join(self._collective_ids))

    async def _try_connect(self, cid: str, queue: asyncio.Queue) -> bool:
        """Attempt one connect+subscribe for *cid*.

        On success the observer is registered and its drain task started;
        returns True.  On failure (NATS unreachable) it logs, cleans up,
        and returns False — the caller schedules a background retry.
        """
        from acc.tui.client import NATSObserver  # noqa: PLC0415 — reuse

        obs = NATSObserver(
            nats_url=self._nats_url,
            collective_id=cid,
            update_queue=queue,
            nkey_seed_path=self._nkey_seed_path,
        )
        try:
            await obs.connect()
            await obs.subscribe()
        except Exception as exc:  # NATS down/unreachable — degrade, don't die
            with contextlib.suppress(Exception):
                await obs.close()
            logger.warning("webgui: collective %s not connected (%s) — will retry", cid, exc)
            return False
        self._observers[cid] = obs
        self._drain_tasks.append(
            asyncio.create_task(self._drain(cid, queue), name=f"webgui-drain-{cid}")
        )
        return True

    async def _reconnect_loop(self, cid: str, queue: asyncio.Queue) -> None:
        """Retry the initial connect for *cid* with capped backoff until it
        succeeds.  (Once connected, the NATS client handles transient
        reconnects itself — this loop only covers the boot-time connect.)"""
        backoff = _RECONNECT_MIN_S
        while True:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)
            if await self._try_connect(cid, queue):
                logger.info("webgui: collective %s connected after retry", cid)
                return

    async def stop(self) -> None:
        """Cancel reconnect + drain tasks and close every NATS connection."""
        for task in (*self._reconnect_tasks, *self._drain_tasks):
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
