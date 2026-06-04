"""`.accpkg` installer — Stage 0 slice 5.

Install flow
------------

Given a ``.accpkg`` file path (catalog resolution comes in slice 6,
operator passes the path directly for Stage 0):

1. Open the gzipped tar and read the first entry — must be
   ``accpkg.yaml``.  Validate the manifest against schema v1.
2. Recompute the content-tree hash by streaming through every other
   entry.  REFUSE if it doesn't match ``manifest.content_sha256``.
3. Resolve ``depends_on`` against the local registry: every named
   dependency must already be installed at a version satisfying the
   constraint.  REFUSE on missing/incompatible dep.  Cycle detection
   waits for the multi-package resolver (slice 6).
4. Unpack to ``<root>/<scope>/<name>-<version>/``.  Tar-slip
   protection: REFUSE any entry with absolute paths, ``..`` parts, or
   a name that escapes the install root.
5. Add a :class:`RegistryEntry` under flock + thread lock.
6. Idempotent re-install: if ``(name, version)`` is already
   registered AND the install_path exists AND its tree-hash matches,
   return the existing entry without rewriting anything.

This module does NOT verify signatures — that's slice 7
(``acc/pkg/verify.py``).  Callers wire verify ahead of install in
the CLI (slice 8).
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from acc.pkg._semver import version_satisfies
from acc.pkg.build import MANIFEST_NAME
from acc.pkg.manifest import AccPkgManifest
from acc.pkg.registry import Registry, RegistryEntry

logger = logging.getLogger("acc.pkg.install")


# ---------------------------------------------------------------------------
# Exceptions — let the CLI map these to deterministic exit codes
# ---------------------------------------------------------------------------


class InstallError(Exception):
    """Base for all install failures."""


class ContentHashMismatch(InstallError):
    """The tarball's content-tree hash didn't match the manifest."""


class MissingDependency(InstallError):
    """A declared ``depends_on:`` is not installed at a satisfying version."""


class UnsafePath(InstallError):
    """A tar entry pointed outside the install root (tar-slip attempt)."""


class AlreadyInstalled(InstallError):
    """``(name, version)`` is already installed with diverging content.

    Distinct from idempotent re-install — see ``install()`` docstring.
    """


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallResult:
    entry: RegistryEntry
    manifest: AccPkgManifest
    install_path: Path
    was_already_installed: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_manifest_from_tar(tar: tarfile.TarFile) -> tuple[AccPkgManifest, tarfile.TarInfo]:
    """Read + validate the first tar entry, which must be ``accpkg.yaml``."""
    first = next(iter(tar))
    if first.name != MANIFEST_NAME:
        raise InstallError(
            f"package is missing manifest as first entry; got {first.name!r}"
        )
    extracted = tar.extractfile(first)
    if extracted is None:
        raise InstallError("could not read manifest from tarball")
    raw = yaml.safe_load(extracted.read()) or {}
    return AccPkgManifest.model_validate(raw), first


def read_manifest(pkg_path: Path) -> AccPkgManifest:
    """Read + validate the manifest of a built ``.accpkg`` without installing.

    Used by the catalog-aware fetch layer to inspect ``depends_on``
    before installing (transitive dependency resolution).
    """
    with gzip.open(pkg_path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        manifest, _ = _read_manifest_from_tar(tar)
    return manifest


def _split_scope_name(name: str) -> tuple[str, str]:
    """``@scope/name`` → ``("scope", "name")``."""
    assert name.startswith("@") and "/" in name, name
    scope, pkg = name[1:].split("/", 1)
    return scope, pkg


def _safe_join(install_root: Path, member_name: str) -> Path:
    """Resolve ``install_root / member_name`` and refuse traversal."""
    if not member_name or member_name.startswith("/"):
        raise UnsafePath(f"absolute path in tarball: {member_name!r}")
    candidate = (install_root / member_name).resolve()
    install_root_resolved = install_root.resolve()
    try:
        candidate.relative_to(install_root_resolved)
    except ValueError as exc:
        raise UnsafePath(
            f"tar entry {member_name!r} escapes install root"
        ) from exc
    return candidate


def _content_tree_hash_from_tar(tar_path: Path) -> str:
    """Re-compute the content-tree hash by streaming the tarball.

    Mirrors ``acc.pkg.build._content_tree_hash`` exactly: sha256 of the
    concatenated ``"<relpath>:<file_sha256>\\n"`` lines, sorted by
    relpath, EXCLUDING the top-level ``accpkg.yaml`` entry.
    """
    entries: list[tuple[str, str]] = []
    with gzip.open(tar_path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        for info in tar:
            if info.name == MANIFEST_NAME:
                continue
            if not info.isfile():
                continue
            extracted = tar.extractfile(info)
            if extracted is None:
                continue
            h = hashlib.sha256()
            for chunk in iter(lambda: extracted.read(65536), b""):
                h.update(chunk)
            # Normalise to forward slashes — matches build-side hash.
            norm = info.name.replace("\\", "/")
            entries.append((norm, h.hexdigest()))
    entries.sort(key=lambda e: e[0])
    accumulator = hashlib.sha256()
    for rel, sha in entries:
        accumulator.update(f"{rel}:{sha}\n".encode("utf-8"))
    return accumulator.hexdigest()


def _check_dependencies(
    manifest: AccPkgManifest, registry: Registry
) -> None:
    for dep in manifest.depends_on:
        installed = registry.find_by_name(dep.name)
        if not installed:
            raise MissingDependency(
                f"package {manifest.name}@{manifest.version} depends on "
                f"{dep.name}@{dep.version}, but {dep.name} is not installed"
            )
        if not any(version_satisfies(e.version, dep.version) for e in installed):
            available = ", ".join(e.version for e in installed)
            raise MissingDependency(
                f"package {manifest.name}@{manifest.version} depends on "
                f"{dep.name}@{dep.version}, but only {available} installed"
            )


def _resolve_install_path(root: Path, manifest: AccPkgManifest) -> Path:
    scope, pkg = _split_scope_name(manifest.name)
    return root / scope / f"{pkg}-{manifest.version}"


def _extract_safely(
    tar_path: Path, install_path: Path
) -> None:
    """Unpack tarball into ``install_path`` with tar-slip protection.

    Excludes the top-level ``accpkg.yaml`` from extraction (it's the
    manifest carrier; we write a normalized copy at the end so
    ``acc-pkg inspect`` can read it without re-opening the tarball).
    """
    install_path.mkdir(parents=True, exist_ok=True)
    with gzip.open(tar_path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        for info in tar:
            if info.name == MANIFEST_NAME:
                continue
            target = _safe_join(install_path, info.name)
            if info.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not info.isfile():
                # Symlinks + special files refused — matches build-time
                # source-tree refusal.
                raise UnsafePath(
                    f"non-regular tar entry refused: {info.name!r}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            # Normalize permissions — drop whatever the tar carried.
            target.chmod(0o644)


def _write_manifest_copy(install_path: Path, manifest: AccPkgManifest) -> None:
    """Write the validated manifest to the installed tree.

    Same content as the tarball entry, but normalised through the
    Pydantic round-trip so on-disk inspection sees the canonical form.
    """
    text = yaml.safe_dump(
        manifest.model_dump(mode="json"),
        sort_keys=False,
        default_flow_style=False,
        width=999,
    )
    (install_path / MANIFEST_NAME).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install(
    pkg_path: Path,
    *,
    registry: Registry | None = None,
) -> InstallResult:
    """Install the ``.accpkg`` at ``pkg_path``.

    Idempotent: re-installing the same ``(name, version)`` whose
    on-disk tree hash still matches is a no-op (returns
    ``was_already_installed=True``).

    Raises
    ------
    ContentHashMismatch
        Tarball content doesn't match the manifest's hash.
    MissingDependency
        A ``depends_on:`` entry isn't satisfied by the registry.
    UnsafePath
        Tar entry tried to escape the install root.
    AlreadyInstalled
        ``(name, version)`` is registered but its current content
        hash diverges from the new package — install refuses rather
        than overwriting.
    """
    pkg_path = pkg_path.resolve()
    if not pkg_path.is_file():
        raise InstallError(f"package not found: {pkg_path}")

    registry = registry or Registry()

    # Step 1: validate manifest
    with gzip.open(pkg_path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        manifest, _ = _read_manifest_from_tar(tar)

    # Step 2: content-tree hash check
    actual_hash = _content_tree_hash_from_tar(pkg_path)
    if actual_hash != manifest.content_sha256:
        raise ContentHashMismatch(
            f"content hash mismatch: manifest says "
            f"{manifest.content_sha256[:12]}..., computed "
            f"{actual_hash[:12]}..."
        )

    install_path = _resolve_install_path(registry.root, manifest)

    # Step 6 (early): idempotent re-install
    existing = registry.find(manifest.name, manifest.version)
    if existing is not None and Path(existing.install_path).is_dir():
        if existing.content_sha256 == actual_hash:
            logger.info(
                "%s@%s already installed at %s (idempotent re-install)",
                manifest.name, manifest.version, existing.install_path,
            )
            return InstallResult(
                entry=existing,
                manifest=manifest,
                install_path=Path(existing.install_path),
                was_already_installed=True,
            )
        raise AlreadyInstalled(
            f"{manifest.name}@{manifest.version} is registered with a "
            f"different content hash; remove the old install first"
        )

    # Step 3: dependency check
    _check_dependencies(manifest, registry)

    # Step 4: unpack
    if install_path.exists():
        # Stale path from a partial install — clear it before extraction.
        shutil.rmtree(install_path)
    _extract_safely(pkg_path, install_path)
    _write_manifest_copy(install_path, manifest)

    # Step 5: register
    entry = registry.make_entry(
        name=manifest.name,
        version=manifest.version,
        content_sha256=actual_hash,
        install_path=install_path,
    )
    registry.add(entry)

    logger.info(
        "installed %s@%s → %s", manifest.name, manifest.version, install_path
    )
    return InstallResult(
        entry=entry,
        manifest=manifest,
        install_path=install_path,
        was_already_installed=False,
    )


def installed_satisfying(
    registry: Registry, name: str, constraint: str
) -> Iterable[RegistryEntry]:
    """All registry entries for ``name`` whose version satisfies
    ``constraint``.  Convenience for the CLI's ``list`` subcommand
    and Stage-1 PROPOSE_INFUSE handler."""
    return [
        e for e in registry.find_by_name(name)
        if version_satisfies(e.version, constraint)
    ]
