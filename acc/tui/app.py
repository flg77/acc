"""ACC TUI — ACCTUIApp: main Textual application.

Lifecycle:
    1. Read ACC_NATS_URL and ACC_COLLECTIVE_ID from environment.
    2. Connect NATSObserver to NATS (3 retries, exponential backoff).
    3. Subscribe to acc.{collective_id}.> on NATS.
    4. Start _drain_queue() background task.
    5. Push DashboardScreen as the initial screen.
    6. Handle Tab↔Dashboard/Infuse screen switch.
    7. On shutdown: close NATS connection.

Env vars:
    ACC_NATS_URL        NATS URL (default: nats://localhost:4222)
    ACC_COLLECTIVE_ID   Collective (default: sol-01)
"""

from __future__ import annotations

import asyncio
import logging
import os

from textual.app import App, ComposeResult
from textual.widgets import Label

from acc.tui.client import NATSObserver
from acc.tui.models import CollectiveSnapshot
from acc.tui.screens.dashboard import DashboardScreen, _RefreshMessage
from acc.tui.screens.infuse import InfuseScreen, _PublishMessage

logger = logging.getLogger("acc.tui.app")

_DEFAULT_NATS_URL = "nats://localhost:4222"
_DEFAULT_COLLECTIVE_ID = "sol-01"
_CONNECT_RETRIES = 3
_RETRY_BASE_S = 2.0
_QUEUE_MAX = 50


class ConnectionErrorScreen(App):
    """Minimal error screen shown when NATS is unreachable (REQ-OBS-007)."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Label(f"⚠  NATS connection failed: {self._message}", id="error-msg")
        yield Label("Set ACC_NATS_URL and restart acc-tui.", id="error-hint")


class ACCTUIApp(App):
    """ACC terminal dashboard and role infusion application."""

    TITLE = "ACC TUI"
    CSS = """
    Screen {
        background: $surface;
    }
    #screen-title, #dashboard-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    .field-label {
        width: auto;
        padding: 0 1 0 0;
    }
    .section-label {
        color: $text-muted;
        margin-top: 1;
    }
    .input-short {
        width: 16;
    }
    .textarea-tall {
        height: 6;
    }
    .textarea-medium {
        height: 4;
    }
    .status-bar {
        color: $warning;
        padding: 0 1;
    }
    .panel-label {
        text-style: bold;
        color: $accent;
    }
    .info-panel {
        border: solid $primary;
        padding: 0 1;
        margin: 0 0 1 0;
        min-height: 6;
    }
    #agents-panel {
        width: 50%;
        min-width: 30;
    }
    #right-panels {
        width: 50%;
        padding: 0 1;
    }
    #last-update {
        color: $text-muted;
        padding: 0 1;
    }
    .footer-bar {
        color: $text-muted;
    }
    """

    SCREENS = {
        "dashboard": DashboardScreen,
        "infuse": InfuseScreen,
    }

    def __init__(self, nats_url: str = "", collective_id: str = "") -> None:
        super().__init__()
        self._nats_url = nats_url or os.environ.get("ACC_NATS_URL", _DEFAULT_NATS_URL)
        self._collective_id = collective_id or os.environ.get("ACC_COLLECTIVE_ID", _DEFAULT_COLLECTIVE_ID)
        self._queue: asyncio.Queue[CollectiveSnapshot] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self.nats_observer = NATSObserver(
            nats_url=self._nats_url,
            collective_id=self._collective_id,
            update_queue=self._queue,
        )
        self._drain_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        """Connect NATS and start background drain task."""
        connected = await self._connect_with_retry()
        if not connected:
            # Connection error — already logged; graceful exit
            await self.action_quit()
            return

        await self.nats_observer.subscribe()
        self._drain_task = asyncio.create_task(self._drain_queue())
        self.push_screen("dashboard")

    async def on_unmount(self) -> None:
        """Clean up NATS connection on exit."""
        if self._drain_task is not None:
            self._drain_task.cancel()
        try:
            await self.nats_observer.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # NATS connection with retry (REQ-OBS-007)
    # ------------------------------------------------------------------

    async def _connect_with_retry(self) -> bool:
        """Attempt to connect to NATS with exponential backoff.

        Returns:
            True if connected, False if all retries exhausted.
        """
        delay = _RETRY_BASE_S
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                await self.nats_observer.connect()
                return True
            except Exception as exc:
                logger.warning(
                    "nats_observer: connection attempt %d/%d failed: %s",
                    attempt, _CONNECT_RETRIES, exc,
                )
                if attempt < _CONNECT_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2
        return False

    # ------------------------------------------------------------------
    # Background queue drain (REQ-REACT-001)
    # ------------------------------------------------------------------

    async def _drain_queue(self) -> None:
        """Drain update_queue and push snapshots into Textual's reactive system."""
        while True:
            try:
                snapshot = await self._queue.get()
                self.call_from_thread(self._apply_snapshot, snapshot)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("drain_queue: error: %s", exc)

    def _apply_snapshot(self, snapshot: CollectiveSnapshot) -> None:
        """Push snapshot into Textual reactive variables on all active screens."""
        # Update DashboardScreen
        try:
            dash = self.get_screen("dashboard")
            if isinstance(dash, DashboardScreen):
                dash.snapshot = snapshot
        except Exception:
            pass

        # Update InfuseScreen history
        try:
            infuse = self.get_screen("infuse")
            if isinstance(infuse, InfuseScreen):
                infuse.apply_snapshot(snapshot)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    async def on__publish_message(self, message: _PublishMessage) -> None:
        """Forward a ROLE_UPDATE publish request to NATSObserver."""
        try:
            await self.nats_observer.publish(message.subject, message.payload)
            logger.info("app: published to %s", message.subject)
        except Exception as exc:
            logger.warning("app: NATS publish failed: %s", exc)

    async def on__refresh_message(self, message: _RefreshMessage) -> None:
        """Re-subscribe to NATS on user request (r key)."""
        try:
            await self.nats_observer.subscribe()
            logger.info("app: re-subscribed to NATS")
        except Exception as exc:
            logger.warning("app: re-subscribe failed: %s", exc)


# ---------------------------------------------------------------------------
# Entry point (REQ-TUI-001)
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch acc-tui."""
    logging.basicConfig(level=logging.WARNING)
    ACCTUIApp().run()


if __name__ == "__main__":
    main()
