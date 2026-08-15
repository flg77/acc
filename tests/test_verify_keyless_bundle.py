"""Keyless cosign verification must pass the sigstore bundle.

A detached ``.sig`` is only the signature bytes.  In keyless mode cosign has
no key and no certificate, so it refuses outright::

    Error: provide a key with --key or --sk, a certificate to verify against
    with --certificate, or a bundle with --bundle

ACC surfaced that as ``SignatureRejected`` — indistinguishable from a genuinely
BAD signature — so **keyless verification could never succeed** and the failure
read like a broken signature (acc-spearhead#92).

It stayed hidden because the only two paths that reach cosign both dodge it:
``operator_mode=dev`` skips verification entirely via ``--allow-unsigned``, and
keypair mode passes ``--key``, which satisfies cosign.  Only prod + keyless —
the configuration that actually matters — hits the broken line.

Verified against the real artefact on lighthouse (2026-08-14),
``@acc/research-roles@1.0.2`` from the signed acc-ecosystem catalog:

* ``--signature`` alone  → cosign refuses (no key/cert/bundle)
* ``--bundle``           → ``Verified OK``
* ``--bundle`` + wrong-repo identity regexp → rejected
* ``--bundle`` + one appended byte          → rejected (payload mismatch)

These tests pin the argv so the flag cannot be dropped again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from acc.pkg.catalog import RequiredSigner
from acc.pkg.verify import SignatureMissing, verify

KEYLESS = RequiredSigner(
    issuer="https://token.actions.githubusercontent.com",
    subject_pattern=r"^https://github\.com/flg77/acc-ecosystem/",
)


@pytest.fixture
def artefacts(tmp_path: Path):
    pkg = tmp_path / "research-roles-1.0.2.accpkg"
    pkg.write_bytes(b"tarball")
    sig = tmp_path / (pkg.name + ".sig")
    sig.write_text("c2ln")
    bundle = tmp_path / (pkg.name + ".bundle")
    bundle.write_text('{"base64Signature":"x","cert":"y","rekorBundle":{}}')
    return pkg, sig, bundle


def _argv(mock_run) -> list[str]:
    return list(mock_run.call_args.args[0])


class TestKeylessUsesBundle:
    def test_bundle_is_passed_when_present(self, artefacts):
        pkg, sig, bundle = artefacts
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            verify(pkg, sig, KEYLESS, bundle_path=bundle)
        argv = _argv(run)
        assert "--bundle" in argv, (
            "keyless verify omitted --bundle; cosign cannot verify without a "
            "cert and fails with 'provide a key ... or a bundle'"
        )
        assert argv[argv.index("--bundle") + 1] == str(bundle.resolve())

    def test_signature_not_passed_alongside_bundle(self, artefacts):
        """cosign rejects --signature together with --bundle."""
        pkg, sig, bundle = artefacts
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            verify(pkg, sig, KEYLESS, bundle_path=bundle)
        assert "--signature" not in _argv(run)

    def test_identity_constraints_still_sent(self, artefacts):
        """The bundle must not become a way to skip identity checking."""
        pkg, sig, bundle = artefacts
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            verify(pkg, sig, KEYLESS, bundle_path=bundle)
        argv = _argv(run)
        assert "--certificate-oidc-issuer" in argv
        assert argv[argv.index("--certificate-oidc-issuer") + 1] == KEYLESS.issuer
        assert "--certificate-identity-regexp" in argv
        assert argv[argv.index("--certificate-identity-regexp") + 1] == KEYLESS.subject_pattern

    def test_falls_back_to_signature_without_bundle(self, artefacts):
        pkg, sig, _ = artefacts
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            verify(pkg, sig, KEYLESS)
        argv = _argv(run)
        assert "--signature" in argv and "--bundle" not in argv


class TestKeypairUnaffected:
    def test_keypair_still_uses_key_and_signature(self, artefacts, tmp_path):
        pkg, sig, bundle = artefacts
        key = tmp_path / "cosign.pub"
        key.write_text("-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----")
        signer = RequiredSigner(issuer="audit", subject_pattern=".*", key_path=str(key))
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            verify(pkg, sig, signer, bundle_path=bundle)
        argv = _argv(run)
        assert "--key" in argv and "--signature" in argv
        # keypair is the air-gap tier: no Rekor entry, and the bundle is a
        # keyless artefact — it must not leak into this path.
        assert "--bundle" not in argv
        assert "--insecure-ignore-tlog" in argv


class TestSigningFloorHolds:
    def test_missing_both_artefacts_raises(self, tmp_path):
        pkg = tmp_path / "p.accpkg"
        pkg.write_bytes(b"x")
        with pytest.raises(SignatureMissing):
            verify(pkg, tmp_path / "absent.sig", KEYLESS)

    def test_bundle_alone_satisfies_the_floor(self, artefacts):
        """A publisher may ship only the bundle — that is still signed."""
        pkg, sig, bundle = artefacts
        sig.unlink()
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            verify(pkg, sig, KEYLESS, bundle_path=bundle)
        assert "--bundle" in _argv(run)


class TestBundleOnlyPublisher:
    """A catalog may publish a bundle and no detached .sig at all."""

    def test_signature_path_none_with_bundle(self, artefacts):
        pkg, _, bundle = artefacts
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            verify(pkg, None, KEYLESS, bundle_path=bundle)
        assert "--bundle" in _argv(run)

    def test_signature_path_none_without_bundle_raises(self, artefacts):
        pkg, _, _ = artefacts
        with pytest.raises(SignatureMissing):
            verify(pkg, None, KEYLESS)
