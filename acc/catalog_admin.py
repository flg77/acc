"""Catalog admin data adapter — Stage 2.4 partial (data layer).

Read + mutate the per-collective ``<workspace>/.acc/catalogs.yaml``
file with validation against :class:`acc.pkg.catalog.Catalog`.
Used by the Catalog admin TUI pane + WebGUI route + the
``acc-podman-desktop`` first-run wizard.

Mutations are guarded by :func:`acc._atomic_write.atomic_write_text`
so concurrent edits from the TUI + the workspace shell don't lose
data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError

from acc._atomic_write import atomic_write_text
from acc.pkg.catalog import Catalog, CatalogFile, RequiredSigner

logger = logging.getLogger("acc.catalog_admin")

CATALOGS_FILENAME = "catalogs.yaml"
DOT_ACC_DIR = ".acc"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def workspace_catalogs_path(workspace: Optional[Path] = None) -> Path:
    """Resolve ``<workspace>/.acc/catalogs.yaml``.

    Defaults to ``Path.cwd()`` so the TUI invoked from a workspace dir
    edits the local override.
    """
    ws = workspace or Path.cwd()
    return ws / DOT_ACC_DIR / CATALOGS_FILENAME


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load(workspace: Optional[Path] = None) -> list[Catalog]:
    """Return the catalog list from ``<workspace>/.acc/catalogs.yaml``.

    Missing file returns an empty list — matches the Stage 0 catalog
    resolver's tolerant behaviour.
    """
    path = workspace_catalogs_path(workspace)
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML in {path}: {exc}") from exc
    parsed = CatalogFile.model_validate(raw)
    return list(parsed.catalogs)


def save(catalogs: list[Catalog], workspace: Optional[Path] = None) -> Path:
    """Persist ``catalogs`` to ``<workspace>/.acc/catalogs.yaml``.

    Atomic + flock-protected via
    :func:`acc._atomic_write.atomic_write_text`.  Returns the absolute
    path written so callers can audit-log it.
    """
    path = workspace_catalogs_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = CatalogFile(catalogs=catalogs).model_dump(mode="json")
    text = yaml.safe_dump(
        doc, sort_keys=False, default_flow_style=False, width=999,
    )
    atomic_write_text(path, text, mode=0o644, backup=False)
    return path.resolve()


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationResult:
    """What a mutation did, for the TUI status line + audit log."""

    action: str        # "added" | "removed" | "reordered"
    catalog_id: str
    path: Path


def add(
    catalog: Catalog, *, workspace: Optional[Path] = None,
) -> MutationResult:
    """Append ``catalog``; refuse on duplicate ``id``.

    Caller validated ``catalog`` against the Pydantic model already
    (TUI does this in the form-submit handler).
    """
    catalogs = load(workspace)
    if any(c.id == catalog.id for c in catalogs):
        raise ValueError(f"catalog id {catalog.id!r} already exists")
    catalogs.append(catalog)
    path = save(catalogs, workspace)
    return MutationResult(action="added", catalog_id=catalog.id, path=path)


def remove(
    catalog_id: str, *, workspace: Optional[Path] = None,
) -> MutationResult:
    """Drop the catalog with the given ``id``."""
    catalogs = load(workspace)
    new = [c for c in catalogs if c.id != catalog_id]
    if len(new) == len(catalogs):
        raise ValueError(f"catalog id {catalog_id!r} not found")
    path = save(new, workspace)
    return MutationResult(action="removed", catalog_id=catalog_id, path=path)


def set_priority(
    catalog_id: str, priority: int, *, workspace: Optional[Path] = None,
) -> MutationResult:
    """Update the priority field on an existing catalog."""
    catalogs = load(workspace)
    found = False
    new: list[Catalog] = []
    for c in catalogs:
        if c.id == catalog_id:
            data = c.model_dump()
            data["priority"] = priority
            new.append(Catalog.model_validate(data))
            found = True
        else:
            new.append(c)
    if not found:
        raise ValueError(f"catalog id {catalog_id!r} not found")
    path = save(new, workspace)
    return MutationResult(
        action="reordered", catalog_id=catalog_id, path=path,
    )


# ---------------------------------------------------------------------------
# Form helpers — the TUI's "Add Catalog" form posts to these
# ---------------------------------------------------------------------------


def parse_form(
    *,
    catalog_id: str,
    tier: str,
    mode: str,
    url: str = "",
    path: str = "",
    issuer: str,
    subject_pattern: str,
    key_path: str = "",
    priority: int = 100,
) -> Catalog:
    """Build + validate a :class:`Catalog` from individual form fields.

    Surfaces Pydantic's ``ValidationError`` to the TUI form-submit
    handler so per-field errors can render inline.
    """
    return Catalog(
        id=catalog_id,
        tier=tier,
        mode=mode,
        url=url,
        path=path,
        required_signer=RequiredSigner(
            issuer=issuer,
            subject_pattern=subject_pattern,
            key_path=key_path,
        ),
        priority=priority,
    )
