"""Compliance framework catalogs (PR-Z2a).

A *framework* is a catalog of controls from a governance standard the
enterprise wants to measure ACC against — NIST AI RMF, ISO/IEC 42001,
the EU AI Act, SOC 2, and enterprise-specific ones Red Hat does not
ship out of the box (e.g. Germany's **BSI**).  The Compliance pane
loads these and the gap-analysis engine (PR-Z2b) maps our loaded
Cat-A/B/C governance rules against them to find uncovered controls.

Two roots, merged by ``framework_id`` (later overrides earlier):

* **built-in** — `regulatory_layer/frameworks/*.yaml`, shipped in the
  image (read-only).
* **imported** — a writable store (`ACC_FRAMEWORKS_IMPORT_ROOT`,
  default `/app/.acc-frameworks`) the operator adds custom catalogs to
  via the pane's "+ Add framework".

Catalog YAML schema::

    framework_id: bsi_c5
    name: "BSI Cloud Computing Compliance Criteria Catalogue (C5)"
    version: "2020"
    source: "BSI C5:2020"
    controls:
      - control_id: OPS-01
        title: "Capacity management — planning"
        description: "..."
        category: "OPS"
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


class FrameworkControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    control_id: str
    title: str
    description: str = ""
    category: str = ""


class Framework(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework_id: str
    name: str
    version: str = ""
    source: str = ""
    controls: list[FrameworkControl] = Field(default_factory=list)

    @property
    def control_count(self) -> int:
        return len(self.controls)


def builtin_frameworks_root() -> Path:
    """Shipped catalogs under ``regulatory_layer/frameworks``.

    Honours ``ACC_REGULATORY_ROOT`` (the governance root) so it tracks
    the same mount the inventory loader uses.
    """
    raw = os.environ.get("ACC_REGULATORY_ROOT", "").strip()
    if raw:
        return Path(raw) / "frameworks"
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / "regulatory_layer" / "frameworks"
    if candidate.is_dir():
        return candidate
    return Path("/app/regulatory_layer/frameworks")


def imported_frameworks_root() -> Path:
    """Writable store for operator-imported catalogs."""
    raw = os.environ.get("ACC_FRAMEWORKS_IMPORT_ROOT", "").strip()
    return Path(raw) if raw else Path("/app/.acc-frameworks")


def framework_roots() -> list[Path]:
    """Load roots, lowest-precedence first (built-in < imported)."""
    return [builtin_frameworks_root(), imported_frameworks_root()]


def load_framework(path: Path) -> Framework:
    """Load + validate one framework catalog YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Framework.model_validate(raw)


def load_all_frameworks(
    roots: Optional[list[Path]] = None,
) -> list[Framework]:
    """Load every catalog across *roots*, merged by ``framework_id``.

    Later roots override earlier ones (imported beats built-in).
    Malformed files are skipped (best-effort) so one bad catalog can't
    blank the list.  Sorted by ``framework_id``.
    """
    import logging  # noqa: PLC0415
    log = logging.getLogger("acc.frameworks")

    roots = roots if roots is not None else framework_roots()
    by_id: dict[str, Framework] = {}
    for root in roots:
        if not root or not Path(root).is_dir():
            continue
        for path in sorted(Path(root).glob("*.yaml")):
            try:
                fw = load_framework(path)
            except Exception as exc:
                log.warning("frameworks: skipped %s (%s)", path, exc)
                continue
            by_id[fw.framework_id] = fw
    return sorted(by_id.values(), key=lambda f: f.framework_id)


def import_framework(
    src_path: Path | str, dest_root: Optional[Path] = None,
) -> Path:
    """Validate *src_path* as a framework catalog and copy it into the
    imported store.  Returns the written path.  Raises ValueError if the
    file isn't a valid catalog (so the pane can surface the error)."""
    src = Path(src_path)
    fw = load_framework(src)  # validates schema; raises on bad input
    dest_root = dest_root or imported_frameworks_root()
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / f"{fw.framework_id}.yaml"
    shutil.copyfile(src, dest)
    return dest


def list_framework_ids(roots: Optional[list[Path]] = None) -> list[str]:
    return [f.framework_id for f in load_all_frameworks(roots)]
