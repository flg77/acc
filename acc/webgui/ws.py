"""WebSocket endpoint for acc-webgui — live CollectiveSnapshot push.

`GET /ws/{collective_id}` upgrades to a WebSocket; the client receives
the current `CollectiveSnapshot` immediately, then every update the
`ObserverHub` fans out as the collective runs.  This is the live data
path; `/api/snapshot/{cid}` is the point-in-time REST fallback.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from acc.webgui.auth import authenticate_websocket
from acc.webgui.observers import ObserverHub

logger = logging.getLogger("acc.webgui.ws")

router = APIRouter()


@router.websocket("/ws/{collective_id}")
async def collective_ws(websocket: WebSocket, collective_id: str) -> None:
    """Stream live `CollectiveSnapshot` updates for one collective."""
    hub: ObserverHub = websocket.app.state.hub

    # Authenticate the upgrade BEFORE accepting.  Browsers cannot set an
    # Authorization header on a WebSocket, so token/oidc/htpasswd modes
    # carry it as a ?token= query param; oauth-proxy/mtls modes use the
    # headers the front layer injects on the upgrade request.  Viewer
    # role suffices — the WS streams read-only snapshots.
    cfg = websocket.app.state.auth_config
    principal = authenticate_websocket(websocket, cfg)
    if principal is None:
        await websocket.close(code=1008)  # policy violation
        return

    await websocket.accept()

    if not hub.register_ws(collective_id, websocket):
        await websocket.send_json({
            "error": f"collective {collective_id!r} not observed",
        })
        await websocket.close(code=1008)  # policy violation
        return

    try:
        # Send the latest snapshot immediately so the client renders
        # without waiting for the next collective signal.
        latest = hub.latest(collective_id)
        if latest is not None:
            await websocket.send_json(latest)
        # The hub broadcasts subsequent updates; just keep the socket
        # open and drain any client pings until it disconnects.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("webgui: websocket error (%s)", collective_id)
    finally:
        hub.unregister_ws(collective_id, websocket)
