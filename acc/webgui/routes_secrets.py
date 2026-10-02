"""Credentials through the web interface -- write-only (UX-07).

``20260926-secrets-from-kubernetes-and-a-live-broker`` Phase 3. An operator
introduces or replaces a credential by name; the value goes into the secret
source the agents read at call time (:mod:`acc.secret_writer`) and nowhere else.

What this surface never does:

* **return a value** -- the list is names, which models use them, and how many
  agents see each one in their mount (from their heartbeats);
* **log a value** -- the log line names the person, the credential and the
  target;
* **echo a value in an error** -- the body is parsed here rather than by a
  pydantic model, because a validation error quotes its input.

Viewers can see the names. Only operators can write.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from acc import secret_writer
from acc.webgui.auth import Principal, require_operator, require_viewer
from acc.webgui.deps import get_hub
from acc.webgui.observers import ObserverHub

logger = logging.getLogger("acc.webgui.secrets")

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


def _used_by() -> dict[str, list[str]]:
    """Credential name -> the models in the registry that name it."""
    from acc.models import load_models  # noqa: PLC0415

    out: dict[str, list[str]] = {}
    try:
        models = load_models()
    except Exception:  # noqa: BLE001 -- a broken registry still lets a key be set
        logger.warning("secrets: the model registry could not be read", exc_info=True)
        return out
    for entry in models:
        if entry.api_key_env:
            out.setdefault(entry.api_key_env, []).append(entry.model_id)
    return out


def _agents(hub: ObserverHub) -> list[dict]:
    rows = []
    for cid in hub.collective_ids():
        snapshot = hub.latest(cid) or {}
        for agent_id, agent in (snapshot.get("agents") or {}).items():
            rows.append({
                "agent_id": agent_id,
                "role": str(agent.get("role") or ""),
                "source": str(agent.get("secret_source") or ""),
                "names": [str(n) for n in (agent.get("secret_names") or [])],
            })
    return rows


@router.get("")
def list_secrets(
    hub: ObserverHub = Depends(get_hub), principal: Principal = Depends(require_viewer),
) -> dict[str, Any]:
    """Names, never values: where a write goes, and who sees what."""
    used_by = _used_by()
    agents = _agents(hub)
    mounted = [a for a in agents if a["source"] == "mounted"]
    names = set(used_by) | {n for a in mounted for n in a["names"]}
    rows = [{
        "name": name,
        "used_by": sorted(used_by.get(name, [])),
        "seen_by": sum(1 for a in mounted if name in a["names"]),
    } for name in sorted(names)]
    return {
        "target": secret_writer.target().to_dict(),
        "rows": rows,
        # Agents that report no source predate this field: counted apart, not as env.
        "agents": {
            "mounted": len(mounted),
            "env": sum(1 for a in agents if a["source"] == "env"),
            "unknown": sum(1 for a in agents if not a["source"]),
        },
    }


@router.post("/{name}")
async def set_secret(
    name: str, request: Request, principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Introduce or replace credential *name*. The body is ``{"value": "..."}``."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="the body is not JSON") from None
    value = body.get("value") if isinstance(body, dict) else None
    try:
        where = secret_writer.write(name, value)
    except secret_writer.SecretWriteError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    logger.info("secrets: %s wrote %s (%s)", principal.user, name, where.where)
    return {
        "name": name,
        "target": where.to_dict(),
        "written_by": principal.user,
        "at": time.time(),
        "note": ("the agents read it on their next call; a cluster's kubelet "
                 "refreshes the mount within about a minute"
                 if where.kind == secret_writer.SECRET else
                 "the agents read it on their next call"),
    }
