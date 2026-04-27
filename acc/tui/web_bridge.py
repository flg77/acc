"""ACC TUI WebBridge — minimal asyncio HTTP server for CollectiveSnapshot JSON export.

Serves two routes (REQ-TUI-041, REQ-TUI-042):
  GET /        → current CollectiveSnapshot serialised as JSON
  GET /health  → {"status": "ok", "collective_ids": [...]}

Implemented as a raw asyncio TCP server (no external framework) to keep the
TUI container dependency-free.  A future WebUI polls GET / on a configurable
cadence to consume live snapshot data (REQ-TUI-041).

If the configured port is already in use, the WebBridge logs a warning and
returns without raising (REQ-TUI-043), so the TUI starts normally.

Float serialisation: 4 decimal places (REQ-TUI-044).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger("acc.tui.web_bridge")

_HTTP_200 = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"
_HTTP_404 = b"HTTP/1.0 404 Not Found\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"error\":\"not found\"}"
_HTTP_405 = b"HTTP/1.0 405 Method Not Allowed\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"error\":\"method not allowed\"}"


def _default_serialiser(obj: Any) -> Any:
    """JSON default — converts floats to 4 d.p., datetimes to ISO-8601 (REQ-TUI-044)."""
    import datetime
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


class WebBridge:
    """Minimal asyncio TCP HTTP server exposing CollectiveSnapshot as JSON.

    Args:
        port: TCP port to listen on (REQ-TUI-041).
        snapshot_getter: Callable returning the current snapshot as a dict.
        collective_ids: List of collective IDs (for /health response).
        host: Bind address (default ``"127.0.0.1"`` — localhost only).
    """

    def __init__(
        self,
        port: int,
        snapshot_getter: Callable[[], dict],
        collective_ids: list[str] | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        self._port = port
        self._host = host
        self._snapshot_getter = snapshot_getter
        self._collective_ids = collective_ids or []

    async def serve(self) -> None:
        """Start the HTTP server, gracefully handling port-in-use (REQ-TUI-043)."""
        try:
            server = await asyncio.start_server(
                self._handle_connection, self._host, self._port
            )
        except OSError as exc:
            logger.warning(
                "web_bridge: port %d already in use — WebBridge disabled: %s",
                self._port, exc,
            )
            return

        logger.info(
            "web_bridge: listening on http://%s:%d/", self._host, self._port
        )
        async with server:
            await server.serve_forever()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single HTTP connection."""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            writer.close()
            return

        parts = request_line.decode("utf-8", errors="replace").split()
        if len(parts) < 2:
            writer.close()
            return

        method, path = parts[0], parts[1].split("?")[0]

        # Consume remaining headers (ignore them)
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                if line in (b"\r\n", b"\n", b""):
                    break
        except asyncio.TimeoutError:
            pass

        if method != "GET":
            writer.write(_HTTP_405)
            await writer.drain()
            writer.close()
            return

        if path == "/health":
            body = json.dumps(
                {
                    "status": "ok",
                    "collective_ids": self._collective_ids,
                    "ts": round(time.time(), 4),
                },
                default=_default_serialiser,
            ).encode("utf-8")
            writer.write(_HTTP_200 + body)

        elif path == "/":
            try:
                data = self._snapshot_getter()
                body = json.dumps(data, default=_default_serialiser).encode("utf-8")
            except Exception as exc:
                logger.warning("web_bridge: snapshot serialisation error: %s", exc)
                body = b'{"error": "snapshot unavailable"}'
            writer.write(_HTTP_200 + body)

        else:
            writer.write(_HTTP_404)

        await writer.drain()
        writer.close()
