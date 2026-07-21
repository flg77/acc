"""OKF knowledge packs — discover + index installed OKF bundles (OKF P5).

A *knowledge* pack ships curated content, not capabilities: one or more OKF v0.1
bundles under ``bundles/<name>/``.  On install the tree lands under
``ACC_PACKAGES_ROOT`` like any other package; this module finds those bundles
and indexes them into a collective's document store (via
:func:`acc.lib.okf.index_bundle`) so agents retrieve them.

Indexing is **idempotent** per ``(bundles-dir, collective)``: a marker file
``bundles/.okf-indexed-<collective_id>`` records which bundles have been
indexed, so a second boot / a restart is a no-op.  A new pack *version* installs
to a fresh path (no marker) and is re-indexed, as intended.  Best-effort
throughout — a bad bundle is logged and skipped, never raised, so knowledge
indexing can never abort an agent's boot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acc.pkg.registry import Registry, installed_capability_dirs

logger = logging.getLogger("acc.pkg.knowledge")

_MARKER_PREFIX = ".okf-indexed-"


def installed_okf_bundle_dirs(registry: Registry | None = None) -> list[Path]:
    """Every installed OKF bundle dir — ``<install_path>/bundles/<name>/`` across
    all installed packages.  Best-effort: a missing/empty registry returns ``[]``."""
    out: list[Path] = []
    for parent in installed_capability_dirs("bundles", registry):
        for child in sorted(parent.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                out.append(child)
    return out


async def index_installed_bundles(
    store: Any, *, collective_id: str, registry: Registry | None = None,
) -> dict[str, Any]:
    """Index every not-yet-indexed installed OKF bundle into *store*.

    Returns ``{"indexed_bundles": N, "documents": M, "skipped": K}`` (K = bundles
    already indexed for this collective).  Never raises.
    """
    from acc.lib.okf import index_bundle, load_bundle  # noqa: PLC0415 (cycle-safe)

    reg = registry or Registry()
    indexed = docs = skipped = 0
    for parent in installed_capability_dirs("bundles", reg):
        marker = parent / f"{_MARKER_PREFIX}{collective_id}"
        done: set[str] = set()
        if marker.exists():
            try:
                done = set(marker.read_text(encoding="utf-8").split())
            except OSError:
                done = set()
        newly: list[str] = []
        pack_id = parent.parent.name  # "<pkg>-<version>" install dir
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in done:
                skipped += 1
                continue
            try:
                res = await index_bundle(
                    store, load_bundle(child),
                    extra_tags=[f"okf-pack:{pack_id}"],
                )
                docs += int(res.get("indexed", 0))
                indexed += 1
                newly.append(child.name)
            except Exception as exc:  # noqa: BLE001 — one bad bundle never aborts
                logger.warning("okf knowledge: index failed for %s: %s", child, exc)
        if newly:
            try:
                marker.write_text("\n".join(sorted(done | set(newly))) + "\n",
                                  encoding="utf-8")
            except OSError as exc:
                # Read-only package mount: we indexed, but can't record it, so a
                # later boot re-indexes.  Log it rather than hide the dup risk.
                logger.warning("okf knowledge: marker unwritable at %s (%s) — "
                               "re-index may duplicate", marker, exc)
    return {"indexed_bundles": indexed, "documents": docs, "skipped": skipped}
