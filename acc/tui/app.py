"""ACC TUI — ACCTUIApp: main Textual application.

Lifecycle:
    1. Read ACC_NATS_URL, ACC_COLLECTIVE_IDS (or ACC_COLLECTIVE_ID) from env.
    2. Create one NATSObserver per collective (multi-collective — REQ-TUI-006).
    3. Connect each observer with exponential backoff retry.
    4. Subscribe to acc.{collective_id}.> on NATS.
    5. Start _drain_queue() background task per observer.
    6. Push DashboardScreen (soma) as the initial screen.
    7. Handle NavigateTo messages from NavigationBar (REQ-TUI-003/004).
    8. Start WebBridge background server when ACC_TUI_WEB_PORT is set (REQ-TUI-041).
    9. On shutdown: close all NATS connections, stop WebBridge.

Env vars:
    ACC_NATS_URL          NATS URL (default: nats://localhost:4222)
    ACC_COLLECTIVE_IDS    Comma-separated collective IDs (e.g. sol-01,sol-02)
    ACC_COLLECTIVE_ID     Single collective ID — fallback when IDS not set
    ACC_TUI_WEB_PORT      HTTP port for WebBridge (0 = disabled)
    ACC_ROLES_ROOT        Path to roles/ directory (default: roles)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Label

from acc.tui.client import NATSObserver
from acc.tui.models import CollectiveSnapshot
from acc.tui.screens.compliance import ComplianceScreen, _OversightAction
from acc.tui.screens.comms import CommunicationsScreen
from acc.tui.screens.dashboard import DashboardScreen, _RefreshMessage
from acc.tui.screens.ecosystem import EcosystemScreen
from acc.tui.screens.infuse import InfuseScreen, _PublishMessage
from acc.tui.screens.performance import PerformanceScreen
from acc.tui.widgets.nav_bar import NavigateTo
from acc.tui.widgets.collective_tabs import CollectiveTabStrip, SwitchCollective

logger = logging.getLogger("acc.tui.app")

_DEFAULT_NATS_URL = "nats://localhost:4222"
_DEFAULT_COLLECTIVE_ID = "sol-01"
_CONNECT_RETRIES = 3
_RETRY_BASE_S = 2.0
_QUEUE_MAX = 50


class ConnectionErrorScreen(App):
    """Minimal error screen shown when NATS is unreachable."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Label(f"⚠  NATS connection failed: {self._message}", id="error-msg")
        yield Label("Set ACC_NATS_URL and restart acc-tui.", id="error-hint")


