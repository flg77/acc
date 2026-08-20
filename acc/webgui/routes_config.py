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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

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


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("")
def get_configuration(principal: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Every settable key, its current value, and how it may be changed."""
    from acc import configschema as schema
    from acc import configstore as store

    posture = _posture_keys()
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
                "writable": not key.secret and key.path not in posture,
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
def propose(
    request: ProposeRequest, principal: Principal = Depends(require_operator)
) -> dict[str, Any]:
    """Raise a posture change as an oversight proposal rather than writing it.

    Returns the proposal for submission. Nothing is written here — the whole
    point is that a human other than the browser session approves it.
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
    return {
        "kind": "config_posture_change",
        "risk_level": "HIGH",
        "summary": f"change {key} from {current!r} to {request.value!r}",
        "rationale": request.rationale
        or "posture change requested through the web interface",
        "params": {"key": key, "value": request.value, "current": current},
        "requested_by": principal.user,
        "at": time.time(),
        "note": (
            "Not applied. This is a proposal for the oversight queue; a posture "
            "change made from a browser session without approval would be a "
            "governance regression."
        ),
    }
