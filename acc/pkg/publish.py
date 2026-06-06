"""OIDC keyless publish — Stage 1.3.

Brainstorm Q8 calls out OIDC keyless via Fulcio as the
"afternoon-setup" path for community publishers — no CA
infrastructure, no key rotation, identity bound to a GitHub Actions
(or other OIDC provider) credential.

This module wraps two cosign-binary operations:

* ``cosign sign-blob --identity-token <token> <tarball>`` — signs
  the tarball using Fulcio to mint a short-lived X.509 cert bound
  to the OIDC identity, then writes the signature + cert to disk
  and records the event in Rekor (the public transparency log).

* ``cosign attest-blob --predicate <file> --type <slsa|custom> ...``
  (deferred to 1.3b once Konflux pipeline lands) — attaches an
  attestation (SLSA provenance, eval-pass, etc.) to the same blob.

The OIDC token discovery follows cosign's convention:

  1. ``SIGSTORE_ID_TOKEN`` env (operator pre-fetched)
  2. ``ACTIONS_ID_TOKEN_REQUEST_URL`` / ``ACTIONS_ID_TOKEN_REQUEST_TOKEN``
     (GitHub Actions workflow)
  3. ``cosign sign-blob`` interactive browser flow (operator
     terminal — out of scope for the CLI, see ``acc-pkg login``)

Stage 1.3 ships the **sign + push** path against an HTTPS catalog
endpoint that accepts an authenticated PUT.  The Stage 0 manual
``kubectl cp`` / ``index.json`` ConfigMap patch flow remains
available; ``acc-pkg publish`` adds the automated alternative.

The Konflux pipeline template at
``gitops/tekton/pipelines/accpkg-build.yaml`` (also in this slice)
strings the full chain: clone → build → Tekton-Chains attest →
cosign sign (via this module) → upload.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from acc.pkg.verify import COSIGN_BIN_ENV, COSIGN_DEFAULT_BIN

logger = logging.getLogger("acc.pkg.publish")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PublishError(Exception):
    """Base for publish failures."""


class OIDCTokenMissing(PublishError):
    """No OIDC token available from env or GitHub Actions."""


class CosignSignFailed(PublishError):
    """``cosign sign-blob`` returned non-zero."""

    def __init__(self, msg: str, cosign_stderr: str) -> None:
        super().__init__(msg)
        self.cosign_stderr = cosign_stderr


class CatalogUploadFailed(PublishError):
    """HTTPS PUT against the catalog endpoint failed."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignArtefacts:
    """Output of :func:`sign_blob`."""

    signature_path: Path
    certificate_path: Path
    rekor_log_index: Optional[int] = None


@dataclass(frozen=True)
class PublishResult:
    """Output of :func:`publish`."""

    tarball_url: str
    signature_url: str
    rekor_log_index: Optional[int]


# ---------------------------------------------------------------------------
# OIDC token discovery
# ---------------------------------------------------------------------------


def resolve_oidc_token() -> Optional[str]:
    """Find an OIDC token from the conventional sources.

    Returns ``None`` when no token is available; caller decides
    whether to fall back to the interactive browser flow (which
    cosign drives itself).
    """
    explicit = os.environ.get("SIGSTORE_ID_TOKEN", "").strip()
    if explicit:
        return explicit

    # GitHub Actions workflow runner — cosign supports the env-derived
    # token fetch when ACTIONS_ID_TOKEN_REQUEST_URL is set, but only
    # via its built-in helper.  We just signal availability.
    if os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip():
        return "${ACTIONS_TOKEN}"   # sentinel: cosign will fetch internally
    return None


# ---------------------------------------------------------------------------
# Cosign binary discovery (shared with verify)
# ---------------------------------------------------------------------------


def _cosign_bin() -> str:
    raw = os.environ.get(COSIGN_BIN_ENV, "").strip()
    candidate = raw or COSIGN_DEFAULT_BIN
    found = shutil.which(candidate)
    if not found:
        raise PublishError(
            f"cosign binary not found on PATH (set {COSIGN_BIN_ENV} to override)"
        )
    return found


# ---------------------------------------------------------------------------
# sign_blob — OIDC keyless via Fulcio + Rekor
# ---------------------------------------------------------------------------


