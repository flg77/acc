"""Tests for the cosign signature verifier (Stage 0 slice 7).

The cosign binary is mocked via :func:`subprocess.run` patching so
tests don't require cosign installed.  A separate manual smoke test
(operator-run, after the acc1 hub bootstraps) exercises the real
binary against the live pilot keypair.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from acc.pkg.catalog import RequiredSigner
from acc.pkg.verify import (
    CosignNotInstalled,
    SignatureMissing,
    SignatureRejected,
    VerifyError,
    is_cosign_available,
    verify,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _keypair_signer(key_path: Path) -> RequiredSigner:
    return RequiredSigner(
        issuer="pilot-keypair",
        subject_pattern=".*",
        key_path=str(key_path),
    )


def _keyless_signer() -> RequiredSigner:
    return RequiredSigner(
        issuer="https://token.actions.githubusercontent.com",
        subject_pattern="^https://github\\.com/flg77/acc-ecosystem/",
    )


@pytest.fixture
def pkg_and_sig(tmp_path: Path):
    pkg = tmp_path / "x.accpkg"
    sig = tmp_path / "x.accpkg.sig"
    pkg.write_bytes(b"FAKE PKG")
    sig.write_text("FAKE SIG", encoding="utf-8")
    return pkg, sig


@pytest.fixture
def pubkey(tmp_path: Path) -> Path:
    p = tmp_path / "acc-pilot.pub"
    p.write_text(
        "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----\n",
        encoding="utf-8",
    )
    return p


def _ok_run(*a, **kw):
    return subprocess.CompletedProcess(
        args=a, returncode=0, stdout="Verified OK\n", stderr=""
    )


def _fail_run(stderr="signature verification failed", rc=1):
    def _run(*a, **kw):
        return subprocess.CompletedProcess(
            args=a, returncode=rc, stdout="", stderr=stderr
        )
    return _run


# ---------------------------------------------------------------------------
# Mode detection on RequiredSigner
# ---------------------------------------------------------------------------


def test_keypair_mode_detected(pubkey):
    sig = _keypair_signer(pubkey)
    assert sig.mode == "keypair"


def test_keyless_mode_detected():
    sig = _keyless_signer()
    assert sig.mode == "keyless"


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_verify_keypair_happy(pkg_and_sig, pubkey):
    pkg, sig = pkg_and_sig
    with patch("acc.pkg.verify.subprocess.run", side_effect=_ok_run), \
         patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        result = verify(pkg, sig, _keypair_signer(pubkey))
    assert result.ok
    assert result.mode == "keypair"
    assert "keypair:" in result.signer_identity


def test_verify_keyless_happy(pkg_and_sig):
    pkg, sig = pkg_and_sig
    with patch("acc.pkg.verify.subprocess.run", side_effect=_ok_run), \
         patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        result = verify(pkg, sig, _keyless_signer())
    assert result.ok
    assert result.mode == "keyless"
    assert "keyless:" in result.signer_identity


# ---------------------------------------------------------------------------
# Cosign command construction
# ---------------------------------------------------------------------------


def test_keypair_invokes_cosign_with_key_flag(pkg_and_sig, pubkey):
    pkg, sig = pkg_and_sig
    captured: list = []

    def _capture(cmd, **kw):
        captured.append(cmd)
        return _ok_run()

    with patch("acc.pkg.verify.subprocess.run", side_effect=_capture), \
         patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        verify(pkg, sig, _keypair_signer(pubkey))

    cmd = captured[0]
    assert "--key" in cmd
    key_idx = cmd.index("--key")
    assert cmd[key_idx + 1].endswith("acc-pilot.pub")
    assert "--certificate-oidc-issuer" not in cmd


def test_keyless_invokes_cosign_with_oidc_flags(pkg_and_sig):
    pkg, sig = pkg_and_sig
    captured: list = []

    def _capture(cmd, **kw):
        captured.append(cmd)
        return _ok_run()

    with patch("acc.pkg.verify.subprocess.run", side_effect=_capture), \
         patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        verify(pkg, sig, _keyless_signer())

    cmd = captured[0]
    assert "--certificate-oidc-issuer" in cmd
    assert "--certificate-identity-regexp" in cmd
    assert "--key" not in cmd
    # The subject regex must be the verbatim pattern from RequiredSigner
    pattern_idx = cmd.index("--certificate-identity-regexp")
    assert "flg77/acc-ecosystem" in cmd[pattern_idx + 1]


def test_cosign_command_includes_signature_and_pkg(pkg_and_sig, pubkey):
    pkg, sig = pkg_and_sig
    captured: list = []

    def _capture(cmd, **kw):
        captured.append(cmd)
        return _ok_run()

    with patch("acc.pkg.verify.subprocess.run", side_effect=_capture), \
         patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        verify(pkg, sig, _keypair_signer(pubkey))

    cmd = captured[0]
    assert "--signature" in cmd
    sig_idx = cmd.index("--signature")
    assert cmd[sig_idx + 1] == str(sig)
    assert str(pkg) in cmd


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------


def test_missing_signature_refused(tmp_path, pubkey):
    pkg = tmp_path / "x.accpkg"
    pkg.write_bytes(b"x")
    missing_sig = tmp_path / "missing.sig"
    with pytest.raises(SignatureMissing):
        verify(pkg, missing_sig, _keypair_signer(pubkey))


def test_cosign_failure_raises_signature_rejected(pkg_and_sig, pubkey):
    pkg, sig = pkg_and_sig
    with patch("acc.pkg.verify.subprocess.run",
               side_effect=_fail_run("bad cert")), \
         patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        with pytest.raises(SignatureRejected) as excinfo:
            verify(pkg, sig, _keypair_signer(pubkey))
    assert "bad cert" in excinfo.value.cosign_stderr


def test_cosign_not_installed_raises(pkg_and_sig, pubkey):
    pkg, sig = pkg_and_sig
    with patch("acc.pkg.verify.shutil.which", return_value=None):
        with pytest.raises(CosignNotInstalled):
            verify(pkg, sig, _keypair_signer(pubkey))


def test_keypair_mode_missing_pubkey_refused(pkg_and_sig, tmp_path):
    pkg, sig = pkg_and_sig
    signer = RequiredSigner(
        issuer="x", subject_pattern=".*",
        key_path=str(tmp_path / "missing.pub"),
    )
    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        with pytest.raises(VerifyError, match="missing pubkey"):
            verify(pkg, sig, signer)


def test_missing_pkg_file_refused(tmp_path, pubkey):
    pkg = tmp_path / "missing.accpkg"
    sig = tmp_path / "x.sig"
    sig.write_text("x", encoding="utf-8")
    with pytest.raises(VerifyError, match="not found"):
        verify(pkg, sig, _keypair_signer(pubkey))


# ---------------------------------------------------------------------------
# Cosign discovery
# ---------------------------------------------------------------------------


def test_cosign_env_override_used(monkeypatch, pkg_and_sig, pubkey):
    pkg, sig = pkg_and_sig
    monkeypatch.setenv("ACC_COSIGN_BIN", "/opt/my-cosign")

    captured = {}

    def _which(name):
        captured["which_arg"] = name
        return "/opt/my-cosign"

    with patch("acc.pkg.verify.shutil.which", side_effect=_which), \
         patch("acc.pkg.verify.subprocess.run", side_effect=_ok_run):
        verify(pkg, sig, _keypair_signer(pubkey))
    assert captured["which_arg"] == "/opt/my-cosign"


def test_is_cosign_available_true_when_present():
    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"):
        assert is_cosign_available() is True


def test_is_cosign_available_false_when_missing():
    with patch("acc.pkg.verify.shutil.which", return_value=None):
        assert is_cosign_available() is False