class ACCTUIApp(App):
    """ACC multi-screen terminal operator console."""

    TITLE = "ACC TUI"

    # External stylesheet — no inline CSS strings (REQ-TUI-005)
    CSS_PATH = Path(__file__).parent / "app.tcss"

    # All 6 biological screens (REQ-TUI-003)
    SCREENS = {
        "soma":        DashboardScreen,
        "nucleus":     InfuseScreen,
        "compliance":  ComplianceScreen,
        "comms":       CommunicationsScreen,
        "performance": PerformanceScreen,
        "ecosystem":   EcosystemScreen,
        # Legacy aliases so existing code using "dashboard"/"infuse" still works
        "dashboard":   DashboardScreen,
        "infuse":      InfuseScreen,
    }

    def __init__(
        self,
        nats_url: str = "",
        collective_id: str = "",
        collective_ids: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._nats_url = nats_url or os.environ.get("ACC_NATS_URL", _DEFAULT_NATS_URL)

        # Multi-collective resolution (REQ-TUI-006)
        if collective_ids:
            self._collective_ids = collective_ids
        else:
            env_ids = os.environ.get("ACC_COLLECTIVE_IDS", "")
            if env_ids:
                self._collective_ids = [c.strip() for c in env_ids.split(",") if c.strip()]
            else:
                single = collective_id or os.environ.get("ACC_COLLECTIVE_ID", _DEFAULT_COLLECTIVE_ID)
                self._collective_ids = [single]

        # Active collective index (for multi-collective tab strip — REQ-TUI-007)
        self._active_collective_idx: int = 0

        # One queue + observer per collective (REQ-TUI-006)
        self._queues: list[asyncio.Queue[CollectiveSnapshot]] = []
        self._observers: list[NATSObserver] = []
        self._drain_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]

        # Snapshots indexed by collective_id
        self._snapshots: dict[str, CollectiveSnapshot] = {}

        # WebBridge (REQ-TUI-041)
        self._web_port = int(os.environ.get("ACC_TUI_WEB_PORT", "0"))
        self._web_bridge_task: asyncio.Task | None = None  # type: ignore[type-arg]

        self._build_observers()

    def _build_observers(self) -> None:
        """Create one NATSObserver per collective_id."""
        for cid in self._collective_ids:
            q: asyncio.Queue[CollectiveSnapshot] = asyncio.Queue(maxsize=_QUEUE_MAX)
            obs = NATSObserver(
                nats_url=self._nats_url,
                collective_id=cid,
                update_queue=q,
            )
            self._queues.append(q)
            self._observers.append(obs)
            self._snapshots[cid] = CollectiveSnapshot(collective_id=cid)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        """Connect all NATS observers and start background tasks."""
        any_connected = False
        for obs in self._observers:
            connected = await self._connect_with_retry(obs)
            if connected:
                await obs.subscribe()
                any_connected = True
            else:
                logger.warning(
                    "app: could not connect observer for collective %s",
                    obs._collective_id,
                )

        if not any_connected:
            await self.action_quit()
            return

        # Start drain tasks for all observers
        for i, (obs, q) in enumerate(zip(self._observers, self._queues)):
            task = asyncio.create_task(
                self._drain_queue(obs._collective_id, q),
                name=f"drain-{obs._collective_id}",
            )
            self._drain_tasks.append(task)

        # Start WebBridge if configured (REQ-TUI-041)
        if self._web_port > 0:
            from acc.tui.web_bridge import WebBridge
            bridge = WebBridge(
                port=self._web_port,
                snapshot_getter=self._get_active_snapshot,
            )
            self._web_bridge_task = asyncio.create_task(
                bridge.serve(), name="web-bridge"
            )

        self.push_screen("soma")

        # Mount multi-collective tab strip when more than one collective (REQ-TUI-007)
        if len(self._collective_ids) > 1:
            tab_strip = CollectiveTabStrip(
                collective_ids=self._collective_ids,
                active_idx=self._active_collective_idx,
                id="collective-tabs",
            )
            self.mount(tab_strip)

    async def on_unmount(self) -> None:
        """Clean up all tasks and NATS connections."""
        for task in self._drain_tasks:
            task.cancel()
        if self._web_bridge_task is not None:
            self._web_bridge_task.cancel()
        for obs in self._observers:
            try:
                await obs.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # NATS connection with retry
    # ------------------------------------------------------------------

    async def _connect_with_retry(self, obs: NATSObserver) -> bool:
        """Attempt NATS connect with exponential backoff."""
        delay = _RETRY_BASE_S
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                await obs.connect()
                return True
            except Exception as exc:
                logger.warning(
                    "app: connect attempt %d/%d for %s failed: %s",
                    attempt, _CONNECT_RETRIES, obs._collective_id, exc,
                )
                if attempt < _CONNECT_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2
        return False

    # ------------------------------------------------------------------
    # Background queue drain
    # ------------------------------------------------------------------

    async def _drain_queue(
        self, collective_id: str, queue: asyncio.Queue
    ) -> None:
        """Drain update_queue and push snapshots into screens."""
        while True:
            try:
                snapshot = await queue.get()
                self._snapshots[collective_id] = snapshot
                # Only push to screens if this is the active collective
                if collective_id == self._active_collective_id:
                    self.call_from_thread(self._apply_snapshot, snapshot)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("drain_queue[%s]: %s", collective_id, exc)

    @property
    def _active_collective_id(self) -> str:
        return self._collective_ids[self._active_collective_idx]

    def _get_active_snapshot(self) -> dict:
        """Return the active CollectiveSnapshot as a dict for WebBridge."""
        import dataclasses
        snap = self._snapshots.get(self._active_collective_id)
        if snap is None:
            return {}
        try:
            return dataclasses.asdict(snap)
        except Exception:
            return {"collective_id": self._active_collective_id}

    def _apply_snapshot(self, snapshot: CollectiveSnapshot) -> None:
        """Push snapshot into all open screens."""
        _SNAPSHOT_SCREENS = [
            ("soma", DashboardScreen),
            ("dashboard", DashboardScreen),
            ("compliance", ComplianceScreen),
            ("comms", CommunicationsScreen),
            ("performance", PerformanceScreen),
            ("ecosystem", EcosystemScreen),
        ]
        for screen_name, screen_cls in _SNAPSHOT_SCREENS:
            try:
                scr = self.get_screen(screen_name)
                if isinstance(scr, screen_cls) and hasattr(scr, "snapshot"):
                    scr.snapshot = snapshot  # type: ignore[attr-defined]
            except Exception:
                pass

        # InfuseScreen uses apply_snapshot() (role audit history)
        try:
            infuse = self.get_screen("nucleus") or self.get_screen("infuse")
            if isinstance(infuse, InfuseScreen):
                infuse.apply_snapshot(snapshot)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Multi-collective tab navigation (REQ-TUI-007)
    # ------------------------------------------------------------------

    def switch_collective(self, idx: int) -> None:
        """Switch the active collective by index."""
        if 0 <= idx < len(self._collective_ids):
            self._active_collective_idx = idx
            cid = self._collective_ids[idx]
            snap = self._snapshots.get(cid)
            if snap:
                self._apply_snapshot(snap)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def on_switch_collective(self, event: SwitchCollective) -> None:
        """Switch active collective from tab strip press (REQ-TUI-007)."""
        self.switch_collective(event.collective_idx)
        # Update tab strip active state
        try:
            strip = self.query_one("#collective-tabs", CollectiveTabStrip)
            strip.set_active(event.collective_idx)
        except Exception:
            pass

    def on_navigate_to(self, event: NavigateTo) -> None:
        """Handle NavigateTo from NavigationBar on any screen."""
        target = event.screen_name
        try:
            self.switch_screen(target)
        except Exception as exc:
            logger.warning("app: switch_screen(%s) failed: %s", target, exc)

    async def on__publish_message(self, message: _PublishMessage) -> None:
        """Forward a ROLE_UPDATE publish request to the active NATSObserver."""
        obs = self._observers[self._active_collective_idx] if self._observers else None
        if obs is None:
            return
        try:
            await obs.publish(message.subject, message.payload)
            logger.info("app: published to %s", message.subject)
        except Exception as exc:
            logger.warning("app: NATS publish failed: %s", exc)

    async def on__refresh_message(self, message: _RefreshMessage) -> None:
        """Re-subscribe to NATS on user request."""
        for obs in self._observers:
            try:
                await obs.subscribe()
            except Exception as exc:
                logger.warning("app: re-subscribe failed for %s: %s", obs._collective_id, exc)

    async def on__oversight_action(self, message: _OversightAction) -> None:
        """Publish oversight approve/reject to NATS (REQ-TUI-026)."""
        obs = self._observers[self._active_collective_idx] if self._observers else None
        if obs is None:
            return
        cid = self._active_collective_id
        subject = f"acc.{cid}.oversight.action"
        payload = {
            "signal_type": "OVERSIGHT_ACTION",
            "action": message.action,
            "collective_id": cid,
        }
        try:
            await obs.publish(subject, payload)
        except Exception as exc:
            logger.warning("app: oversight publish failed: %s", exc)

    # ------------------------------------------------------------------
    # Public accessor (for WebBridge and tests)
    # ------------------------------------------------------------------

    @property
    def nats_observer(self) -> NATSObserver:
        """Return the primary (first) NATSObserver — backward compat."""
        return self._observers[0]

    @property
    def collective_ids(self) -> list[str]:
        return list(self._collective_ids)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch acc-tui."""
    logging.basicConfig(level=logging.WARNING)
    ACCTUIApp().run()


if __name__ == "__main__":
    main()
