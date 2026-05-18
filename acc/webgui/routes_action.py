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


class OversightRequest(BaseModel):
    collective_id: str
    oversight_id: str
    decision: str = Field(..., pattern="^(APPROVE|REJECT)$")
    reason: str = ""


class TestLLMRequest(BaseModel):
    base_url: str


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
    )
    task_id = await channel.send(
        prompt=req.content,
        target_role=req.target_role,
        target_agent_id=req.target_agent_id,
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
        "reason": req.reason,
        "ts": time.time(),
        "collective_id": req.collective_id,
    }
    await obs.publish(
        subject_oversight_decision(req.collective_id, req.oversight_id), payload,
    )
    return {"status": "published", "decision": req.decision}


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
