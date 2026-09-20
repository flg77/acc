"""Configuration through the web interface, driven by the schema.

The governance question is not optional here, and it shapes the whole surface.
Some configuration is ordinary — an endpoint, a model binding, a timeout. Some
is **posture**: the security floor, the deploy mode, whether compliance
evaluation runs at all. Changing posture from a browser without approval would
be a governance regression, so this surface **cannot** write those keys.

A posture change is turned into an oversight proposal instead. That is not a
smaller version of writing it — it is the governed path the rest of ACC already
uses, with the approval record and the audit trail that come with it.

Three further rules:

* **The schema drives the form.** Choices come from the same enum the runtime
  validates against, so the browser cannot offer a value the agent will reject.
* **Preview before write.** The caller sees exactly what would change, keyed by
  the file that owns it.
* **Secrets are never writable and never returned.** `.env` is not editable
  from here at all, and a secret-marked value is reported as set/unset.

Viewers can read this surface. Only operators can change anything.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from acc.deploy import environment
from acc.webgui.auth import Principal, require_operator, require_viewer

logger = logging.getLogger("acc.webgui.config")

router = APIRouter(prefix="/api/config", tags=["config"])


def _posture_keys() -> frozenset[str]:
    """The keys that may not be written from a browser.

    Imported from the profile module rather than restated: two lists of
    "what counts as posture" would eventually disagree, and the disagreement
    would be discovered by something posture-changing slipping through.
    """
    from acc.profiles import POSTURE_KEYS  # noqa: PLC0415

    return POSTURE_KEYS


class SetRequest(BaseModel):
    key: str = Field(..., description="Dotted configuration key")
    value: Any = Field(..., description="New value")


class ProposeRequest(BaseModel):
    key: str
    value: Any
    rationale: str = ""
    collective_id: str = ""


def _refuse_write_here() -> None:
    """409 where a configuration file written by this process is read by no
    agent (a cluster pod) — instead of writing it and promising a restart."""
    reason = environment().unavailable("config.write")
    if reason:
        raise HTTPException(status_code=409, detail=f"not available here: {reason}")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("")
def get_configuration(principal: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Every settable key, its current value, and how it may be changed."""
    from acc import configschema as schema
    from acc import configstore as store

    posture = _posture_keys()
    not_here = environment().unavailable("config.write")
    entries: list[dict[str, Any]] = []
    for key in schema.schema():
        if key.dynamic or key.file == "env":
            continue
        resolved = store.get(key.path)
        entries.append(
            {
                "key": key.path,
                "file": key.file,
                "type": key.type,
                "choices": list(key.choices),
                "description": key.description,
                "secret": key.secret,
                # A secret is reported as present or not; the value never
                # leaves the process through this surface.
                "value": ("<set>" if resolved.value else "<unset>")
                if key.secret
                else resolved.value,
                "set": resolved.present,
                "posture": key.path in posture,
                "writable": not not_here and not key.secret and key.path not in posture,
                "write_block_reason": not_here,
            }
        )
    return {
        "entries": sorted(entries, key=lambda e: e["key"]),
        "posture_keys": sorted(posture),
        "note": (
            "Posture keys are not writable here. Use /api/config/propose to "
            "route the change through oversight."
        ),
    }


