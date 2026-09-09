"""The data trees that leave the checkout -- collected into the wheel (IN-05).

`20260909-acc-install` IN-05 (proposal 055).  ``roles/``, ``skills/``,
``mcps/``, ``collectives/``, ``container/production/``,
``regulatory_layer/``, the ``*.example`` configs, ``acc-deploy.sh`` and
``packaging/control-roles.yaml`` live at the repository root, so a plain
``pip install`` never carried them and every command needed a checkout.
``setup.py`` calls :func:`collect` at build time to copy them into
``acc/_share/`` inside the wheel; :func:`acc.paths.share` finds that
directory next to the package (``<site-packages>/acc/_share``), after an
operator's home and before ``<prefix>/share/acc``.  The RPM (IN-06) points
``/usr/share/acc`` at the same tree.

The list is explicit on purpose: what ships is a decision, not a glob.
State (``instances/``, ``logs/``, ``workspaces/``, ``.env``) never ships.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: Directories copied whole (relative to the repository root).
SHARE_TREES: tuple[str, ...] = (
    "roles",
    "skills",
    "mcps",
    "collectives",
    "container/production",
    "regulatory_layer",
)
#: Single files copied to the same relative place.
SHARE_FILES: tuple[str, ...] = (
    "acc-deploy.sh",
    ".env.example",
    "acc-config.yaml.example",
    "models.yaml.example",
    "collective.yaml.example",
    "catalogs.yaml.example",
    "acc-webgui.htpasswd.example",
    "packaging/control-roles.yaml",
)
#: Never shipped, wherever they appear inside a tree.
EXCLUDE_NAMES: frozenset[str] = frozenset({"__pycache__", ".git", ".DS_Store", "node_modules"})
EXCLUDE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")


def _ignore(directory: str, names: list[str]) -> set[str]:
    return {n for n in names if n in EXCLUDE_NAMES or n.endswith(EXCLUDE_SUFFIXES)}


def collect(root: Path, dest: Path) -> list[str]:
    """Copy the share trees and files from *root* into *dest*.

    Returns the relative paths copied (trees first, then files).  A tree or
    file that is absent in *root* is skipped, not an error: a source
    checkout may be partial (the public mirror lacks the withheld files).
    """
    root = Path(root).resolve()
    dest = Path(dest).resolve()
    copied: list[str] = []
    for rel in SHARE_TREES:
        src = root / rel
        if not src.is_dir():
            continue
        target = dest / rel
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, ignore=_ignore, symlinks=False)
        copied.append(rel)
    for rel in SHARE_FILES:
        src = root / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(rel)
    (dest / "SHARE.txt").write_text(
        "The ACC data trees shipped with the package (packaging/build_share.py).\n"
        + "".join(f"{c}\n" for c in copied),
        encoding="utf-8",
    )
    return copied


if __name__ == "__main__":  # pragma: no cover
    import sys

    here = Path(__file__).resolve().parent.parent
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "acc" / "_share"
    for line in collect(here, out):
        print(line)
