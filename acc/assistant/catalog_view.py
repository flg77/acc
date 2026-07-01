"""Unified role-catalog view for the assistant — proposal 019 PR-OP1.

The assistant cannot route to "the most capable role" if it can't
see the full catalog.  This module builds a read-only, unified view
of three sources:

1. **In-tree roles** — the 7 CONTROL roles (arbiter, assistant,
   compliance_officer, ingester, observer, orchestrator, reviewer)
   plus anything else still under ``roles/`` — always installed.
2. **Packaged roles** — movable roles served from an installed
   ``.accpkg`` (Stage 2), via
   :func:`acc.pkg.role_resolution.list_installed_roles`.
3. **Available packages** — packs advertised by a configured catalog
   but not yet installed, via :func:`acc.marketplace.render_rows`.

The catalog index is package-level (a pack like
``@acc/business-roles`` advertises one index entry, not its 25
roles), so un-installed roles can only be surfaced at the *package*
granularity — honest about the fact that you must install a pack to
enumerate its roles.

Running-vs-dormant state is a runtime fact the registry doesn't hold;
the caller (the agent runtime, or a test) passes ``running_roles`` —
the set of role names it knows are live — and this module annotates
each installed role accordingly.  Absent that hint, state is
``"installed"`` (present-but-liveness-unknown).

Pure logic, no I/O beyond reading role.yaml files + the catalog
adapters; no NATS, no LLM.  The ``catalog_query`` skill wraps
:func:`build_catalog_view` and returns its ``to_dict()`` projection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Optional

import yaml

from acc.pkg.role_resolution import CONTROL_ROLES

logger = logging.getLogger("acc.assistant.catalog_view")

# In-tree roles are read relative to this env (mirrors CapabilityIndex
# + acc-tui's ACC_ROLES_ROOT contract).  We import lazily / read the
# env at call time so tests can monkeypatch it per-case.
_EXCLUDED_ROLE_DIRS = {"_base", "TEMPLATE"}

RoleState = Literal["running", "dormant", "installed"]
RoleSource = Literal["in_tree", "package"]


@dataclass(frozen=True)
class RoleCatalogEntry:
    """One role the ecosystem currently provides (installed)."""

    role: str
    source: RoleSource
    state: RoleState
    package: Optional[str]          # @scope/name for packaged; None in-tree
    version: Optional[str]
    advertised_skills: tuple[str, ...]
    task_types: tuple[str, ...]
    domain_id: Optional[str]
    purpose: str

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "source": self.source,
            "state": self.state,
            "package": self.package,
            "version": self.version,
            "advertised_skills": list(self.advertised_skills),
            "task_types": list(self.task_types),
            "domain_id": self.domain_id,
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class AvailablePackageEntry:
    """A pack advertised by a catalog but not yet installed.

    Package-granular: install it to enumerate the roles it provides.
    """

    package: str                   # @scope/name
    version: str
    tier: str                      # trusted | tp | community | self
    catalog_id: str
    signer: str

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "version": self.version,
            "tier": self.tier,
            "catalog_id": self.catalog_id,
            "signer": self.signer,
        }


@dataclass(frozen=True)
class CatalogView:
    """The assistant's read-only catalog snapshot."""

    installed_roles: tuple[RoleCatalogEntry, ...] = ()
    available_packages: tuple[AvailablePackageEntry, ...] = ()
    control_roles: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "installed_roles": [r.to_dict() for r in self.installed_roles],
            "available_packages": [p.to_dict() for p in self.available_packages],
            "control_roles": list(self.control_roles),
        }

    def role(self, name: str) -> Optional[RoleCatalogEntry]:
        for r in self.installed_roles:
            if r.role == name:
                return r
        return None


