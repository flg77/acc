"""Cosign signature verification — Stage 0 slice 7.

This module enforces the **signing floor** described in brainstorm
Q3b: every ``.accpkg`` install verifies a cosign signature against
the resolving catalog's :class:`RequiredSigner` BEFORE the installer
unpacks anything.  No tier is exempt — community packages are signed
too; tiers differ only in policy depth, not signing presence.

Two verification modes
----------------------

* **Keypair** (Stage 0 pilot): the catalog declares
  ``required_signer.key_path = "/path/to/cosign.pub"``.  We invoke::

      cosign verify-blob --key <pub> --signature <sig> <pkg>

* **Keyless** (Stage 1+): the catalog declares
  ``required_signer.{issuer, subject_pattern}``.  We invoke::

      cosign verify-blob \\
        --certificate-oidc-issuer <issuer> \\
        --certificate-identity-regexp <subject_pattern> \\
        --signature <sig> <pkg>

What Stage 0 does NOT enforce
-----------------------------

Enterprise Contract policy depth (build provenance + eval-pass
attestations + Cat-A/B/C smoke) is Stage 1.  When the catalog
advertises attestations beyond the bare signature, ``verify()``
emits an INFO-level message that the EC policy check is a Stage-1
feature; install still proceeds.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from acc.pkg.catalog import RequiredSigner

logger = logging.getLogger("acc.pkg.verify")

COSIGN_BIN_ENV = "ACC_COSIGN_BIN"
COSIGN_DEFAULT_BIN = "cosign"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VerifyError(Exception):
    """Base for all verification failures."""


class CosignNotInstalled(VerifyError):
    """The ``cosign`` binary isn't on PATH (or at ``ACC_COSIGN_BIN``)."""


class SignatureRejected(VerifyError):
    """Cosign refused the signature.  Carries the cosign stderr for audit."""

    def __init__(self, msg: str, cosign_stderr: str) -> None:
        super().__init__(msg)
        self.cosign_stderr = cosign_stderr


class SignatureMissing(VerifyError):
    """No signature artefact was supplied AND the catalog requires signing.

    Stage 0 default: signing is required.  Operator override via the
    CLI ``--allow-unsigned`` flag is operator-explicit and
    audit-logged (slice 8).
    """


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    mode: str                       # "keypair" | "keyless"
    cosign_stdout: str
    cosign_stderr: str
    signer_identity: str            # human-readable: pubkey path or OIDC identity


# ---------------------------------------------------------------------------
# Cosign binary discovery
# ---------------------------------------------------------------------------


def _cosign_bin() -> str:
    """Return the cosign executable path; raise if not found."""
    import os
    raw = os.environ.get(COSIGN_BIN_ENV, "").strip()
    candidate = raw or COSIGN_DEFAULT_BIN
    found = shutil.which(candidate)
    if not found:
        raise CosignNotInstalled(
            f"cosign binary not found on PATH "
            f"(set {COSIGN_BIN_ENV} to override; default tries {COSIGN_DEFAULT_BIN!r})"
        )
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify(
    pkg_path: Path,
    signature_path: Path,
    required_signer: RequiredSigner,
) -> VerifyResult:
    """Verify ``pkg_path`` against ``signature_path`` for ``required_signer``.

    Returns a :class:`VerifyResult` on success.  Raises:

    * :class:`SignatureMissing` if ``signature_path`` doesn't exist.
    * :class:`CosignNotInstalled` if cosign isn't on PATH.
    * :class:`SignatureRejected` if cosign returns non-zero (carries
      the cosign stderr for audit).
    """
    pkg_path = pkg_path.resolve()
    if not pkg_path.is_file():
        raise VerifyError(f"package not found: {pkg_path}")
    if not signature_path.is_file():
        raise SignatureMissing(
            f"signature not found at {signature_path}; the signing floor "
            "requires every install to carry a cosign signature "
            "(override with the operator-explicit --allow-unsigned)"
        )

    cosign = _cosign_bin()
    cmd = [cosign, "verify-blob"]
    signer_identity: str
    if required_signer.mode == "keypair":
        key = Path(required_signer.key_path).resolve()
        if not key.is_file():
            raise VerifyError(
                f"keypair-mode catalog points at missing pubkey: {key}"
            )
        cmd += ["--key", str(key)]
        signer_identity = f"keypair:{key.name}"
    else:
        cmd += [
            "--certificate-oidc-issuer", required_signer.issuer,
            "--certificate-identity-regexp", required_signer.subject_pattern,
        ]
        signer_identity = (
            f"keyless:{required_signer.issuer} ~ {required_signer.subject_pattern}"
        )

    cmd += ["--signature", str(signature_path), str(pkg_path)]

    logger.debug("running %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise CosignNotInstalled(f"failed to exec cosign: {exc}") from exc

    if result.returncode != 0:
        raise SignatureRejected(
            f"cosign rejected the signature (rc={result.returncode}); "
            f"signer={signer_identity}",
            cosign_stderr=result.stderr,
        )

    logger.info(
        "verified %s against %s (mode=%s)",
        pkg_path.name, signer_identity, required_signer.mode,
    )
    logger.info(
        "WARNING: Enterprise Contract policy depth (eval + Cat-A/B/C "
        "attestations) is a Stage 1 feature; Stage 0 verifies the bare "
        "cosign signature only."
    )

    return VerifyResult(
        ok=True,
        mode=required_signer.mode,
        cosign_stdout=result.stdout,
        cosign_stderr=result.stderr,
        signer_identity=signer_identity,
    )


def is_cosign_available() -> bool:
    """Check whether the cosign binary is reachable.

    Useful for the CLI to print a helpful "cosign not installed; install
    it before running verify/install" message rather than a stack
    trace.
    """
    try:
        _cosign_bin()
        return True
    except CosignNotInstalled:
        return False
