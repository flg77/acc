"""Tests for the ``acc-pkg`` CLI (Stage 0 slice 8)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from acc.pkg.build import MANIFEST_NAME, build
from acc.pkg.cli import (
    EXIT_DEPS,
    EXIT_HASH_MISMATCH,
    EXIT_OK,
    EXIT_SCHEMA,
    EXIT_SIGNATURE,
    EXIT_USER_ERROR,
    main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_source(
    root: Path,
    *,
    name: str = "@acc/coding-agent",
    version: str = "0.1.0",
    depends_on: list[dict] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "name": name,
            "version": version,
            "roles": [{"name": "coding_agent", "path": "roles/coding_agent/role.yaml"}],
            "depends_on": depends_on or [],
        }),
        encoding="utf-8",
    )
    (root / "roles" / "coding_agent").mkdir(parents=True)
    (root / "roles" / "coding_agent" / "role.yaml").write_text(
        "role_definition:\n  purpose: test\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def pkg_paths(tmp_path: Path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "dist" / "pkg.accpkg"
    build(src, out)
    sig = out.parent / (out.name + ".sig")
    sig.write_text("FAKE SIG", encoding="utf-8")
    pub = tmp_path / "acc-pilot.pub"
    pub.write_text("-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----\n",
                   encoding="utf-8")
    return src, out, sig, pub


@pytest.fixture
def autoroot(monkeypatch, tmp_path):
    """Route the registry's packages root to tmp."""
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "pkg-root"))


def _ok_cosign(*a, **kw):
    return subprocess.CompletedProcess(args=a, returncode=0, stdout="OK\n", stderr="")


def _mock_cosign():
    return (
        patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"),
        patch("acc.pkg.verify.subprocess.run", side_effect=_ok_cosign),
    )


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def test_cli_build_happy(pkg_paths, tmp_path, capsys):
    src, _, _, _ = pkg_paths
    out_file = tmp_path / "new.accpkg"
    rc = main(["--json", "build", str(src), "-o", str(out_file)])
    assert rc == EXIT_OK
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["name"] == "@acc/coding-agent"
    assert out_file.is_file()


def test_cli_build_missing_source(tmp_path):
    rc = main(["build", str(tmp_path / "missing"), "-o", str(tmp_path / "x.accpkg")])
    assert rc == EXIT_USER_ERROR


def test_cli_build_invalid_manifest(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / MANIFEST_NAME).write_text(
        yaml.safe_dump({"schema_version": 1, "name": "no-scope", "version": "0.1.0"}),
        encoding="utf-8",
    )
    rc = main(["build", str(src), "-o", str(tmp_path / "out.accpkg")])
    assert rc == EXIT_SCHEMA


# ---------------------------------------------------------------------------
# install — happy + idempotent
# ---------------------------------------------------------------------------


def test_cli_install_happy(pkg_paths, autoroot, capsys):
    _, pkg, sig, pub = pkg_paths
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        rc = main([
            "--json", "install", str(pkg),
            "--signature", str(sig), "--key", str(pub),
        ])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["was_already_installed"] is False


def test_cli_install_idempotent_second_run(pkg_paths, autoroot, capsys):
    _, pkg, sig, pub = pkg_paths
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        rc1 = main(["--json", "install", str(pkg), "--signature", str(sig), "--key", str(pub)])
    capsys.readouterr()  # discard first
    with p_which, p_run:
        rc2 = main(["--json", "install", str(pkg), "--signature", str(sig), "--key", str(pub)])
    assert rc1 == EXIT_OK
    assert rc2 == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["was_already_installed"] is True


# ---------------------------------------------------------------------------
# install — signing-floor refusal paths
# ---------------------------------------------------------------------------


def test_cli_install_refuses_without_signature(pkg_paths, autoroot, tmp_path):
    _, pkg, _, pub = pkg_paths
    # Point --signature at a missing file
    rc = main([
        "install", str(pkg),
        "--signature", str(tmp_path / "missing.sig"),
        "--key", str(pub),
    ])
    assert rc == EXIT_SIGNATURE


def test_cli_install_refuses_without_signer_flags(pkg_paths, autoroot):
    _, pkg, sig, _ = pkg_paths
    # No --key and no --issuer/--subject
    rc = main(["install", str(pkg), "--signature", str(sig)])
    assert rc == EXIT_USER_ERROR


