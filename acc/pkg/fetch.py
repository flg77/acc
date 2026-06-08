"""Catalog-aware fetch + verify + install — Stage 1.5.3 helper.

Single entry point used by both:

* Stage 1.5.3 (``acc-cli collective pkg-install``) — installs
  ``required_packages:`` declared in a ``collective.yaml`` at boot
  time (``acc-deploy.sh apply`` wraps this).
* Stage 1.4 (``PROPOSE_INFUSE`` handler in
  ``acc/assistant_proposal.py``) — installs a single ``@scope/name``
  after Compliance pane approval.

Flow
----

1. Resolve ``@scope/name`` (+ optional constraint) against layered
   catalogs.  Picks highest version satisfying the constraint within
   the highest-priority catalog.
2. Materialise the tarball + signature locally — either by HTTPS GET
   (``mode: https`` catalog) or by reading the on-disk file
   (``mode: file`` catalog).  Files go to a tmpdir; nothing pollutes
   the install root until step 4.
3. Verify the cosign signature against the resolving catalog's
   ``required_signer``.  REFUSES on signer mismatch (signing floor
   non-negotiable per brainstorm Q3b) — unless the operator passed
   the audit-logged ``allow_unsigned=True``.
4. Hand off to :func:`acc.pkg.install.install` for the full content-
   hash check + topo-sort + unpack + registry update.

Returns the :class:`InstallResult` so callers can surface the entry
to the operator (Compliance pane row, acc-deploy log line, etc.).
"""

from __future__ import annotations

import logging
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from acc.pkg.catalog import (
    Catalog,
    CatalogIndexEntry,
    ResolvedPackage,
    resolve_constraint,
)
from acc.pkg.install import (
    InstallResult,
    install as _install,
    installed_satisfying,
    read_manifest,
)
from acc.pkg.registry import Registry
from acc.pkg.verify import VerifyError, verify as _verify

logger = logging.getLogger("acc.pkg.fetch")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Base for fetch-helper failures."""


class CatalogResolutionFailed(FetchError):
    """No catalog advertises a version satisfying the constraint."""


class TarballDownloadFailed(FetchError):
    """HTTPS download or local-file read failed for the tarball/signature."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchResult:
    install: InstallResult
    resolved: ResolvedPackage


# ---------------------------------------------------------------------------
# Materialisation per catalog mode
# ---------------------------------------------------------------------------


def _materialise(
    resolved: ResolvedPackage, dest_dir: Path
) -> tuple[Path, Path | None]:
    """Place tarball + signature in ``dest_dir`` and return their paths.

    Signature path is ``None`` if the catalog entry doesn't supply
    one (caller decides whether to refuse).
    """
    cat = resolved.catalog
    entry = resolved.entry
    name_safe = entry.name.replace("/", "__").replace("@", "")
    tarball_dest = dest_dir / f"{name_safe}-{entry.version}.accpkg"
    sig_dest: Path | None = None

    if cat.mode == "https":
        # tarball_url is documented to be relative to the catalog url
        # OR a fully-qualified URL.  Support both shapes.
        tarball_url = (
            entry.tarball_url
            if entry.tarball_url.startswith("http")
            else cat.url.rstrip("/") + "/" + entry.tarball_url.lstrip("/")
        )
        _download(tarball_url, tarball_dest)
        if entry.signature_url:
            sig_url = (
                entry.signature_url
                if entry.signature_url.startswith("http")
                else cat.url.rstrip("/") + "/" + entry.signature_url.lstrip("/")
            )
            sig_dest = dest_dir / (tarball_dest.name + ".sig")
            _download(sig_url, sig_dest)
    else:  # file mode
        src = Path(entry.tarball_path)
        if not src.is_file():
            raise TarballDownloadFailed(
                f"file catalog {cat.id} pointed at missing tarball: {src}"
            )
        tarball_dest.write_bytes(src.read_bytes())
        if entry.signature_path:
            sig_src = Path(entry.signature_path)
            if sig_src.is_file():
                sig_dest = dest_dir / (tarball_dest.name + ".sig")
                sig_dest.write_bytes(sig_src.read_bytes())

    return tarball_dest, sig_dest


