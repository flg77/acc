"""Action REST endpoints for acc-webgui (proposal acc-webgui PR-3).

The web UI's write actions are exactly the TUI's — no new authority
over the collective: infuse a role (ROLE_UPDATE), send a prompt
(TASK_ASSIGN), record an oversight decision (OVERSIGHT_DECISION), test
an LLM endpoint.  Each publishes through the same `NATSObserver` the
TUI uses.

Auth (PR-5) gates these behind the ``operator`` role and stamps the
authenticated human identity onto the payloads; until then the
operator id defaults to ``webgui:operator``.
"""

from __future__ import annotations

import time

from acc import identity as _identity

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from acc.webgui.auth import Principal, require_operator
from acc.webgui.deps import get_hub
from acc.webgui.observers import ObserverHub

router = APIRouter(prefix="/api", tags=["action"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class InfuseRequest(BaseModel):
    collective_id: str
    role_definition: dict = Field(..., description="The merged role definition dict")


class PromptRequest(BaseModel):
    collective_id: str
    target_role: str
    content: str
    target_agent_id: str | None = None
    timeout_s: float = 180.0
    session_id: str | None = None
    """The conversation this prompt continues (RP-02).

    The channel has always accepted this -- ``WebPromptChannel`` inherits
    ``TUIPromptChannel.send()`` -- but the route never passed one, so every web
    prompt was a first turn while the TUI and Slack could hold a thread.

    Only the id travels.  Prior turns are replayed server-side from the durable
    tracelog, so a client cannot fabricate history it never had.  Omitting it
    degrades to a ``task_id``-scoped session (one turn), never to a thread
    belonging to someone else."""
    operating_mode: str = "AUTO"
    """Per-request operating mode (PR-L D-003).

    Same defect class as ``session_id`` was: the channel has accepted it all
    along and the route never passed one, so the web surface was pinned to
    AUTO while the TUI could choose.

    Not validated here on purpose. ``acc.operating_modes.normalise`` coerces an
    unknown value to AUTO agent-side, which fails toward the *stricter* gate;
    rejecting here would move the same decision somewhere with less context."""
    workspace: str | None = None
    """The trusted workspace project the agent resolves fs_read/fs_write under
    (PR-U2b), relative to the ``/workspace`` mount.

    Client-supplied and remote, unlike the TUI's, but not trusted: ``agent.py``
    rejects absolute paths, ``..`` and ``/..``, and
    ``workspace.resolve_in_workspace`` enforces symlink-collapsed containment.
    This route already requires an operator principal."""


class OversightRequest(BaseModel):
    collective_id: str
    oversight_id: str
    decision: str = Field(..., pattern="^(APPROVE|REJECT)$")
    reason: str = ""


class TestLLMRequest(BaseModel):
    base_url: str


class BoardControlRequest(BaseModel):
    """`20260903-work-board-webgui` — one intervention on one card."""

    collective_id: str
    kind: str = Field(..., pattern="^(plan_step|task)$")
    action: str = Field(..., pattern="^(cancel|retry|reassign)$")
    plan_id: str = ""
    step_id: str = ""
    task_id: str = ""
    role: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_observer(hub: ObserverHub, collective_id: str):
    obs = hub.observer(collective_id)
    if obs is None:
        raise HTTPException(status_code=404,
                            detail=f"collective {collective_id!r} not observed")
    return obs


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/infuse")
async def infuse_role(
    req: InfuseRequest,
    hub: ObserverHub = Depends(get_hub),
    principal: Principal = Depends(require_operator),
) -> dict:
    """Publish a ROLE_UPDATE — the Nucleus/Infuse screen's Apply action."""
    from acc.signals import subject_role_update  # noqa: PLC0415

    obs = _require_observer(hub, req.collective_id)
    payload = {
        "signal_type": "ROLE_UPDATE",
        "agent_id": "",
        "collective_id": req.collective_id,
        "ts": time.time(),
        "approver_id": "",
        "signature": "",
        "role_definition": req.role_definition,
        "from_operator": principal.user,
    }
    await obs.publish(subject_role_update(req.collective_id), payload)
    return {"status": "published", "note": "awaiting arbiter approval"}


@router.post("/prompt")
async def send_prompt(
    req: PromptRequest,
    hub: ObserverHub = Depends(get_hub),
    principal: Principal = Depends(require_operator),
) -> dict:
    """Send a TASK_ASSIGN and await the TASK_COMPLETE reply.

    Progress streams live on the WebSocket; this endpoint long-polls
    the final reply so simple clients get a single response.
    """
    import asyncio  # noqa: PLC0415

    from acc.channels.webgui import WebPromptChannel  # noqa: PLC0415

    obs = _require_observer(hub, req.collective_id)
    channel = WebPromptChannel(
        obs, collective_id=req.collective_id,
        from_agent=f"webgui:{principal.user}",
        user=principal.user, role=principal.role,
    )
    task_id = await channel.send(
        prompt=req.content,
        target_role=req.target_role,
        target_agent_id=req.target_agent_id,
        session_id=req.session_id,
        operating_mode=req.operating_mode,
        workspace=req.workspace,
    )
    try:
        reply = await channel.receive(task_id, timeout=req.timeout_s)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504,
                            detail=f"no reply within {req.timeout_s}s")
    finally:
        await channel.close()
    return {
        "task_id": task_id,
        # Echoed so a client that did not name a thread can adopt the one it
        # was given, and keep the conversation going without inventing an id.
        "session_id": req.session_id or task_id,
        "agent_id": reply.agent_id,
        "output": reply.output,
        "blocked": reply.blocked,
        "block_reason": reply.block_reason,
        "episode_id": reply.episode_id,
        "latency_ms": reply.latency_ms,
        "invocations": reply.invocations,
    }


@router.post("/oversight")
async def oversight_decision(
    req: OversightRequest,
    hub: ObserverHub = Depends(get_hub),
    principal: Principal = Depends(require_operator),
) -> dict:
    """Publish an OVERSIGHT_DECISION — the Compliance screen's approve/reject."""
    from acc.signals import subject_oversight_decision  # noqa: PLC0415

    obs = _require_observer(hub, req.collective_id)
    payload = {
        "signal_type": "OVERSIGHT_DECISION",
        "oversight_id": req.oversight_id,
        "decision": req.decision,
        "approver_id": f"webgui:{principal.user}",
        # Phase 2 of the hub scope: a hub promotion needs an operator-tier
        # approver; the web session's role maps onto the shared tiers.
        "approver_tier": _identity.from_web(principal.user, principal.role).tier,
        "reason": req.reason,
        "ts": time.time(),
        "collective_id": req.collective_id,
    }
    await obs.publish(
        subject_oversight_decision(req.collective_id, req.oversight_id), payload,
    )
    return {"status": "published", "decision": req.decision}


@router.post("/board/control")
async def board_control(
    req: BoardControlRequest,
    hub: ObserverHub = Depends(get_hub),
    principal: Principal = Depends(require_operator),
) -> dict:
    """Cancel / retry / reassign a plan step (``PLAN_STEP_CONTROL``, applied by
    the arbiter) or cancel a single task (``TASK_CANCEL``).  The board holds
    no state: the response says *published*, and the card moves when the
    arbiter re-broadcasts.  The principal is stamped as ``actor`` — the
    WebGUI is the surface where an intervention can be attributed."""
    from acc.signals import (  # noqa: PLC0415
        SIG_PLAN_STEP_CONTROL,
        SIG_TASK_CANCEL,
        subject_plan_control,
        subject_task_cancel,
    )

    obs = _require_observer(hub, req.collective_id)
    actor = f"webgui:{principal.user}"
    if req.kind == "plan_step":
        if not (req.plan_id and req.step_id):
            raise HTTPException(status_code=400, detail="plan_id and step_id are required")
        if req.action == "reassign" and not req.role:
            raise HTTPException(status_code=400, detail="reassign needs a role")
        payload = {
            "signal_type": SIG_PLAN_STEP_CONTROL,
            "collective_id": req.collective_id,
            "plan_id": req.plan_id,
            "step_id": req.step_id,
            "action": req.action,
            "role": req.role,
            "reason": req.reason,
            "actor": actor,
            "ts": time.time(),
        }
        await obs.publish(subject_plan_control(req.collective_id), payload)
        return {"status": "published", "signal": SIG_PLAN_STEP_CONTROL, "actor": actor}
    # kind == task: only cancel makes sense for a single prompt task
    if req.action != "cancel":
        raise HTTPException(status_code=400, detail="a task can only be cancelled")
    if not req.task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    payload = {
        "signal_type": SIG_TASK_CANCEL,
        "collective_id": req.collective_id,
        "task_id": req.task_id,
        "reason": req.reason or f"cancelled by {actor}",
        "actor": actor,
        "ts": time.time(),
    }
    await obs.publish(subject_task_cancel(req.collective_id), payload)
    return {"status": "published", "signal": SIG_TASK_CANCEL, "actor": actor}


@router.post("/test-llm")
async def test_llm(
    req: TestLLMRequest,
    principal: Principal = Depends(require_operator),
) -> dict:
    """Probe an LLM endpoint — the Configuration screen's test-connection."""
    import httpx  # noqa: PLC0415

    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(req.base_url)
        return {
            "reachable": True,
            "status_code": resp.status_code,
            "latency_ms": round((time.time() - started) * 1000, 1),
        }
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}
