"""Map role names to the installed package providing them — Stage 1 slice 1.5.1.

The catalog + registry tell us **what packages are installed**.  The
roles themselves are identified by role-name strings (``coding_agent``,
``research_planner``, ...).  This module bridges the two: given a role
name, find the installed package whose tree contains
``roles/<name>/role.yaml`` — or ``None`` if no installed package
provides it (in which case the in-tree fallback kicks in).

Resolution rules
----------------

1. Walk all entries in :class:`acc.pkg.registry.Registry`.
2. For each entry, look at
   ``<install_path>/roles/<role_name>/role.yaml``.
3. Multiple installed packages may advertise the same role
   (different versions, different scopes).  Latest version wins
   (string-sort descending on ``RegistryEntry.version``).  Alternates
   are returned so the caller can log them at INFO for the audit
   trail.
4. The 7 CONTROL roles (arbiter, assistant, compliance_officer,
   ingester, observer, orchestrator, reviewer) are NEVER served from
   a package — they're substrate.  The resolver returns ``None`` for
   them unconditionally so a malicious package can't shadow them.

Performance
-----------

Each call walks the registry's flock-protected JSON.  RoleLoader and
CapabilityIndex both cache their lookups, so the registry scan is
once per (process, role) lifetime in practice.  No additional cache
needed at this layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from acc.pkg.registry import Registry, RegistryEntry

logger = logging.getLogger("acc.pkg.role_resolution")

# Substrate roles — never served from a package even if one tries to
# ship them.  Matches the CONTROL classification in
# ``openspec/changes/20260604-role-ecosystem-strategy/ecosystem-implementation.md``.
CONTROL_ROLES: frozenset[str] = frozenset(
    {
        "arbiter",
        "assistant",
        "compliance_officer",
        "ingester",
        "observer",
        "orchestrator",
        "reviewer",
    }
)

# The authoritative base pack that may serve CONTROL/core roles. Under
# uniform packaging core roles ARE packaged — but only this pack may
# provide them (anti-shadowing: a community pack cannot override `arbiter`).
CONTROL_ROLES_PACKAGE = "@acc/control-roles"


@dataclass(frozen=True)
class ResolvedRoleSource:
    """The chosen package + path for a role, plus any alternates."""

    role_yaml_path: Path
    package: RegistryEntry
    alternates: tuple[RegistryEntry, ...] = ()

    @property
    def audit_label(self) -> str:
        return f"installed:{self.package.name}@{self.package.version}"


def resolve_role_source(
    role_name: str, *, registry: Registry | None = None
) -> ResolvedRoleSource | None:
    """Return the installed-package source for ``role_name``, or None.

    Returns ``None`` (caller falls back to in-tree) when:

    * ``role_name`` is a CONTROL role (substrate; never packaged)
    * No installed package contains ``roles/<role_name>/role.yaml``
    """
    # Uniform packaging: control/core roles resolve from an installed
    # package too (e.g. @acc/control-roles). When no installed package
    # provides the role we return None and the caller falls back to the
    # in-tree ./roles tree (graceful during migration).
    registry = registry or Registry()
    try:
        installed = registry.list()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "role_resolution: registry unreadable (%s) — falling back to in-tree",
            exc,
        )
        return None

    matches: list[tuple[RegistryEntry, Path]] = []
    for entry in installed:
        candidate = (
            Path(entry.install_path) / "roles" / role_name / "role.yaml"
        )
        if candidate.is_file():
            matches.append((entry, candidate))

    if role_name in CONTROL_ROLES:
        # Anti-shadowing: a control/core role may ONLY be served by the
        # authoritative base pack — never an arbitrary (community) pack.
        matches = [m for m in matches if m[0].name == CONTROL_ROLES_PACKAGE]

    if not matches:
        return None

    # Latest version wins.  Plain string sort matches what
    # Registry.find() does — semver-aware sort lives in the catalog
    # resolver, not here.
    matches.sort(key=lambda m: m[0].version, reverse=True)
    chosen_entry, chosen_path = matches[0]
    alternates = tuple(entry for entry, _ in matches[1:])

    if alternates:
        logger.info(
            "role_resolution: %s served from %s@%s (alternates: %s)",
            role_name,
            chosen_entry.name,
            chosen_entry.version,
            ", ".join(f"{a.name}@{a.version}" for a in alternates),
        )

    return ResolvedRoleSource(
        role_yaml_path=chosen_path,
        package=chosen_entry,
        alternates=alternates,
    )


def list_installed_roles(
    registry: Registry | None = None,
) -> dict[str, ResolvedRoleSource]:
    """Return a ``{role_name: ResolvedRoleSource}`` map for every role
    served by an installed package.

    CapabilityIndex uses this to surface installed-package roles
    alongside in-tree ones without per-role registry scans.
    """
    registry = registry or Registry()
    try:
        installed = registry.list()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "role_resolution: registry unreadable (%s) — returning empty map",
            exc,
        )
        return {}

    out: dict[str, list[tuple[RegistryEntry, Path]]] = {}
    for entry in installed:
        roles_dir = Path(entry.install_path) / "roles"
        if not roles_dir.is_dir():
            continue
        for role_dir in roles_dir.iterdir():
            if not role_dir.is_dir():
                continue
            # Anti-shadowing: only the base pack may serve control roles.
            if role_dir.name in CONTROL_ROLES and entry.name != CONTROL_ROLES_PACKAGE:
                continue
            role_yaml = role_dir / "role.yaml"
            if not role_yaml.is_file():
                continue
            out.setdefault(role_dir.name, []).append((entry, role_yaml))

    resolved: dict[str, ResolvedRoleSource] = {}
    for name, matches in out.items():
        matches.sort(key=lambda m: m[0].version, reverse=True)
        chosen_entry, chosen_path = matches[0]
        resolved[name] = ResolvedRoleSource(
            role_yaml_path=chosen_path,
            package=chosen_entry,
            alternates=tuple(e for e, _ in matches[1:]),
        )
    return resolved