@router.get("/roles")
def get_role_models(principal: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Role→model bindings with the values that are actually offerable.

    The choices come from the registry, so the browser cannot offer a model
    that would resolve to nothing at agent boot.
    """
    from acc.models import load_models, load_role_chains

    models = load_models()
    return {
        "available": [
            {"model_id": m.model_id, "backend": m.backend, "label": m.display()}
            for m in models
        ],
        "bindings": {role: chain for role, chain in sorted(load_role_chains().items())},
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@router.post("/preview")
def preview(
    request: SetRequest, principal: Principal = Depends(require_operator)
) -> dict[str, Any]:
    """What this change would do, without doing it."""
    _refuse_write_here()
    from acc import configstore as store

    try:
        change = store.set_key(request.key, str(request.value), dry_run=True)
    except store.ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "key": change.path,
        "file": change.file,
        "path": str(change.file_path),
        "before": change.before,
        "after": change.after,
        "diff": change.diff,
        "posture": request.key in _posture_keys(),
    }


@router.post("/set")
def set_value(
    request: SetRequest, principal: Principal = Depends(require_operator)
) -> dict[str, Any]:
    """Write one ordinary configuration key.

    Refuses posture keys outright. This endpoint has no path to changing the
    security floor, deliberately: a governance control that can be edited from
    a browser session is not a control.
    """
    _refuse_write_here()
    key = request.key
    if key in _posture_keys():
        raise HTTPException(
            status_code=403,
            detail=(
                f"{key} is a posture setting and cannot be changed here. "
                f"POST /api/config/propose routes it through oversight."
            ),
        )

    from acc import configstore as store

    try:
        change = store.set_key(key, str(request.value))
    except store.ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "webgui: %s set %s = %r (%s)",
        principal.user, change.path, change.after, change.file,
    )
    return {
        "key": change.path,
        "file": change.file,
        "before": change.before,
        "after": change.after,
        "diff": change.diff,
        "changed_by": principal.user,
        "at": time.time(),
        "note": "agents resolve configuration at boot — restart to apply",
    }


@router.post("/propose")
async def propose(
    request: ProposeRequest,
    http: Request,
    principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Raise a posture change as an oversight item rather than writing it.

    The item is filed on the collective's oversight queue (``OVERSIGHT_SUBMIT``,
    HIGH) so a human other than the browser session decides it.  Nothing is
    written here, and an approval applies nothing by itself: it is the
    recorded decision that whoever changes the deployment acts on.  Where no
    collective is observed the proposal comes back with ``filed: false`` —
    said, not assumed.
    """
    from acc import configschema as schema
    from acc import configstore as store

    key = request.key
    if key not in _posture_keys():
        raise HTTPException(
            status_code=400,
            detail=f"{key} is not a posture setting; use /api/config/set",
        )

    entry = schema.find(key)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"unknown key {key}")
    if entry.choices and str(request.value) not in entry.choices:
        raise HTTPException(
            status_code=400,
            detail=f"{key}: must be one of {', '.join(entry.choices)}",
        )

    current = store.get(key).value
    summary = f"change {key} from {current!r} to {request.value!r}"

    hub = getattr(http.app.state, "hub", None)
    collective_id = request.collective_id or (
        next(iter(hub.collective_ids()), "") if hub is not None else ""
    )
    obs = hub.observer(collective_id) if hub is not None and collective_id else None
    oversight_id = ""
    if obs is not None:
        from acc.cli.oversight_cmd import build_submit_payload  # noqa: PLC0415

        payload = build_submit_payload(
            collective_id, f"config-posture:{key}", f"webgui:{principal.user}", "HIGH",
            f"{summary} — {request.rationale or 'requested through the web interface'}",
        )
        await obs.publish(f"acc.{collective_id}.oversight.submit", payload)
        oversight_id = payload["oversight_id"]

    return {
        "kind": "config_posture_change",
        "risk_level": "HIGH",
        "filed": bool(oversight_id),
        "oversight_id": oversight_id,
        "collective_id": collective_id,
        "summary": summary,
        "rationale": request.rationale
        or "posture change requested through the web interface",
        "params": {"key": key, "value": request.value, "current": current},
        "requested_by": principal.user,
        "at": time.time(),
        "note": (
            "Not applied. Filed on the oversight queue; an approval records the "
            "decision — a posture change made from a browser session without "
            "one would be a governance regression."
            if oversight_id else
            "Not applied, and NOT filed: no collective is observed from here, "
            "so there is no oversight queue to put it on."
        ),
    }
