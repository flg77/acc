"""Catalog store — verify → promote → index logic for the acc-catalog endpoint.

Framework-agnostic on purpose: :mod:`acc.catalog.server` is a thin FastAPI
shell over this class, so the trust-critical path (cosign verify before a
package is ever listed) is unit-testable with nothing but ``acc.pkg.*`` and a
real ``cosign`` binary — no fastapi/uvicorn needed.

The upload protocol matches the existing client, :func:`acc.pkg.publish.publish`,
which PUTs three artefacts to ``<catalog_url>/upload/<filename>``::

    <name>-<version>.accpkg        # the gzip tarball
    <name>-<version>.accpkg.sig    # detached cosign signature
    <name>-<version>.accpkg.pem    # Fulcio cert (keyless only; absent for keypair)

Artefacts land in ``<root>/staging`` as they arrive.  As soon as both the
``.accpkg`` and its ``.sig`` are present, :meth:`CatalogStore.stage` runs
:func:`acc.pkg.verify.verify` against the configured :class:`RequiredSigner`.

* **verify OK** → the triplet is moved into ``<root>/packages/<scope>/`` (the
  layout ``acc/pkg/catalog.py`` file-mode and the GitHub Pages https-mode both
  use), a ``.sha256`` sidecar + a recorded ``eval_pass`` attestation are
  written, and ``<root>/index.json`` is regenerated.
* **verify fails** → the staged triplet is deleted and :class:`RejectedUpload`
  is raised.  Nothing reaches the served tree or the index — *signed or it
  doesn't list* (marketplace draft §6.2).

The recorded ``eval_pass`` attestation (``.att.json``) satisfies the brief's
"record the eval_pass attestation so the install-time EC policy can gate it".
Wiring the install client to *fetch* that bundle (so EC runs against it at
install rather than the permissive local default) is a documented follow-on —
see ``docs/marketplace-design-DRAFT.md`` and lab-gitops backlog 008.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from acc.pkg.catalog import RequiredSigner
from acc.pkg.ec_policy import Attestation
from acc.pkg.install import read_manifest
from acc.pkg.verify import VerifyError, verify as _verify

logger = logging.getLogger("acc.catalog.store")

SCHEMA_VERSION = 1

_ACCPKG_SUFFIX = ".accpkg"
_SIG_SUFFIX = ".accpkg.sig"
_PEM_SUFFIX = ".accpkg.pem"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CatalogStoreError(Exception):
    """Base for catalog-store failures."""


class RejectedUpload(CatalogStoreError):
    """An upload was refused — bad name, unreadable manifest, or (the point of
    the whole exercise) a signature that cosign would not verify.  Carries an
    optional ``detail`` (e.g. the cosign stderr) for audit display.
    """

    def __init__(self, msg: str, detail: str = "") -> None:
        super().__init__(msg)
        self.detail = detail


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedPackage:
    """A package that passed verification and is now served + indexed."""

    name: str               # @scope/name (from the manifest — authoritative)
    version: str
    scope: str
    tarball_sha256: str     # sha256 of the .accpkg file bytes (64-hex)
    tarball_rel: str        # served path, e.g. /packages/<scope>/<file>
    signature_rel: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_artefact_name(filename: str) -> str:
    """Return ``filename`` if it is a bare, traversal-free artefact name.

    Uploads are addressed by filename only (no scope in the path — the client
    PUTs ``tarball_path.name``); the scope is derived from the manifest at
    promotion.  Reject anything with a path separator, ``..``, or a leading
    dot so a malicious filename can never escape the staging dir.
    """
    if not filename or filename != Path(filename).name:
        raise RejectedUpload(f"unsafe artefact filename: {filename!r}")
    if filename.startswith(".") or ".." in filename:
        raise RejectedUpload(f"unsafe artefact filename: {filename!r}")
    if not (
        filename.endswith(_ACCPKG_SUFFIX)
        or filename.endswith(_SIG_SUFFIX)
        or filename.endswith(_PEM_SUFFIX)
    ):
        raise RejectedUpload(
            f"unsupported artefact {filename!r} — expected "
            f"*.accpkg, *.accpkg.sig or *.accpkg.pem"
        )
    return filename


def _base_accpkg(filename: str) -> str:
    """Map any artefact filename to its ``.accpkg`` base name."""
    if filename.endswith(_SIG_SUFFIX) or filename.endswith(_PEM_SUFFIX):
        return filename[:-4]  # strip ".sig" / ".pem"
    return filename


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _split_scope(name: str) -> tuple[str, str]:
    """``@scope/pkg`` → ``("scope", "pkg")``."""
    if not (name.startswith("@") and "/" in name):
        raise RejectedUpload(f"manifest name is not @scope/name: {name!r}")
    scope, pkg = name[1:].split("/", 1)
    return scope, pkg


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class CatalogStore:
    """Filesystem-backed writable catalog rooted at ``root``.

    Layout::

        <root>/staging/                  # in-flight uploads
        <root>/packages/<scope>/<name>-<version>.accpkg(.sig/.pem/.sha256/.att.json)
        <root>/index.json                # regenerated on every promotion
    """

    def __init__(
        self,
        root: Path,
        *,
        required_signer: RequiredSigner,
        tier: str = "community",
        ec_policy_path: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.packages_dir = self.root / "packages"
        self.staging_dir = self.root / "staging"
        self.index_path = self.root / "index.json"
        self.required_signer = required_signer
        self.tier = tier
        self.ec_policy_path = ec_policy_path
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    # -- read path ----------------------------------------------------------

    def artefact_path(self, scope: str, filename: str) -> Path:
        """Resolve a served artefact path, refusing traversal."""
        scope_safe = _safe_segment(scope)
        file_safe = _safe_segment(filename)
        candidate = (self.packages_dir / scope_safe / file_safe).resolve()
        if not str(candidate).startswith(str(self.packages_dir.resolve()) + "/"):
            raise RejectedUpload(f"unsafe artefact path: {scope}/{filename}")
        return candidate

    def index(self) -> dict:
        """Return the current index document (rebuilding from disk if absent)."""
        if self.index_path.is_file():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return self.rebuild_index()

    # -- write path ---------------------------------------------------------

    def stage(self, filename: str, data: bytes) -> dict:
        """Accept one uploaded artefact; promote the package when verifiable.

        Returns a small status dict the HTTP layer echoes back to the client.
        Raises :class:`RejectedUpload` if the artefact name is unsafe or, once
        a verifiable (.accpkg + .sig) pair is present, if cosign rejects it.
        """
        safe = _safe_artefact_name(filename)
        (self.staging_dir / safe).write_bytes(data)
        base = _base_accpkg(safe)

        # A late-arriving .pem when the package is already promoted: attach it
        # next to the served tarball and we're done.
        if safe.endswith(_PEM_SUFFIX) and not (self.staging_dir / base).exists():
            attached = self._attach_late_artefact(base, safe)
            return {"staged": safe, "promoted": False, "attached": attached}

        published = self._try_promote(base)
        if published is None:
            return {"staged": safe, "promoted": False}
        return {
            "staged": safe,
            "promoted": True,
            "name": published.name,
            "version": published.version,
            "tarball_sha256": published.tarball_sha256,
            "tarball_url": published.tarball_rel,
            "signature_url": published.signature_rel,
        }

    def _try_promote(self, base: str) -> PublishedPackage | None:
        """Promote ``base`` if both its tarball and signature are staged."""
        acc = self.staging_dir / base
        sig = self.staging_dir / (base + ".sig")
        if not (acc.is_file() and sig.is_file()):
            return None
        pem = self.staging_dir / (base + ".pem")
        staged = [p for p in (acc, sig, pem) if p.is_file()]

        # Read the manifest BEFORE verifying so a corrupt tarball is reported
        # as a rejected upload rather than a stack trace.
        try:
            manifest = read_manifest(acc)
        except Exception as exc:  # noqa: BLE001 — any tar/yaml/validation error
            _unlink_all(staged)
            raise RejectedUpload(f"unreadable .accpkg manifest: {exc}") from exc

        scope, _pkg = _split_scope(manifest.name)

        try:
            _verify(
                acc, sig, self.required_signer,
                ec_policy_path=self.ec_policy_path,
            )
        except VerifyError as exc:
            _unlink_all(staged)
            detail = getattr(exc, "cosign_stderr", "") or ""
            raise RejectedUpload(
                f"signature verification failed for {manifest.name}@"
                f"{manifest.version}: {exc}",
                detail=detail,
            ) from exc

        sha = _sha256_file(acc)
        dest_dir = self.packages_dir / scope
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_acc = dest_dir / base
        dest_sig = dest_dir / (base + ".sig")

        shutil.move(str(acc), str(dest_acc))
        shutil.move(str(sig), str(dest_sig))
        if pem.is_file():
            shutil.move(str(pem), str(dest_dir / (base + ".pem")))

        # .sha256 sidecar — matches the file-mode catalog convention
        # (acc/pkg/catalog.py:_fetch_index_file) so the same tree can be served
        # over file:// or https://.
        (dest_dir / (base + ".sha256")).write_text(
            f"{sha}  {base}\n", encoding="utf-8"
        )
        self._record_attestation(dest_dir / (base + ".att.json"), sha)

        logger.info(
            "promoted %s@%s (scope=%s, sha=%s, signer=%s)",
            manifest.name, manifest.version, scope, sha[:12],
            self.required_signer.mode,
        )
        self.rebuild_index()

        return PublishedPackage(
            name=manifest.name,
            version=manifest.version,
            scope=scope,
            tarball_sha256=sha,
            tarball_rel=f"/packages/{scope}/{base}",
            signature_rel=f"/packages/{scope}/{base}.sig",
        )

    def _attach_late_artefact(self, base: str, artefact: str) -> bool:
        """Move a staged artefact (e.g. a late .pem) next to an already-served
        package.  Returns True if a home was found.
        """
        for scope_dir in self.packages_dir.iterdir():
            if scope_dir.is_dir() and (scope_dir / base).is_file():
                shutil.move(
                    str(self.staging_dir / artefact),
                    str(scope_dir / artefact),
                )
                return True
        return False

    def _record_attestation(self, path: Path, sha: str) -> None:
        """Record a minimal ``eval_pass`` attestation bundle for the package.

        P0 stamps a presence record keyed to the tarball sha; a real pipeline
        (006 / 008) replaces ``verdicts`` with the Stage-1.1 eval results.
        """
        att = Attestation(
            kind="eval_pass",
            sha256=sha,
            data={"recorded_by": "acc-catalog", "verdicts": {}},
        )
        path.write_text(
            json.dumps([att.model_dump()], indent=2) + "\n", encoding="utf-8"
        )

    def rebuild_index(self) -> dict:
        """Regenerate ``index.json`` from the served package tree.

        Schema matches ``acc/pkg/catalog.py:CatalogIndexEntry`` (https mode):
        ``{schema_version, tier, packages: [{name, version, tarball_sha256,
        tarball_url, signature_url}]}``.  URLs are relative to the catalog base
        (the client joins them with the catalog ``url``).
        """
        packages: list[dict] = []
        for scope_dir in sorted(self.packages_dir.iterdir()):
            if not scope_dir.is_dir():
                continue
            scope = scope_dir.name
            for accpkg in sorted(scope_dir.glob("*.accpkg")):
                entry = self._index_entry(scope, accpkg)
                if entry is not None:
                    packages.append(entry)
        doc = {
            "schema_version": SCHEMA_VERSION,
            "tier": self.tier,
            "packages": packages,
        }
        self.index_path.write_text(
            json.dumps(doc, indent=2) + "\n", encoding="utf-8"
        )
        return doc

    def _index_entry(self, scope: str, accpkg: Path) -> dict | None:
        sha_file = accpkg.with_suffix(".accpkg.sha256")
        sha = (
            sha_file.read_text(encoding="utf-8").strip().split()[0]
            if sha_file.is_file()
            else _sha256_file(accpkg)
        )
        try:
            manifest = read_manifest(accpkg)
            name, version = manifest.name, manifest.version
        except Exception as exc:  # noqa: BLE001
            logger.warning("index: skipping unreadable %s (%s)", accpkg.name, exc)
            return None
        sig = accpkg.with_suffix(".accpkg.sig")
        return {
            "name": name,
            "version": version,
            "tarball_sha256": sha,
            "tarball_url": f"/packages/{scope}/{accpkg.name}",
            "signature_url": (
                f"/packages/{scope}/{sig.name}" if sig.is_file() else ""
            ),
        }


def _safe_segment(segment: str) -> str:
    if not segment or segment != Path(segment).name or ".." in segment:
        raise RejectedUpload(f"unsafe path segment: {segment!r}")
    return segment


def _unlink_all(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "CatalogStore",
    "CatalogStoreError",
    "PublishedPackage",
    "RejectedUpload",
    "SCHEMA_VERSION",
]
