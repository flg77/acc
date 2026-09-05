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


@router.get("/api/board/{collective_id}", tags=["read"],
            dependencies=[Depends(require_viewer)])
def board(collective_id: str, hub: ObserverHub = Depends(get_hub)) -> dict:
    """The work board (`20260903-work-board-webgui`): the same pure
    projection the TUI Board renders, over the hub's latest snapshot.

    Columns are the runtime's states (QUEUED · RUNNING · BLOCKED · DONE ·
    FAILED); nothing here is editable — interventions go through
    ``POST /api/board/control`` and the arbiter moves the card."""
    from dataclasses import asdict  # noqa: PLC0415

    from acc.work_board import columns, project_board  # noqa: PLC0415

    if hub.observer(collective_id) is None:
        raise HTTPException(status_code=404,
                            detail=f"collective {collective_id!r} not observed")
    snap = hub.latest(collective_id) or {}
    items = project_board(
        active_plans=snap.get("active_plans"),
        cluster_topology=snap.get("cluster_topology"),
        oversight_pending_items=snap.get("oversight_pending_items"),
        oversight_recent_items=snap.get("oversight_recent_items"),
        assistant_outcomes=snap.get("assistant_outcomes"),
        signal_flow_log=snap.get("signal_flow_log"),
    )
    return {
        "collective_id": collective_id,
        "generated_ts": time.time(),
        "columns": [
            {"status": status,
             "items": [{**asdict(it), "can_cancel": it.can_cancel,
                        "can_retry": it.can_retry, "can_reassign": it.can_reassign}
                       for it in bucket]}
            for status, bucket in columns(items)
        ],
    }


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
