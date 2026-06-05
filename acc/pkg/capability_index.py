"""Capability index over installed packages — the rpmdb-style query layer.

Reads each installed package's normalized ``accpkg.yaml`` (written into the
install path at install time) to answer RPM-like questions:

* ``-qf`` — which installed package provides a given role/skill/mcp
  (:func:`find_owners`).
* ``-ql`` — what a package provides (:func:`package_provides`).
* ``-V``  — does an installed package's on-disk content still match its
  recorded ``content_sha256`` (:func:`verify_installed`).

Index-on-read (no schema change): the registry already records each
package's ``install_path``; the manifest there carries the
``roles/skills/mcps`` lists. A cached on-write index can come later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml

from acc.pkg.build import MANIFEST_NAME, _content_tree_hash, _walk_source
from acc.pkg.manifest import AccPkgManifest
from acc.pkg.registry import Registry, RegistryEntry

Kind = Literal["roles", "skills", "mcps"]
_KIND_ALIASES = {
    "role": "roles", "roles": "roles",
    "skill": "skills", "skills": "skills",
    "mcp": "mcps", "mcps": "mcps",
}


def _manifest_for(entry: RegistryEntry) -> Optional[AccPkgManifest]:
    path = Path(entry.install_path) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AccPkgManifest.model_validate(raw)
    except Exception:  # noqa: BLE001 — a malformed installed manifest shouldn't crash queries
        return None


def package_provides(entry: RegistryEntry) -> dict[str, list[str]]:
    """Return ``{"roles": [...], "skills": [...], "mcps": [...]}`` for one package."""
    m = _manifest_for(entry)
    if m is None:
        return {"roles": [], "skills": [], "mcps": []}
    return {
        "roles": [r.name for r in m.roles],
        "skills": [s.name for s in m.skills],
        "mcps": [x.name for x in m.mcps],
    }


def find_owners(
    name: str, *, kind: str | None = None, registry: Registry | None = None
) -> list[tuple[RegistryEntry, str]]:
    """Return ``[(entry, kind)]`` of installed packages providing ``name``.

    ``kind`` (optional) filters to ``role`` / ``skill`` / ``mcp``.
    """
    reg = registry or Registry()
    want: str | None = _KIND_ALIASES.get(kind) if kind else None
    out: list[tuple[RegistryEntry, str]] = []
    for entry in reg.list():
        provides = package_provides(entry)
        for k in ("roles", "skills", "mcps"):
            if want is not None and k != want:
                continue
            if name in provides[k]:
                out.append((entry, k))
    return out


def find_package(
    name: str, version: str | None = None, *, registry: Registry | None = None
) -> Optional[RegistryEntry]:
    """Resolve a package by ``@scope/name`` (newest version if unspecified)."""
    reg = registry or Registry()
    return reg.find(name, version)


def verify_installed(entry: RegistryEntry) -> tuple[bool, str]:
    """Recompute the on-disk content-tree hash and compare to the registry.

    Returns ``(ok, detail)``. ``ok`` is False on a content-hash mismatch
    (tamper) or a missing install path.
    """
    root = Path(entry.install_path)
    if not root.is_dir():
        return False, f"install path missing: {root}"
    try:
        actual = _content_tree_hash(_walk_source(root))
    except Exception as exc:  # noqa: BLE001
        return False, f"hash recompute failed: {exc}"
    if actual != entry.content_sha256:
        return False, (
            f"content hash mismatch: recorded {entry.content_sha256[:12]}…, "
            f"on-disk {actual[:12]}…"
        )
    return True, "ok"