def test_cli_install_allow_unsigned_bypass(pkg_paths, autoroot, caplog):
    _, pkg, _, _ = pkg_paths
    import logging
    caplog.set_level(logging.WARNING, logger="acc.pkg.cli")
    rc = main(["install", str(pkg), "--allow-unsigned"])
    assert rc == EXIT_OK
    # Audit log entry emitted
    assert any("AUDIT" in r.message and "--allow-unsigned" in r.message
               for r in caplog.records)


def test_cli_install_cosign_rejection_returns_signature_exit_code(
    pkg_paths, autoroot
):
    _, pkg, sig, pub = pkg_paths
    fail_run = lambda *a, **kw: subprocess.CompletedProcess(
        args=a, returncode=1, stdout="", stderr="bad cert"
    )
    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.verify.subprocess.run", side_effect=fail_run):
        rc = main([
            "install", str(pkg),
            "--signature", str(sig), "--key", str(pub),
        ])
    assert rc == EXIT_SIGNATURE


def test_cli_install_missing_dep_returns_deps_exit_code(
    tmp_path, autoroot
):
    src = _write_source(
        tmp_path / "src",
        name="@acc/needs-dep",
        depends_on=[{"name": "@acc/missing", "version": "^1.0"}],
    )
    out = tmp_path / "needs.accpkg"
    build(src, out)

    rc = main(["install", str(out), "--allow-unsigned"])
    assert rc == EXIT_DEPS


# ---------------------------------------------------------------------------
# verify — standalone
# ---------------------------------------------------------------------------


def test_cli_verify_happy(pkg_paths, capsys):
    _, pkg, sig, pub = pkg_paths
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        rc = main([
            "--json", "verify", str(pkg),
            "--signature", str(sig), "--key", str(pub),
        ])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "keypair"


def test_cli_verify_requires_signer_flags(pkg_paths):
    _, pkg, sig, _ = pkg_paths
    rc = main(["verify", str(pkg), "--signature", str(sig)])
    assert rc == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def test_cli_inspect_emits_manifest(pkg_paths, capsys):
    _, pkg, _, _ = pkg_paths
    rc = main(["--json", "inspect", str(pkg)])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "@acc/coding-agent"
    assert payload["version"] == "0.1.0"
    assert payload["content_sha256"]


def test_cli_inspect_missing_file(tmp_path):
    rc = main(["inspect", str(tmp_path / "missing.accpkg")])
    assert rc == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_cli_list_empty(autoroot, capsys):
    rc = main(["--json", "list"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload == []


def test_cli_list_after_install(pkg_paths, autoroot, capsys):
    _, pkg, _, _ = pkg_paths
    # Install first
    main(["install", str(pkg), "--allow-unsigned"])
    capsys.readouterr()  # discard
    rc = main(["--json", "list"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["name"] == "@acc/coding-agent"


def test_cli_list_available_returns_catalog_entries(
    pkg_paths, tmp_path, monkeypatch, capsys
):
    """``--available`` lists entries from a file-mode catalog rather than
    the registry.
    """
    _, pkg, _, _ = pkg_paths

    # Place the built pkg + sidecar sha into a fake catalog dir layout
    dist = tmp_path / "dist"
    scope_dir = dist / "acc"
    scope_dir.mkdir(parents=True)
    pkg_in_catalog = scope_dir / "coding-agent-0.1.0.accpkg"
    pkg_in_catalog.write_bytes(pkg.read_bytes())
    import hashlib
    sha = hashlib.sha256(pkg_in_catalog.read_bytes()).hexdigest()
    pkg_in_catalog.with_suffix(".accpkg.sha256").write_text(sha, encoding="utf-8")

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [{
        "id": "test", "tier": "trusted", "mode": "file",
        "path": str(dist),
        "required_signer": {"issuer": "x", "subject_pattern": ".*"},
    }]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))

    rc = main(["--json", "list", "--available"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert any(row["name"] == "@acc/coding-agent" for row in payload)


# ---------------------------------------------------------------------------
# --quiet behaviour
# ---------------------------------------------------------------------------


def test_quiet_mode_suppresses_stdout(pkg_paths, autoroot, capsys):
    _, pkg, _, _ = pkg_paths
    rc = main(["--quiet", "install", str(pkg), "--allow-unsigned"])
    assert rc == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ""


def test_quiet_mode_still_emits_json_when_both_set(pkg_paths, autoroot, capsys):
    _, pkg, _, _ = pkg_paths
    # When --quiet AND --json are both passed, JSON wins (machine
    # consumers always need structured output).
    rc = main(["--quiet", "--json", "install", str(pkg), "--allow-unsigned"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