def _download(url: str, dest: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            dest.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise TarballDownloadFailed(
            f"failed to download {url}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_and_install(
    name: str,
    constraint: str = ">=0.0.0",
    *,
    workspace: Path | None = None,
    registry: Registry | None = None,
    allow_unsigned: bool = False,
) -> FetchResult:
    """Resolve, fetch, verify, install ``@scope/name`` matching ``constraint``.

    Parameters
    ----------
    name
        Scoped package name (``@acc/coding-roles``).
    constraint
        Semver constraint (default ``">=0.0.0"`` — match anything).
    workspace
        Optional workspace dir whose ``.acc/catalogs.yaml`` is
        layered on top of the user + system catalogs.
    registry
        Override the install target's registry (tests use a tmp root).
    allow_unsigned
        Operator-explicit + audit-logged bypass of the signing floor.
        Default ``False``.

    Raises
    ------
    CatalogResolutionFailed
        No catalog advertises a version satisfying the constraint.
    TarballDownloadFailed
        HTTPS download or local-file read failed.
    SignatureMissing / SignatureRejected
        Signing-floor violation (propagated from :mod:`acc.pkg.verify`).
    ContentHashMismatch / MissingDependency / etc.
        Propagated from :mod:`acc.pkg.install`.
    """
    resolved = resolve_constraint(name, constraint, workspace=workspace)
    if resolved is None:
        raise CatalogResolutionFailed(
            f"no catalog advertises {name} matching {constraint!r}"
        )

    logger.info(
        "fetch: %s@%s from catalog %s (tier=%s)",
        resolved.entry.name,
        resolved.entry.version,
        resolved.catalog.id,
        resolved.catalog.tier,
    )

    with tempfile.TemporaryDirectory(prefix="acc-pkg-fetch-") as tmp:
        tmp_dir = Path(tmp)
        tarball_path, sig_path = _materialise(resolved, tmp_dir)
        install_result = _verify_and_install(
            resolved,
            tarball_path,
            sig_path,
            allow_unsigned=allow_unsigned,
            registry=registry,
        )

    return FetchResult(install=install_result, resolved=resolved)


# ---------------------------------------------------------------------------
# Verify + install (shared by single-package and closure paths)
# ---------------------------------------------------------------------------


def _verify_and_install(
    resolved: ResolvedPackage,
    tarball_path: Path,
    sig_path: Path | None,
    *,
    allow_unsigned: bool,
    registry: Registry | None,
) -> InstallResult:
    """Enforce the signing floor, then install the materialised tarball."""
    if allow_unsigned:
        logger.warning(
            "AUDIT: --allow-unsigned bypass for %s@%s by caller",
            resolved.entry.name, resolved.entry.version,
        )
    else:
        if sig_path is None or not sig_path.is_file():
            raise VerifyError(
                f"catalog {resolved.catalog.id} did not provide a "
                "signature for "
                f"{resolved.entry.name}@{resolved.entry.version}; "
                "the signing floor is non-negotiable — pass "
                "allow_unsigned=True only with audit-logged approval"
            )
        _verify(tarball_path, sig_path, resolved.catalog.required_signer)

    return _install(tarball_path, registry=registry)


# ---------------------------------------------------------------------------
# Transitive dependency closure
# ---------------------------------------------------------------------------


_MAX_CLOSURE_DEPTH = 16


def fetch_and_install_closure(
    name: str,
    constraint: str = ">=0.0.0",
    *,
    workspace: Path | None = None,
    registry: Registry | None = None,
    allow_unsigned: bool = False,
) -> FetchResult:
    """Like :func:`fetch_and_install`, but install the full ``depends_on`` closure.

    Resolves ``@scope/name`` matching ``constraint``, then — before
    installing it — recursively fetches + verifies + installs every
    ``depends_on:`` entry not already satisfied in the registry, in
    dependency order (children first).  This is what makes an umbrella
    meta-pack (e.g. ``@acc/business-roles@^2.0`` → the seven domain
    packs) install as a single ``required_packages:`` entry.

    The single-package :func:`fetch_and_install` (and the installer's
    own ``_check_dependencies``) refuse a package whose deps aren't
    present; this helper satisfies them first.

    Cycles and runaway depth are guarded (``MissingDependency`` will
    surface for genuinely unsatisfiable circular deps).

    Returns the :class:`FetchResult` for the requested top-level package.
    """
    registry = registry or Registry()
    result = _install_closure(
        name,
        constraint,
        workspace=workspace,
        registry=registry,
        allow_unsigned=allow_unsigned,
        visited=set(),
        depth=0,
    )
    assert result is not None  # top-level is never a pre-visited cycle node
    return result


def _install_closure(
    name: str,
    constraint: str,
    *,
    workspace: Path | None,
    registry: Registry,
    allow_unsigned: bool,
    visited: set[tuple[str, str]],
    depth: int,
) -> FetchResult | None:
    if depth > _MAX_CLOSURE_DEPTH:
        raise FetchError(
            f"dependency closure exceeded max depth {_MAX_CLOSURE_DEPTH} "
            f"while resolving {name}"
        )

    resolved = resolve_constraint(name, constraint, workspace=workspace)
    if resolved is None:
        raise CatalogResolutionFailed(
            f"no catalog advertises {name} matching {constraint!r}"
        )

    key = (resolved.entry.name, resolved.entry.version)
    if key in visited:
        # Already handled earlier in this closure pass (diamond / cycle
        # guard).  Deps don't consume the return value.
        return None
    visited.add(key)

    logger.info(
        "fetch (closure): %s@%s from catalog %s (tier=%s)",
        resolved.entry.name,
        resolved.entry.version,
        resolved.catalog.id,
        resolved.catalog.tier,
    )

    with tempfile.TemporaryDirectory(prefix="acc-pkg-fetch-") as tmp:
        tarball_path, sig_path = _materialise(resolved, Path(tmp))

        # Install unsatisfied dependencies first (children before parent).
        manifest = read_manifest(tarball_path)
        for dep in manifest.depends_on:
            if list(installed_satisfying(registry, dep.name, dep.version)):
                continue
            _install_closure(
                dep.name,
                dep.version,
                workspace=workspace,
                registry=registry,
                allow_unsigned=allow_unsigned,
                visited=visited,
                depth=depth + 1,
            )

        install_result = _verify_and_install(
            resolved,
            tarball_path,
            sig_path,
            allow_unsigned=allow_unsigned,
            registry=registry,
        )

    return FetchResult(install=install_result, resolved=resolved)