def _read_role_capabilities(role_yaml: Path) -> dict:
    """Best-effort read of a role.yaml's advertised capabilities.

    Returns a flat dict with skills / task_types / domain_id / purpose.
    Never raises — a malformed file yields empty fields.
    """
    try:
        raw = yaml.safe_load(role_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("catalog_view: unreadable role.yaml %s (%s)", role_yaml, exc)
        return {}
    rd = raw.get("role_definition", raw) or {}
    skills = sorted(set(
        list(rd.get("allowed_skills") or [])
        + list(rd.get("default_skills") or [])
    ))
    return {
        "advertised_skills": tuple(skills),
        "task_types": tuple(rd.get("task_types") or []),
        "domain_id": rd.get("domain_id"),
        "purpose": (rd.get("purpose") or "").strip(),
    }


def _state_for(role: str, running: set[str]) -> RoleState:
    if not running:
        return "installed"
    return "running" if role in running else "dormant"


def _in_tree_roles(roles_root: Path) -> list[str]:
    if not roles_root.is_dir():
        return []
    out: list[str] = []
    for candidate in roles_root.iterdir():
        if not candidate.is_dir() or candidate.name in _EXCLUDED_ROLE_DIRS:
            continue
        if (candidate / "role.yaml").is_file():
            out.append(candidate.name)
    return out


def build_catalog_view(
    *,
    roles_root: str | Path,
    running_roles: Iterable[str] = (),
    name_filter: Optional[str] = None,
    workspace: Optional[Path] = None,
) -> CatalogView:
    """Assemble the unified catalog view.

    Args:
        roles_root: where in-tree role dirs live (the agent passes its
            resolved ``ACC_ROLES_ROOT``).
        running_roles: role names the caller knows are live; used to
            annotate ``state`` (running / dormant).  Empty → ``installed``.
        name_filter: optional ``@scope`` / substring filter applied to
            available-package names (mirrors the Marketplace search box).
        workspace: optional workspace path for layered-catalog override.

    Best-effort: registry / catalog failures degrade to empty sections
    rather than raising — the assistant always gets *some* view.
    """
    roles_root = Path(roles_root)
    running = {r for r in running_roles if r}

    installed: list[RoleCatalogEntry] = []
    seen_roles: set[str] = set()

    # 1. In-tree roles (CONTROL + any remaining).
    for role in _in_tree_roles(roles_root):
        caps = _read_role_capabilities(roles_root / role / "role.yaml")
        installed.append(RoleCatalogEntry(
            role=role,
            source="in_tree",
            state=_state_for(role, running),
            package=None,
            version=None,
            advertised_skills=caps.get("advertised_skills", ()),
            task_types=caps.get("task_types", ()),
            domain_id=caps.get("domain_id"),
            purpose=caps.get("purpose", ""),
        ))
        seen_roles.add(role)

    # 2. Packaged roles (installed from .accpkg).
    try:
        from acc.pkg.role_resolution import list_installed_roles
        packaged = list_installed_roles()
    except Exception as exc:  # noqa: BLE001
        logger.debug("catalog_view: list_installed_roles failed (%s)", exc)
        packaged = {}
    for role, src in packaged.items():
        if role in seen_roles:
            continue  # in-tree wins the display slot; dual-source loader prefers package at runtime
        caps = _read_role_capabilities(src.role_yaml_path)
        installed.append(RoleCatalogEntry(
            role=role,
            source="package",
            state=_state_for(role, running),
            package=src.package.name,
            version=src.package.version,
            advertised_skills=caps.get("advertised_skills", ()),
            task_types=caps.get("task_types", ()),
            domain_id=caps.get("domain_id"),
            purpose=caps.get("purpose", ""),
        ))
        seen_roles.add(role)

    # 3. Available packages (advertised, not installed).
    available: list[AvailablePackageEntry] = []
    try:
        from acc.marketplace import render_rows
        rows = render_rows(name_filter=name_filter, workspace=workspace)
        for row in rows:
            available.append(AvailablePackageEntry(
                package=row.name,
                version=row.version,
                tier=row.tier,
                catalog_id=row.catalog_id,
                signer=row.signer,
            ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("catalog_view: render_rows failed (%s)", exc)

    installed.sort(key=lambda r: r.role)
    available.sort(key=lambda p: (p.package, p.version))

    return CatalogView(
        installed_roles=tuple(installed),
        available_packages=tuple(available),
        control_roles=tuple(sorted(CONTROL_ROLES)),
    )


# ---------------------------------------------------------------------------
# Proposal 045 Slice 2 — catalog discovery for the assistant (parity with the
# operator's /catalog list + /catalog <num> --list-roles).  The assistant
# discovers catalogs → drills into a catalog's packs → proposes an infuse.
# ---------------------------------------------------------------------------


def list_catalog_sources(*, workspace: Optional[Path] = None) -> list[dict]:
    """The configured catalogs (id / tier / mode / url) as plain dicts.

    Best-effort: ``[]`` if the catalog layer can't be read.  Mirrors the
    operator's ``/catalog list`` so the assistant sees the same numbered set.
    """
    try:
        from acc.pkg.catalog import list_catalogs  # noqa: PLC0415
        return [
            {
                "id": c.id, "tier": c.tier, "mode": c.mode,
                "url": c.url or c.path, "priority": c.priority,
            }
            for c in list_catalogs(workspace=workspace)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("catalog_view: list_catalog_sources failed (%s)", exc)
        return []


def roles_for_catalog(
    catalog_id: str, *, workspace: Optional[Path] = None,
) -> list[dict]:
    """The packs one catalog advertises (package-granular), as plain dicts.

    Lets the assistant inspect a specific catalog's offering before proposing an
    infuse.  Best-effort ``[]``; an unknown ``catalog_id`` → ``[]``.  Roles are
    pack-granular (a pack advertises one entry, not its roles) — installing the
    pack is what enumerates its roles.
    """
    try:
        from acc.pkg.catalog import (  # noqa: PLC0415
            catalog_entries, list_catalogs,
        )
        cat = next(
            (c for c in list_catalogs(workspace=workspace) if c.id == catalog_id),
            None,
        )
        if cat is None:
            return []
        latest: dict[str, str] = {}
        for e in catalog_entries(cat):
            latest.setdefault(e.name, e.version)
        return [{"package": n, "version": v} for n, v in sorted(latest.items())]
    except Exception as exc:  # noqa: BLE001
        logger.debug("catalog_view: roles_for_catalog failed (%s)", exc)
        return []
