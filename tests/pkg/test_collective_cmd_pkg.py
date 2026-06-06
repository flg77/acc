"""Tests for ``acc-cli collective pkg-status`` + ``pkg-install`` (Stage 1.5.3)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from acc.cli.collective_cmd import register
from acc.pkg.build import MANIFEST_NAME, build
from acc.pkg.registry import Registry, RegistryEntry


# ---------------------------------------------------------------------------
# Setup — file-mode catalog + tmp install root
# ---------------------------------------------------------------------------


def _write_pkg_source(root: Path, *, name: str, version: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(yaml.safe_dump({
        "schema_version": 1,
        "name": name,
        "version": version,
        "roles": [{"name": "x", "path": "roles/x/role.yaml"}],
    }), encoding="utf-8")
    (root / "roles" / "x").mkdir(parents=True)
    (root / "roles" / "x" / "role.yaml").write_text(
        "role_definition:\n  purpose: t\n", encoding="utf-8"
    )
    return root


def _stage_in_catalog(catalog_root: Path, scope: str, pkg_path: Path) -> Path:
    scope_dir = catalog_root / scope
    scope_dir.mkdir(parents=True, exist_ok=True)
    dest = scope_dir / pkg_path.name
    dest.write_bytes(pkg_path.read_bytes())
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix(".accpkg.sha256").write_text(sha, encoding="utf-8")
    dest.with_suffix(".accpkg.sig").write_text("MOCK SIG", encoding="utf-8")
    return dest


@pytest.fixture
def catalog_env(monkeypatch, tmp_path):
    """Build @acc/coding-roles@0.1.0, stage as file catalog, point envs."""
    src = _write_pkg_source(tmp_path / "src", name="@acc/coding-roles", version="0.1.0")
    pkg = tmp_path / "coding-roles-0.1.0.accpkg"
    build(src, pkg)
    catalog_root = tmp_path / "catalog"
    _stage_in_catalog(catalog_root, "acc", pkg)

    sys_cat = tmp_path / "system-catalog.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [{
        "id": "test", "tier": "trusted", "mode": "file",
        "path": str(catalog_root),
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": ".*",
        },
    }]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "install"))
    return tmp_path


def _make_spec_file(dir_: Path, required_packages: list[str]) -> Path:
    p = dir_ / "collective.yaml"
    p.write_text(yaml.safe_dump({
        "collective_id": "dev-1",
        "required_packages": required_packages,
    }), encoding="utf-8")
    return p


def _run_cli(argv: list[str]) -> tuple[int, str, str, argparse.Namespace]:
    """Build an argparse tree, parse argv, dispatch the handler.

    Returns (rc, stdout, stderr, args).
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    register(sub)
    args = parser.parse_args(argv)
    return args.func(args)


def _ok_cosign(*a, **kw):
    return subprocess.CompletedProcess(args=a, returncode=0, stdout="OK\n", stderr="")


# ---------------------------------------------------------------------------
# pkg-status
# ---------------------------------------------------------------------------


def test_pkg_status_all_missing(catalog_env, capsys):
    spec_path = _make_spec_file(
        catalog_env, ["@acc/coding-roles@^0.1"]
    )
    rc = _run_cli([
        "collective", "pkg-status", str(spec_path), "--json",
    ])
    assert rc == 3   # 1+ missing → EXIT_DEPS shape
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing"] == ["@acc/coding-roles@^0.1"]
    assert payload["satisfied"] == []


