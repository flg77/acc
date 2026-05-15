"""ACC SPIFFE offline-survival monitor (proposal 012 PR-3).

An edge agent's ``spiffe-helper`` sidecar keeps the SVID + trust-bundle
files fresh by talking to the local SPIRE Agent.  When the edge is
partitioned from its parent (nested topology) or from its federation
peers, those files stop refreshing and eventually go stale.

This module watches the bundle file's age and, when it crosses
``offline_max_age_h``, applies the operator-configured
``offline_action``:

- ``rotate``  — the edge-local SPIRE server is expected to have
  rotated its own signing material from its long-lived attested
  credential, so the agent simply keeps serving.  The monitor logs +
  emits an audit event but takes no restrictive action.
- ``degrade`` — the agent should enter read-only mode.  The monitor
  reports ``degrade``; the agent's registered handler flips the flag.
- ``shutdown`` — the agent should exit non-zero (fail-safe).  The
  monitor reports ``shutdown``; the handler calls the exit path.

The monitor is a **building block**: it classifies + emits events but
never itself kills the process or mutates agent state.  The agent
bootstrap wires :meth:`OfflineBundleMonitor.run` with a handler that
performs the actual degrade / shutdown — same split as
:class:`acc.role_sync_listener.RoleSyncListener` (proposal 010 PR-5).

Design reference: proposal 012 §2 G4/G8 + §5 PR-3 step 2.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

logger = logging.getLogger("acc.spiffe_offline")


# Outcome of a freshness check.
STATE_FRESH = "fresh"
ACTION_ROTATE = "rotate"
ACTION_DEGRADE = "degrade"
ACTION_SHUTDOWN = "shutdown"

_VALID_ACTIONS = {ACTION_ROTATE, ACTION_DEGRADE, ACTION_SHUTDOWN}


class EventPublisher(Protocol):
    """Minimal NATS-publish surface — same shape as the one
    :mod:`acc.role_sync_conflict` uses.  ``None`` disables publication.
    """

    async def publish(self, subject: str, payload: bytes) -> None: ...


class OfflineBundleMonitor:
    """Watches the SPIRE trust-bundle file age and reports the
    configured offline action when it goes stale.

    Args:
        bundle_path: absolute path to the trust-bundle file the
            spiffe-helper sidecar refreshes (the agent's
            ``svid_mount_path`` + bundle file name).
        offline_max_age_h: bundle age (hours) past which the bundle is
            considered stale and ``offline_action`` fires.
        offline_action: one of ``rotate`` / ``degrade`` / ``shutdown``
            (``security.spiffe.offline_action``).
        publisher: optional NATS publisher for audit events.
        events_subject: subject prefix; the monitor publishes on
            ``<events_subject>.offline``.
        now: optional time source (defaults to ``time.time``) — tests
            inject this for deterministic age arithmetic.
    """

    def __init__(
        self,
        bundle_path: str | Path,
        offline_max_age_h: float,
        offline_action: str,
        publisher: Optional[EventPublisher] = None,
        events_subject: str = "acc.spiffe",
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        if offline_action not in _VALID_ACTIONS:
            raise ValueError(
                f"offline_action {offline_action!r} is not one of "
                f"{sorted(_VALID_ACTIONS)}"
            )
        self._bundle_path = Path(bundle_path)
        self._max_age_s = offline_max_age_h * 3600.0
        self._action = offline_action
        self._publisher = publisher
        self._subject = events_subject.rstrip(".")
        self._now = now if now is not None else time.time
        self._task: Optional[asyncio.Task[None]] = None
        # Counters — exposed for /metrics integration in a follow-up.
        self.fresh_count = 0
        self.stale_count = 0

    # ----- freshness inspection ----------------------------------------

    def bundle_age_s(self, now: Optional[float] = None) -> Optional[float]:
        """Return the bundle file's age in seconds, or ``None`` when
        the file is missing (which :meth:`check` treats as stale)."""
        try:
            mtime = self._bundle_path.stat().st_mtime
        except OSError:
            return None
        current = now if now is not None else self._now()
        return max(0.0, current - mtime)

    def check(self, now: Optional[float] = None) -> str:
        """Classify the bundle's freshness.

        Returns :data:`STATE_FRESH` when the bundle is within
        ``offline_max_age_h``; otherwise returns the configured
        ``offline_action`` (``rotate`` / ``degrade`` / ``shutdown``).

        A missing bundle file counts as stale.
        """
        age = self.bundle_age_s(now)
        if age is not None and age <= self._max_age_s:
            self.fresh_count += 1
            return STATE_FRESH

        self.stale_count += 1
        logger.warning(
            "spiffe-offline: trust bundle at %s is stale (age=%s, "
            "max=%.0fs) — applying offline_action=%s",
            self._bundle_path,
            "missing" if age is None else f"{age:.0f}s",
            self._max_age_s,
            self._action,
        )
        return self._action

    # ----- event publication -------------------------------------------

    async def publish_offline(self, action: str, age_s: Optional[float]) -> None:
        """Emit ``<events_subject>.offline`` describing the stale-bundle
        event so an audit log / dashboard can record partition
        patterns.  No-op when no publisher is configured."""
        if self._publisher is None:
            return
        body = {
            "bundle_path": str(self._bundle_path),
            "action": action,
            "bundle_age_s": age_s,
            "max_age_s": self._max_age_s,
            "ts": time.time(),
        }
        subject = f"{self._subject}.offline"
        try:
            payload = json.dumps(body, sort_keys=True).encode("utf-8")
            await self._publisher.publish(subject, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("spiffe-offline: publish %s failed: %s", subject, exc)

    # ----- poll loop ----------------------------------------------------

    async def start(
        self,
        poll_interval_s: float,
        handler: Callable[[str], Awaitable[None]],
    ) -> None:
        """Begin polling.  On each stale detection the monitor emits an
        audit event and invokes *handler* with the action string
        (``rotate`` / ``degrade`` / ``shutdown``); the handler performs
        the actual agent-side behaviour.  Safe to call once.

        The handler is **not** invoked while the bundle is fresh.
        """
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._poll_loop(poll_interval_s, handler),
            name="spiffe-offline-monitor",
        )
        logger.info(
            "spiffe-offline: monitor started (bundle=%s, max_age=%.0fs, "
            "action=%s, interval=%.0fs)",
            self._bundle_path, self._max_age_s, self._action, poll_interval_s,
        )

    async def stop(self) -> None:
        """Cancel the poll loop if running."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _poll_loop(
        self,
        poll_interval_s: float,
        handler: Callable[[str], Awaitable[None]],
    ) -> None:
        while True:
            try:
                result = self.check()
                if result != STATE_FRESH:
                    await self.publish_offline(result, self.bundle_age_s())
                    try:
                        await handler(result)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "spiffe-offline: handler raised for action=%s",
                            result,
                        )
            except Exception:  # noqa: BLE001
                logger.exception("spiffe-offline: poll iteration failed")
            try:
                await asyncio.sleep(poll_interval_s)
            except asyncio.CancelledError:
                raise
