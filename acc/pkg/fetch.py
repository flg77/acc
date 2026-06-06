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
from acc.pkg.install import InstallResult, install as _install
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

        # Verify before install — signing floor.
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

        install_result = _install(tarball_path, registry=registry)

    return FetchResult(install=install_result, resolved=resolved)
