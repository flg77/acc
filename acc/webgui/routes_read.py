"""Read-only REST endpoints for acc-webgui (proposal acc-webgui PR-1).

These mirror the data the TUI's dormant WebBridge exposed (`GET /` →
`CollectiveSnapshot`), but as a proper, documented FastAPI surface.
All endpoints are read-only; the action endpoints (infuse / prompt /
oversight) ship in PR-3, the tracing endpoints in PR-4.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from acc.webgui.auth import require_viewer
from acc.webgui.deps import get_hub
from acc.webgui.observers import ObserverHub

router = APIRouter()


@router.get("/health", tags=["meta"])
def health(hub: ObserverHub = Depends(get_hub)) -> dict:
    """Liveness probe — intentionally unauthenticated (proposal §6)."""
    return {
        "status": "ok",
        "collective_ids": hub.collective_ids(),
        "ts": round(time.time(), 4),
    }


@router.get("/api/collectives", tags=["read"],
            dependencies=[Depends(require_viewer)])
def list_collectives(hub: ObserverHub = Depends(get_hub)) -> dict:
    """List every collective this acc-webgui instance observes."""
    return {"collectives": hub.collective_ids()}


@router.get("/api/snapshot/{collective_id}", tags=["read"],
            dependencies=[Depends(require_viewer)])
def get_snapshot(
    collective_id: str, hub: ObserverHub = Depends(get_hub),
) -> dict:
    """Return the most recent `CollectiveSnapshot` for *collective_id*.

    The live stream is the WebSocket `/ws/{collective_id}`; this REST
    endpoint is the point-in-time fetch (initial page load, polling
    fallback, automation).
    """
    if collective_id not in hub.collective_ids():
        raise HTTPException(status_code=404,
                            detail=f"collective {collective_id!r} not observed")
    snap = hub.latest(collective_id)
    if snap is None:
        # Observed, but no signal received yet — not an error.
        return {"collective_id": collective_id, "snapshot": None,
                "note": "no snapshot received yet"}
    return {"collective_id": collective_id, "snapshot": snap}