def sign_blob(
    tarball_path: Path,
    *,
    output_dir: Optional[Path] = None,
    oidc_issuer: str = "https://oauth2.sigstore.dev/auth",
    identity_token: Optional[str] = None,
) -> SignArtefacts:
    """Sign ``tarball_path`` keylessly via Fulcio; record in Rekor.

    Writes ``<tarball>.sig`` and ``<tarball>.pem`` next to the
    tarball (or in ``output_dir`` if provided).  Returns the two
    artefact paths + the Rekor log index (parsed from cosign's
    output if present).

    Parameters
    ----------
    tarball_path
        Path to the ``.accpkg`` tarball to sign.
    output_dir
        Where to write the ``.sig`` + ``.pem`` (default: alongside
        the tarball).
    oidc_issuer
        Sigstore OIDC issuer URL.  Defaults to the public Sigstore
        OAuth endpoint; air-gap operators substitute their own
        Trusted Artifact Signer instance via this kwarg or the
        ``SIGSTORE_OIDC_ISSUER`` env var.
    identity_token
        Pre-fetched OIDC token.  When ``None``, cosign discovers via
        its own conventions (env + GHA + interactive).
    """
    tarball_path = tarball_path.resolve()
    if not tarball_path.is_file():
        raise PublishError(f"tarball not found: {tarball_path}")

    output_dir = (output_dir or tarball_path.parent).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sig_path = output_dir / (tarball_path.name + ".sig")
    cert_path = output_dir / (tarball_path.name + ".pem")

    cosign = _cosign_bin()
    issuer = os.environ.get("SIGSTORE_OIDC_ISSUER", "").strip() or oidc_issuer
    cmd = [
        cosign, "sign-blob",
        "--yes",                  # non-interactive — skip confirmation prompt
        "--oidc-issuer", issuer,
        "--output-signature", str(sig_path),
        "--output-certificate", str(cert_path),
    ]
    if identity_token:
        # cosign's --identity-token flag accepts the JWT directly OR
        # the shell-expanded form ``${ACTIONS_TOKEN}`` (which it
        # resolves itself when ACTIONS_ID_TOKEN_REQUEST_URL is set).
        cmd += ["--identity-token", identity_token]
    cmd += [str(tarball_path)]

    logger.debug("publish: running %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise PublishError(f"failed to exec cosign: {exc}") from exc

    if result.returncode != 0:
        raise CosignSignFailed(
            f"cosign sign-blob failed (rc={result.returncode})",
            cosign_stderr=result.stderr,
        )

    # Cosign prints "tlog entry created with index: <N>" on stderr.
    log_index: Optional[int] = None
    for line in (result.stderr or "").splitlines():
        if "tlog entry created with index" in line:
            try:
                log_index = int(line.rsplit(":", 1)[-1].strip())
            except (ValueError, IndexError):
                log_index = None
            break

    logger.info(
        "publish: signed %s (signature=%s, rekor_index=%s)",
        tarball_path.name, sig_path.name, log_index,
    )
    return SignArtefacts(
        signature_path=sig_path,
        certificate_path=cert_path,
        rekor_log_index=log_index,
    )


# ---------------------------------------------------------------------------
# publish — sign + upload to catalog endpoint
# ---------------------------------------------------------------------------


def publish(
    tarball_path: Path,
    catalog_url: str,
    *,
    token: Optional[str] = None,
    output_dir: Optional[Path] = None,
    oidc_issuer: str = "https://oauth2.sigstore.dev/auth",
) -> PublishResult:
    """Sign the tarball + upload tarball/signature/cert to ``catalog_url``.

    The catalog endpoint must accept authenticated PUT requests at:

    * ``<catalog_url>/upload/<scope>/<name>-<version>.accpkg``
    * ``<catalog_url>/upload/<scope>/<name>-<version>.accpkg.sig``
    * ``<catalog_url>/upload/<scope>/<name>-<version>.accpkg.pem``

    ``token`` is sent as ``Authorization: Bearer <token>`` if
    supplied (operator-controlled per catalog).

    Returns the URLs the catalog now serves the package + signature
    from (the operator publishes these in the catalog's
    ``index.json``).
    """
    artefacts = sign_blob(
        tarball_path, output_dir=output_dir,
        oidc_issuer=oidc_issuer,
    )

    base = catalog_url.rstrip("/") + "/upload/"
    tarball_url = base + tarball_path.name
    signature_url = base + artefacts.signature_path.name
    cert_url = base + artefacts.certificate_path.name

    _http_put(tarball_url, tarball_path.read_bytes(), token=token)
    _http_put(signature_url, artefacts.signature_path.read_bytes(), token=token)
    _http_put(cert_url, artefacts.certificate_path.read_bytes(), token=token)

    return PublishResult(
        tarball_url=tarball_url,
        signature_url=signature_url,
        rekor_log_index=artefacts.rekor_log_index,
    )


def _http_put(url: str, data: bytes, *, token: Optional[str] = None) -> None:
    """PUT ``data`` to ``url`` with optional bearer auth."""
    req = urllib.request.Request(url, data=data, method="PUT")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status not in (200, 201, 204):
                raise CatalogUploadFailed(
                    f"unexpected status {response.status} from {url}"
                )
    except urllib.error.HTTPError as exc:
        raise CatalogUploadFailed(
            f"HTTP {exc.code} {exc.reason} on PUT {url}: {exc.read().decode('utf-8', 'replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise CatalogUploadFailed(
            f"failed to PUT {url}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# login — print OIDC setup hints
# ---------------------------------------------------------------------------


def login_hint() -> dict[str, object]:
    """Return a structured hint for the operator about OIDC setup.

    Stage 1.3 doesn't broker the OIDC flow itself — cosign drives it
    when invoked directly.  ``acc-pkg login`` surfaces what's
    available (env tokens, GHA, browser-cosign) so the operator
    knows whether the next ``acc-pkg publish`` will succeed without
    extra interaction.
    """
    explicit = bool(os.environ.get("SIGSTORE_ID_TOKEN"))
    gha = bool(os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"))
    issuer = (
        os.environ.get("SIGSTORE_OIDC_ISSUER", "").strip()
        or "https://oauth2.sigstore.dev/auth"
    )
    return {
        "issuer": issuer,
        "sigstore_id_token_set": explicit,
        "github_actions_token_available": gha,
        "interactive_browser_fallback_available": shutil.which("cosign") is not None,
        "ready_to_publish": explicit or gha,
    }


__all__ = [
    "PublishError",
    "OIDCTokenMissing",
    "CosignSignFailed",
    "CatalogUploadFailed",
    "SignArtefacts",
    "PublishResult",
    "resolve_oidc_token",
    "sign_blob",
    "publish",
    "login_hint",
]
