"""Export the golden-prompt store as an ``@scope/*`` pack (use-case
portability — gap #5 of the a→b→c author→export→drive flow).

A golden-only pack is just an ``accpkg.yaml`` (name + version, no
roles/skills/mcps) plus a ``golden/`` directory of prompt YAMLs.  The
build/install pipeline is kind-agnostic (``build._walk_source`` packs
every file; ``install._extract_safely`` unpacks every member), so the
``golden/`` dir rides through untouched.  Once installed under
``ACC_PACKAGES_ROOT``, its prompts are auto-discovered by
``golden_prompts.golden_roots`` via ``registry.installed_capability_dirs
("golden")`` (gap #4) — no manifest field and no loader special-casing
needed.

This is the *build* half; publish (sign + upload) reuses
``acc.pkg.publish`` unchanged.  Together they close the loop:
author in Diagnostics → ``golden-pack`` → publish → a corpus installs the
pack (AccPackageInstall CR) → the suite auto-loads → the same ``run_all``
engine drives it at DC scale.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from acc.pkg.build import BuildResult, build


def _slug(name: str) -> str:
    """A filesystem-safe stem for ``@scope/name`` → ``scope-name``."""
    return name.lstrip("@").replace("/", "-")


def build_golden_pack(
    name: str,
    version: str,
    *,
    prompts_root: Optional[Path] = None,
    output_path: Optional[Path] = None,
    staging_dir: Optional[Path] = None,
    description: str = "",
) -> BuildResult:
    """Stage a pack source tree from the golden-prompt store and build it.

    Reads the merged prompts under *prompts_root* (default: the writable
    golden store), writes them under ``<staged>/golden/<name>.yaml`` next to
    a minimal ``accpkg.yaml`` (``name`` + ``version`` only), and builds a
    deterministic ``.accpkg`` at *output_path* (default
    ``<scope-name>-<version>.accpkg`` in the cwd).

    Returns the :class:`acc.pkg.build.BuildResult` (stamped manifest + output
    path + content/tarball hashes).  Raises ``ValueError`` when the store has
    no prompts, and ``pydantic.ValidationError`` on a bad name/version (the
    manifest enforces ``@scope/name`` + exact semver).
    """
    import yaml as _yaml

    from acc.golden_prompts import dump_prompt, load_merged, writable_root

    root = Path(prompts_root) if prompts_root is not None else writable_root()
    prompts = load_merged([root])
    if not prompts:
        raise ValueError(f"no golden prompts found under {root}")

    slug = _slug(name)
    out = (
        Path(output_path)
        if output_path is not None
        else Path(f"{slug}-{version}.accpkg")
    )

    parent = (
        Path(staging_dir)
        if staging_dir is not None
        else Path(tempfile.mkdtemp(prefix="acc-golden-pack-"))
    )
    src = parent / slug
    golden = src / "golden"
    golden.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "description": (
            description
            or f"Golden-prompt use-case pack ({len(prompts)} prompts)."
        ),
    }
    (src / "accpkg.yaml").write_text(
        _yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8",
    )
    for p in prompts:
        dump_prompt(p, golden / f"{p.name}.yaml")

    # validate=False: a golden-only pack declares no capabilities to gate.
    return build(src, out, validate=False)