def test_pkg_status_all_satisfied(catalog_env, capsys):
    # Pre-install into the registry
    reg = Registry()
    install_path = catalog_env / "install" / "acc" / "coding-roles-0.1.0"
    install_path.mkdir(parents=True)
    reg.add(RegistryEntry(
        name="@acc/coding-roles", version="0.1.0",
        content_sha256="a" * 64, install_path=str(install_path),
        installed_at="2026-06-03T00:00:00+00:00",
    ))

    spec_path = _make_spec_file(catalog_env, ["@acc/coding-roles@^0.1"])
    rc = _run_cli([
        "collective", "pkg-status", str(spec_path), "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing"] == []
    assert payload["satisfied"] == ["@acc/coding-roles@^0.1"]


def test_pkg_status_no_required_packages(catalog_env, capsys):
    """An empty required_packages list → exit 0, nothing missing."""
    spec_path = _make_spec_file(catalog_env, [])
    rc = _run_cli([
        "collective", "pkg-status", str(spec_path), "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing"] == []


def test_pkg_status_human_output(catalog_env, capsys):
    spec_path = _make_spec_file(catalog_env, ["@acc/coding-roles@^0.1"])
    rc = _run_cli([
        "collective", "pkg-status", str(spec_path),
    ])
    assert rc == 3
    captured = capsys.readouterr().out
    assert "MISSING" in captured
    assert "@acc/coding-roles@^0.1" in captured


def test_pkg_status_missing_spec_file(tmp_path):
    rc = _run_cli([
        "collective", "pkg-status", str(tmp_path / "nope.yaml"),
    ])
    assert rc == 1


# ---------------------------------------------------------------------------
# pkg-install
# ---------------------------------------------------------------------------


def test_pkg_install_resolves_and_installs(catalog_env, capsys):
    spec_path = _make_spec_file(catalog_env, ["@acc/coding-roles@^0.1"])

    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.verify.subprocess.run", side_effect=_ok_cosign):
        rc = _run_cli([
            "collective", "pkg-install", str(spec_path), "--json",
        ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["installed"]) == 1
    assert payload["installed"][0]["installed"] == "@acc/coding-roles@0.1.0"
    assert payload["failed"] == []
    # Registry knows
    reg = Registry()
    assert reg.find("@acc/coding-roles", "0.1.0") is not None


def test_pkg_install_skips_already_satisfied(catalog_env, capsys):
    """When everything's already installed, pkg-install is a no-op (rc=0)."""
    # Pre-install
    reg = Registry()
    install_path = catalog_env / "install" / "acc" / "coding-roles-0.1.0"
    install_path.mkdir(parents=True)
    reg.add(RegistryEntry(
        name="@acc/coding-roles", version="0.1.0",
        content_sha256="a" * 64, install_path=str(install_path),
        installed_at="2026-06-03T00:00:00+00:00",
    ))

    spec_path = _make_spec_file(catalog_env, ["@acc/coding-roles@^0.1"])
    rc = _run_cli([
        "collective", "pkg-install", str(spec_path), "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_satisfied"] is True


def test_pkg_install_no_required_packages_noop(catalog_env, capsys):
    spec_path = _make_spec_file(catalog_env, [])
    rc = _run_cli([
        "collective", "pkg-install", str(spec_path), "--json",
    ])
    assert rc == 0


def test_pkg_install_catalog_miss_reports_failure(catalog_env, capsys):
    spec_path = _make_spec_file(catalog_env, ["@acc/no-such-pkg@^1.0"])

    with patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"), \
         patch("acc.pkg.verify.subprocess.run", side_effect=_ok_cosign):
        rc = _run_cli([
            "collective", "pkg-install", str(spec_path), "--json",
        ])
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"] == []
    assert len(payload["failed"]) == 1
    assert "no catalog advertises" in payload["failed"][0]["error"]


def test_pkg_install_allow_unsigned_passes_through(catalog_env, tmp_path, monkeypatch):
    """--allow-unsigned reaches fetch_and_install (mocked) intact."""
    # Replace fetch_and_install with a spy; assert allow_unsigned=True propagates.
    called: dict = {}

    def fake_fetch(name, constraint, *, workspace=None, allow_unsigned=False, **kw):
        called["allow_unsigned"] = allow_unsigned
        called["name"] = name
        called["constraint"] = constraint
        # Mimic FetchResult shape minimally
        from acc.pkg.fetch import FetchError
        raise FetchError("test-stub")

    with patch("acc.pkg.fetch.fetch_and_install", side_effect=fake_fetch):
        spec_path = _make_spec_file(catalog_env, ["@acc/x@1.0.0"])
        _run_cli([
            "collective", "pkg-install", str(spec_path),
            "--allow-unsigned", "--json",
        ])
    assert called["allow_unsigned"] is True
    assert called["name"] == "@acc/x"
    assert called["constraint"] == "1.0.0"
