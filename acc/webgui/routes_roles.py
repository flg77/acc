"""WebGUI roles + catalogs REST API — Stage 2.4.

Mirrors the data the TUI's MarketplaceScreen + CatalogsScreen
consume.  Pure JSON; the React surface (separate ``acc-web`` repo)
builds the visual layer on top.

Routes:

* ``GET    /api/roles/available``         — list packages
* ``POST   /api/roles/install``           — stage PROPOSE_INFUSE
* ``GET    /api/catalogs``                — list catalogs
* ``POST   /api/catalogs``                — add catalog
* ``DELETE /api/catalogs/{catalog_id}``   — remove catalog
* ``PATCH  /api/catalogs/{catalog_id}``   — update priority

Auth: viewer for reads, operator for writes (matches existing
acc/webgui/auth.py role gating).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from pydantic import BaseModel, Field, ValidationError

from acc import catalog_admin, marketplace
from acc.pkg.catalog import Catalog
from acc.webgui.auth import require_operator, require_viewer

logger = logging.getLogger("acc.webgui.routes_roles")

router = APIRouter(prefix="/api", tags=["roles"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class _RowOut(BaseModel):
    name: str
    version: str
    tier: str
    tier_badge: str
    catalog_id: str
    catalog_mode: str
    signer: str
    install_marker: str


class _InstallRequest(BaseModel):
    name: str = Field(..., description="@scope/name to install")
    constraint: Optional[str] = Field(
        None,
        description="Semver constraint; defaults to ^<highest-version-found>",
    )


class _InstallResponse(BaseModel):
    """The marker the operator's UI surfaces to the Compliance pane."""

    install_marker: str
    target_name: str
    target_constraint: str


class _CatalogIn(BaseModel):
    catalog_id: str
    tier: str
    mode: str
    url: Optional[str] = ""
    path: Optional[str] = ""
    issuer: str
    subject_pattern: str
    key_path: Optional[str] = ""
    priority: int = 100


class _PriorityPatch(BaseModel):
    priority: int


def _catalog_to_json(c: Catalog) -> dict:
    return {
        "id": c.id,
        "tier": c.tier,
        "mode": c.mode,
        "url": c.url,
        "path": c.path,
        "required_signer": {
            "issuer": c.required_signer.issuer,
            "subject_pattern": c.required_signer.subject_pattern,
            "key_path": c.required_signer.key_path,
        },
        "priority": c.priority,
    }


# ---------------------------------------------------------------------------
# Roles — discovery + install staging
# ---------------------------------------------------------------------------


@router.get(
    "/roles/available",
    response_model=list[_RowOut],
    summary="List packages advertised across layered catalogs",
)
def roles_available(
    filter: str = "",
    _: bool = Depends(require_viewer),
) -> list[dict]:
    rows = marketplace.render_rows(name_filter=filter or None)
    return [
        {
            "name": r.name,
            "version": r.version,
            "tier": r.tier,
            "tier_badge": r.tier_badge,
            "catalog_id": r.catalog_id,
            "catalog_mode": r.catalog_mode,
            "signer": r.signer,
            "install_marker": r.install_marker,
        }
        for r in rows
    ]


@router.post(
    "/roles/install",
    response_model=_InstallResponse,
    summary="Stage a PROPOSE_INFUSE marker for the Compliance pane queue",
)
def roles_install(
    body: _InstallRequest,
    _: bool = Depends(require_operator),
) -> _InstallResponse:
    versions = marketplace.list_versions(body.name)
    if not versions:
        raise HTTPException(
            status_code=404,
            detail=f"no catalog advertises {body.name!r}",
        )
    target = versions[0]  # newest
    try:
        marker = marketplace.stage_install(target, constraint=body.constraint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    constraint = body.constraint or f"^{target.version.split('-')[0]}"
    logger.info(
        "roles_install: staged %s@%s by operator", body.name, constraint,
    )
    return _InstallResponse(
        install_marker=marker,
        target_name=body.name,
        target_constraint=constraint,
    )


# ---------------------------------------------------------------------------
# Catalogs — CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/catalogs",
    summary="List configured catalogs (workspace layer)",
)
def catalogs_list(_: bool = Depends(require_viewer)) -> list[dict]:
    cats = catalog_admin.load()
    return [_catalog_to_json(c) for c in cats]


@router.post(
    "/catalogs",
    summary="Add a new catalog to the workspace layer",
)
def catalogs_add(
    body: _CatalogIn,
    _: bool = Depends(require_operator),
) -> dict:
    try:
        cat = catalog_admin.parse_form(
            catalog_id=body.catalog_id,
            tier=body.tier,
            mode=body.mode,
            url=body.url or "",
            path=body.path or "",
            issuer=body.issuer,
            subject_pattern=body.subject_pattern,
            key_path=body.key_path or "",
            priority=body.priority,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    try:
        result = catalog_admin.add(cat)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "action": result.action,
        "catalog_id": result.catalog_id,
        "path": str(result.path),
    }


@router.delete(
    "/catalogs/{catalog_id}",
    summary="Remove a catalog from the workspace layer",
)
def catalogs_remove(
    catalog_id: str = PathParam(..., min_length=1),
    _: bool = Depends(require_operator),
) -> dict:
    try:
        result = catalog_admin.remove(catalog_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "action": result.action,
        "catalog_id": result.catalog_id,
        "path": str(result.path),
    }


@router.patch(
    "/catalogs/{catalog_id}",
    summary="Update a catalog's priority",
)
def catalogs_set_priority(
    body: _PriorityPatch,
    catalog_id: str = PathParam(..., min_length=1),
    _: bool = Depends(require_operator),
) -> dict:
    if not (1 <= body.priority <= 1000):
        raise HTTPException(
            status_code=400, detail="priority must be 1-1000",
        )
    try:
        result = catalog_admin.set_priority(catalog_id, body.priority)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "action": result.action,
        "catalog_id": result.catalog_id,
        "priority": body.priority,
        "path": str(result.path),
    }
