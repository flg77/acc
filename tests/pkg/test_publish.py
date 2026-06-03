"""Tests for the OIDC keyless publish helper (Stage 1.3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from acc.pkg.publish import (
    CatalogUploadFailed,
    CosignSignFailed,
    PublishError,
    SignArtefacts,
    login_hint,
    publish,
    resolve_oidc_token,
    sign_blob,
)


# ---------------------------------------------------------------------------
# resolve_oidc_token
# ---------------------------------------------------------------------------


def test_resolve_oidc_token_from_env(monkeypatch):
    monkeypatch.setenv("SIGSTORE_ID_TOKEN", "real-jwt-here")
    assert resolve_oidc_token() == "real-jwt-here"


def test_resolve_oidc_token_from_gha_returns_sentinel(monkeypatch):
    monkeypatch.delenv("SIGSTORE_ID_TOKEN", raising=False)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://gha")
    assert resolve_oidc_token() == "${ACTIONS_TOKEN}"


def test_resolve_oidc_token_none_when_unset(monkeypatch):
    monkeypatch.delenv("SIGSTORE_ID_TOKEN", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    assert resolve_oidc_token() is None


def test_resolve_oidc_token_explicit_wins_over_gha(monkeypatch):
    monkeypatch.setenv("SIGSTORE_ID_TOKEN", "explicit")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://gha")
    assert resolve_oidc_token() == "explicit"


# ---------------------------------------------------------------------------
# sign_blob
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_pkg(tmp_path):
    p = tmp_path / "coding-agent-0.1.0.accpkg"
    p.write_bytes(b"FAKE TARBALL CONTENTS")
    return p


def _cosign_ok(*a, **kw):
    # Honour cosign's --output-signature / --output-certificate by
    # writing stub bytes there — publish() reads these for upload.
    cmd = a[0] if a else kw.get("args", [])
    if isinstance(cmd, list):
        for flag in ("--output-signature", "--output-certificate"):
            if flag in cmd:
                idx = cmd.index(flag)
                if idx + 1 < len(cmd):
                    Path(cmd[idx + 1]).write_bytes(b"STUB " + flag.encode())
    return subprocess.CompletedProcess(
        args=a, returncode=0,
        stdout="",
        stderr=(
            "Using payload from: ...\n"
            "tlog entry created with index: 12345678\n"
            "Signature written in: <stdout>\n"
        ),
    )


def _cosign_fail(*a, **kw):
    return subprocess.CompletedProcess(
        args=a, returncode=1, stdout="", stderr="OIDC token rejected",
    )


def test_sign_blob_happy_returns_artefacts(fake_pkg):
    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_ok):
        artefacts = sign_blob(fake_pkg)
    assert artefacts.signature_path.name == "coding-agent-0.1.0.accpkg.sig"
    assert artefacts.certificate_path.name == "coding-agent-0.1.0.accpkg.pem"
    assert artefacts.rekor_log_index == 12345678


def test_sign_blob_invokes_cosign_with_correct_flags(fake_pkg):
    captured: list = []

    def _capture(cmd, **kw):
        captured.append(cmd)
        return _cosign_ok()

    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_capture):
        sign_blob(fake_pkg)
    cmd = captured[0]
    assert "sign-blob" in cmd
    assert "--yes" in cmd  # non-interactive
    assert "--oidc-issuer" in cmd
    assert "--output-signature" in cmd
    assert "--output-certificate" in cmd


def test_sign_blob_passes_identity_token(fake_pkg):
    captured: list = []

    def _capture(cmd, **kw):
        captured.append(cmd)
        return _cosign_ok()

    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_capture):
        sign_blob(fake_pkg, identity_token="JWT-VALUE")
    cmd = captured[0]
    assert "--identity-token" in cmd
    idx = cmd.index("--identity-token")
    assert cmd[idx + 1] == "JWT-VALUE"


def test_sign_blob_oidc_issuer_env_override(monkeypatch, fake_pkg):
    monkeypatch.setenv("SIGSTORE_OIDC_ISSUER", "https://custom-tas/")
    captured: list = []

    def _capture(cmd, **kw):
        captured.append(cmd)
        return _cosign_ok()

    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_capture):
        sign_blob(fake_pkg)
    cmd = captured[0]
    idx = cmd.index("--oidc-issuer")
    assert cmd[idx + 1] == "https://custom-tas/"


def test_sign_blob_cosign_failure_raises(fake_pkg):
    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_fail):
        with pytest.raises(CosignSignFailed) as excinfo:
            sign_blob(fake_pkg)
    assert "OIDC token rejected" in excinfo.value.cosign_stderr


def test_sign_blob_missing_tarball(tmp_path):
    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"):
        with pytest.raises(PublishError, match="not found"):
            sign_blob(tmp_path / "ghost.accpkg")


def test_sign_blob_no_cosign_binary(fake_pkg):
    with patch("acc.pkg.publish.shutil.which", return_value=None):
        with pytest.raises(PublishError, match="cosign binary not found"):
            sign_blob(fake_pkg)


def test_sign_blob_writes_to_custom_output_dir(tmp_path, fake_pkg):
    out_dir = tmp_path / "elsewhere"
    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_ok):
        artefacts = sign_blob(fake_pkg, output_dir=out_dir)
    assert artefacts.signature_path.parent == out_dir.resolve()


def test_sign_blob_rekor_index_none_when_absent(fake_pkg):
    def _ok_no_rekor(*a, **kw):
        return subprocess.CompletedProcess(
            args=a, returncode=0, stdout="", stderr="Signature written\n",
        )
    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_ok_no_rekor):
        artefacts = sign_blob(fake_pkg)
    assert artefacts.rekor_log_index is None


# ---------------------------------------------------------------------------
# publish — sign + upload
# ---------------------------------------------------------------------------


def _mock_http_put_success():
    class FakeResponse:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return patch(
        "acc.pkg.publish.urllib.request.urlopen",
        return_value=FakeResponse(),
    )


def test_publish_happy_path(fake_pkg):
    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_ok), \
         _mock_http_put_success():
        result = publish(fake_pkg, "https://hub.example.com")
    assert result.tarball_url.startswith("https://hub.example.com/upload/")
    assert result.tarball_url.endswith(".accpkg")
    assert result.signature_url.endswith(".sig")
    assert result.rekor_log_index == 12345678


def test_publish_sign_failure_propagates(fake_pkg):
    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_fail):
        with pytest.raises(CosignSignFailed):
            publish(fake_pkg, "https://hub.example.com")


def test_publish_upload_failure_raises(fake_pkg):
    import urllib.error

    def _fail(req, **kw):
        raise urllib.error.URLError("connection refused")

    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_ok), \
         patch("acc.pkg.publish.urllib.request.urlopen", side_effect=_fail):
        with pytest.raises(CatalogUploadFailed):
            publish(fake_pkg, "https://hub.example.com")


def test_publish_sends_bearer_token(fake_pkg):
    captured: list = []

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _capture(req, **kw):
        captured.append(req)
        return FakeResponse()

    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_ok), \
         patch("acc.pkg.publish.urllib.request.urlopen", side_effect=_capture):
        publish(fake_pkg, "https://hub.example.com", token="hub-token-xyz")
    # The first PUT should carry the auth header
    assert captured[0].headers.get("Authorization") == "Bearer hub-token-xyz"


# ---------------------------------------------------------------------------
# login_hint
# ---------------------------------------------------------------------------


def test_login_hint_ready_when_explicit_token(monkeypatch):
    monkeypatch.setenv("SIGSTORE_ID_TOKEN", "x")
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    hint = login_hint()
    assert hint["sigstore_id_token_set"] is True
    assert hint["ready_to_publish"] is True


def test_login_hint_ready_when_gha(monkeypatch):
    monkeypatch.delenv("SIGSTORE_ID_TOKEN", raising=False)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://gha")
    hint = login_hint()
    assert hint["github_actions_token_available"] is True
    assert hint["ready_to_publish"] is True


def test_login_hint_not_ready_when_unset(monkeypatch):
    monkeypatch.delenv("SIGSTORE_ID_TOKEN", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    hint = login_hint()
    assert hint["ready_to_publish"] is False


def test_login_hint_default_issuer(monkeypatch):
    monkeypatch.delenv("SIGSTORE_OIDC_ISSUER", raising=False)
    hint = login_hint()
    assert hint["issuer"] == "https://oauth2.sigstore.dev/auth"


def test_login_hint_issuer_env_override(monkeypatch):
    monkeypatch.setenv("SIGSTORE_OIDC_ISSUER", "https://custom-tas/")
    hint = login_hint()
    assert hint["issuer"] == "https://custom-tas/"


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_login_emits_json(capsys, monkeypatch):
    from acc.pkg.cli import EXIT_OK, main

    monkeypatch.setenv("SIGSTORE_ID_TOKEN", "x")
    rc = main(["--json", "login"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready_to_publish"] is True


def test_cli_publish_happy(fake_pkg, capsys):
    from acc.pkg.cli import EXIT_OK, main

    with patch("acc.pkg.publish.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.publish.subprocess.run", side_effect=_cosign_ok), \
         _mock_http_put_success():
        rc = main([
            "--json", "publish", str(fake_pkg),
            "--catalog-url", "https://hub.example.com",
        ])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["rekor_log_index"] == 12345678


def test_cli_publish_missing_pkg(tmp_path):
    from acc.pkg.cli import EXIT_USER_ERROR, main

    rc = main([
        "publish", str(tmp_path / "missing.accpkg"),
        "--catalog-url", "https://hub.example.com",
    ])
    assert rc == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Konflux pipeline manifest parses
# ---------------------------------------------------------------------------


def test_konflux_pipeline_template_parses():
    """The Stage 1.3 Konflux pipeline must validate as YAML."""
    import yaml

    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "gitops" / "tekton" / "pipelines" / "accpkg-build.yaml"
    if not path.is_file():
        pytest.skip("Konflux pipeline template not shipped")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["apiVersion"] == "tekton.dev/v1"
    assert doc["kind"] == "Pipeline"
    task_names = [t["name"] for t in doc["spec"]["tasks"]]
    assert "clone" in task_names
    assert "build" in task_names
    assert "publish" in task_names
