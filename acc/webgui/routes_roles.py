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
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from pydantic import BaseModel, Field, ValidationError

from acc import catalog_admin, marketplace
from acc.pkg.catalog import Catalog
from acc.webgui.auth import require_operator, require_viewer

logger = logging.getLogger("acc.webgui.routes_roles")

router = APIRouter(prefix="/api", tags=["roles"])

# Role ids are directory names under roles/ — restrict to a safe charset
# so a path like ``../../etc`` can never reach the filesystem helpers.
_ROLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def _roles_root() -> Path:
    """Resolve the writable in-tree roles/ directory.

    Mirrors the CapabilityIndex / acc-tui ``ACC_ROLES_ROOT`` contract;
    the WebGUI container mounts this writable (agent pods mount it
    read-only).  Defaults to the in-image ``/app/roles`` layout.
    """
    return Path(os.environ.get("ACC_ROLES_ROOT", "/app/roles"))


def _safe_role_id(role_id: str) -> str:
    if not _ROLE_ID_RE.match(role_id or ""):
        raise HTTPException(
            status_code=400,
            detail=f"invalid role id {role_id!r} (lowercase + underscore only)",
        )
    return role_id


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
# Role authoring — create / edit role.yaml + role.md (proposal 020 WS-C)
#
# WebGUI parity with the TUI Ecosystem pane's inline role editor.  Reuses
# acc.tui.role_writeback (validate + atomic write) so the WebGUI and TUI
# share one validation + write path.  Publish-to-catalog is WS-C3 (gated
# on the signing-identity decision) and lands separately.
# ---------------------------------------------------------------------------


class _RoleYamlIn(BaseModel):
    yaml_text: str = Field(..., min_length=1, description="full role.yaml contents")


class _RoleMdIn(BaseModel):
    md_text: str = Field("", description="full role.md contents (free-form)")


class _RoleCreateIn(BaseModel):
    role_id: str = Field(..., description="new role dir name (lowercase + _)")
    yaml_text: str = Field(..., min_length=1)
    md_text: str = Field("", description="optional role.md")


def _role_yaml_path(role_id: str) -> Path:
    return _roles_root() / role_id / "role.yaml"


def _role_md_path(role_id: str) -> Path:
    return _roles_root() / role_id / "role.md"


@router.get(
    "/roles",
    summary="List authorable in-tree roles (role-editor picker source)",
)
def roles_list(_: bool = Depends(require_viewer)) -> list[dict]:
    """Enumerate role dirs under ``ACC_ROLES_ROOT`` that carry a role.yaml.

    Distinct from ``/roles/available`` (catalog *packages*): this lists the
    locally-authorable roles the editor can open + edit + publish.
    """
    root = _roles_root()
    if not root.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not _ROLE_ID_RE.match(child.name):
            continue
        if not (child / "role.yaml").is_file():
            continue
        out.append(
            {
                "role_id": child.name,
                "has_md": (child / "role.md").is_file(),
            }
        )
    return out


@router.get(
    "/roles/{role_id}/md",
    summary="Read a role's role.md (for the editor)",
)
def role_md_get(
    role_id: str = PathParam(...),
    _: bool = Depends(require_viewer),
) -> dict:
    role_id = _safe_role_id(role_id)
    path = _role_md_path(role_id)
    # role.md is optional — return empty text rather than 404 so the editor
    # can open a role that has only a role.yaml and start a narrative.
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {"role_id": role_id, "md_text": text}


@router.get(
    "/roles/{role_id}/yaml",
    summary="Read a role's role.yaml (for the editor)",
)
def role_yaml_get(
    role_id: str = PathParam(...),
    _: bool = Depends(require_viewer),
) -> dict:
    role_id = _safe_role_id(role_id)
    path = _role_yaml_path(role_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"role {role_id!r} has no role.yaml")
    return {"role_id": role_id, "yaml_text": path.read_text(encoding="utf-8")}


@router.put(
    "/roles/{role_id}/yaml",
    summary="Validate + write a role's role.yaml (atomic)",
)
def role_yaml_put(
    body: _RoleYamlIn,
    role_id: str = PathParam(...),
    _: bool = Depends(require_operator),
) -> dict:
    role_id = _safe_role_id(role_id)
    path = _role_yaml_path(role_id)
    if not path.parent.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"role {role_id!r} does not exist; POST /api/roles to create it",
        )
    from acc.tui.role_writeback import RoleValidationError, upsert_role_yaml  # noqa: PLC0415
    try:
        upsert_role_yaml(path, body.yaml_text, role_name=role_id,
                         roles_root=_roles_root(), validate=True)
    except RoleValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "errors": exc.errors},
        ) from exc
    logger.info("role_yaml_put: wrote role.yaml for %s by operator", role_id)
    return {"role_id": role_id, "action": "updated"}


@router.put(
    "/roles/{role_id}/md",
    summary="Write a role's role.md (free-form narrative)",
)
def role_md_put(
    body: _RoleMdIn,
    role_id: str = PathParam(...),
    _: bool = Depends(require_operator),
) -> dict:
    role_id = _safe_role_id(role_id)
    path = _role_md_path(role_id)
    if not path.parent.is_dir():
        raise HTTPException(status_code=404, detail=f"role {role_id!r} does not exist")
    from acc.tui.role_writeback import upsert_role_md  # noqa: PLC0415
    upsert_role_md(path, body.md_text)
    return {"role_id": role_id, "action": "updated"}


@router.post(
    "/roles",
    summary="Create a new role (role.yaml + optional role.md)",
)
def role_create(
    body: _RoleCreateIn,
    _: bool = Depends(require_operator),
) -> dict:
    role_id = _safe_role_id(body.role_id)
    role_dir = _roles_root() / role_id
    if (role_dir / "role.yaml").is_file():
        raise HTTPException(status_code=409, detail=f"role {role_id!r} already exists")
    from acc.tui.role_writeback import (  # noqa: PLC0415
        RoleValidationError, upsert_role_md, upsert_role_yaml,
    )
    try:
        role_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"cannot create role dir: {exc}") from exc
    try:
        upsert_role_yaml(role_dir / "role.yaml", body.yaml_text,
                         role_name=role_id, roles_root=_roles_root(), validate=True)
    except RoleValidationError as exc:
        # roll back the empty dir so a failed create leaves no trace
        try:
            (role_dir).rmdir()
        except OSError:
            pass
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "errors": exc.errors},
        ) from exc
    if body.md_text:
        upsert_role_md(role_dir / "role.md", body.md_text)
    logger.info("role_create: created role %s by operator", role_id)
    return {"role_id": role_id, "action": "created"}


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
