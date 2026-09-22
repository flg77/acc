"""Read-only REST endpoints for acc-webgui (proposal acc-webgui PR-1).

These mirror the data the TUI's dormant WebBridge exposed (`GET /` →
`CollectiveSnapshot`), but as a proper, documented FastAPI surface.
All endpoints are read-only; the action endpoints (infuse / prompt /
oversight) ship in PR-3, the tracing endpoints in PR-4.
"""

from __future__ import annotations

import dataclasses
import time

from fastapi import APIRouter, Depends, HTTPException

from acc.webgui.auth import Principal, require_viewer
from acc.webgui.deps import get_hub
from acc.webgui.observers import ObserverHub

router = APIRouter()


def _web_principal(principal):
    """The web session's user on the shared identity ladder (HG-40.1b item 4):
    ``webgui:<user>`` is what its prompts are attributed to, so the same
    string is what its views are filtered by."""
    from acc.identity import from_web  # noqa: PLC0415
    return from_web(principal.user, principal.role)


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


@router.get("/api/environment", tags=["read"], dependencies=[Depends(require_viewer)])
def environment_info() -> dict:
    """Where this WebGUI runs, and what can be changed from here.

    One answer for every screen (``acc.deploy.environment``): the kind of
    place, the deployment it belongs to, and per capability either
    *available* or the sentence that says why not.  The SPA renders from it —
    a control whose capability is unavailable is hidden or disabled with
    that sentence, never offered and left to fail.
    """
    import os  # noqa: PLC0415

    from acc.deploy import environment  # noqa: PLC0415

    import acc  # noqa: PLC0415

    info = environment().to_dict()
    info["runtime"] = acc.__version__
    # The two trace stores are files: where one IS mounted the screen works,
    # whatever kind of place this is.
    for cap, env_var, default in (
        ("trace.audit", "ACC_AUDIT_FILE_PATH", "/app/data/audit"),
        ("trace.episodes", "ACC_LANCEDB_PATH", "/app/data/lancedb"),
    ):
        if os.path.isdir(os.environ.get(env_var, default)):
            info["capabilities"][cap] = {"available": True, "reason": ""}
    return info


# The declared read is three Kubernetes API calls in a cluster; the page polls,
# so it is answered from the last read for a few seconds.  ``read_at`` says how old.
_AGENTSET_TTL_S = 10.0
_agentset_cache: "tuple[float, object] | None" = None


def _declared_agentset():
    global _agentset_cache  # noqa: PLW0603 — a process-wide read-through cache
    from acc.deployment import agentset  # noqa: PLC0415

    now = time.time()
    if _agentset_cache is None or now - _agentset_cache[0] > _AGENTSET_TTL_S:
        _agentset_cache = (now, agentset())
    return _agentset_cache


@router.get("/api/agentset", tags=["read"], dependencies=[Depends(require_viewer)])
def agentset_info(collective: str = "", hub: ObserverHub = Depends(get_hub)) -> dict:
    """The agentset of this deployment — declared beside running.

    *Declared* is ``acc.deployment.agentset``: ``collective.yaml`` in a
    checkout, the namespace's ``AgentCollective`` in a cluster pod.  *Running*
    is what the bus has seen for *collective* (``acc.deployment.compare``, the
    rule the TUI shares).  A refused read arrives as ``errors``, never as an
    empty list.  A plain ``def``: the read blocks, so it runs in the threadpool.
    """
    from acc.deployment import compare  # noqa: PLC0415

    read_at, declared = _declared_agentset()
    cid = collective or (hub.collective_ids() or [""])[0]
    snapshot = hub.latest(cid) if cid else None
    running = None
    if snapshot is not None:
        running = [
            (str(a.get("role") or ""), str(a.get("llm_model") or ""))
            for a in (snapshot.get("agents") or {}).values()
        ]
    rows, undeclared = compare(declared, running)
    body = declared.to_dict()
    body.update({
        "collective": cid,
        "read_at": round(read_at, 3),
        "bus": running is not None,
        "rows": [dataclasses.asdict(r) for r in rows],
        "undeclared": undeclared,
    })
    return body


@router.get("/api/whoami", tags=["read"])
def whoami(principal: Principal = Depends(require_viewer)) -> dict:
    """Who the signed-in person is and at which tier — the environment bar
    shows it, so a missing control is explained by the tier, not left to guess."""
    return {"user": principal.user, "role": principal.role}


@router.get("/api/tracing", tags=["read"], dependencies=[Depends(require_viewer)])
def tracing_info() -> dict:
    """Where this deployment's traces go and what they carry
    (``acc.deployment.tracing``) — read-only.  In a cluster that is
    ``AgentCorpus.spec.observability``, asked with the pod's ServiceAccount;
    a refusal arrives as ``errors``, not as an empty answer.  A plain ``def``:
    the read blocks, so it runs in the threadpool."""
    from acc.deployment import tracing  # noqa: PLC0415

    return tracing().to_dict()


@router.get("/api/deploy", tags=["read"], dependencies=[Depends(require_viewer)])
def deploy_info() -> dict:
    """Where this WebGUI runs (proposal 056 §4.1).

    ``cluster`` is true in a pod the operator made — the screens use it to
    hide or explain the checkout-only affordances (authoring roles on disk,
    staging a package install, adding a workspace catalog).
    """
    import os  # noqa: PLC0415

    from acc.deploy import is_cluster  # noqa: PLC0415

    return {
        "cluster": is_cluster(),
        "deploy_mode": os.environ.get("ACC_DEPLOY_MODE", ""),
        "corpus_name": os.environ.get("ACC_CORPUS_NAME", ""),
    }


@router.get("/api/board/{collective_id}", tags=["read"],
            dependencies=[Depends(require_viewer)])
def board(collective_id: str, hub: ObserverHub = Depends(get_hub),
          principal: Principal = Depends(require_viewer)) -> dict:
    """The work board (`20260903-work-board-webgui`): the same pure
    projection the TUI Board renders, over the hub's latest snapshot.

    Columns are the runtime's states (QUEUED · RUNNING · BLOCKED · DONE ·
    FAILED); nothing here is editable — interventions go through
    ``POST /api/board/control`` and the arbiter moves the card."""
    from dataclasses import asdict  # noqa: PLC0415

    from acc.work_board import columns, project_board, visible_to  # noqa: PLC0415

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
    # HG-40.1b item 4 -- a viewer sees the items they asked for; an operator
    # sees the collective.
    viewer = _web_principal(principal)
    items = visible_to(items, viewer.attribution(), viewer.tier)
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
    principal: Principal = Depends(require_viewer),
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
    from acc.work_board import filter_snapshot  # noqa: PLC0415
    viewer = _web_principal(principal)
    return {"collective_id": collective_id,
            "snapshot": filter_snapshot(snap, viewer.attribution(), viewer.tier)}
