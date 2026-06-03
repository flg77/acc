"""Tests for the catalog-aware fetch + install helper (Stage 1.5.3)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from acc.pkg.build import MANIFEST_NAME, build
from acc.pkg.catalog import resolve_constraint
from acc.pkg.fetch import (
    CatalogResolutionFailed,
    TarballDownloadFailed,
    fetch_and_install,
)
from acc.pkg.registry import Registry
from acc.pkg.verify import VerifyError


# ---------------------------------------------------------------------------
# Helpers — build a real .accpkg + lay it out as a file-mode catalog
# ---------------------------------------------------------------------------


def _write_source(
    root: Path,
    *,
    name: str = "@acc/coding-agent",
    version: str = "0.1.0",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "name": name,
            "version": version,
            "roles": [{"name": "coding_agent", "path": "roles/coding_agent/role.yaml"}],
        }),
        encoding="utf-8",
    )
    (root / "roles" / "coding_agent").mkdir(parents=True)
    (root / "roles" / "coding_agent" / "role.yaml").write_text(
        "role_definition:\n  purpose: fetch-test\n", encoding="utf-8"
    )
    return root


def _stage_file_catalog(
    catalog_dir: Path, pkg_path: Path, *, with_sig: bool = True
) -> tuple[Path, Path | None]:
    """Lay out a built pkg as a file-mode catalog at ``catalog_dir/acc/``.

    Returns ``(in_catalog_pkg_path, in_catalog_sig_path)``.
    """
    scope_dir = catalog_dir / "acc"
    scope_dir.mkdir(parents=True, exist_ok=True)
    dest = scope_dir / pkg_path.name
    dest.write_bytes(pkg_path.read_bytes())
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix(".accpkg.sha256").write_text(sha, encoding="utf-8")
    sig_path = None
    if with_sig:
        sig_path = dest.with_suffix(".accpkg.sig")
        sig_path.write_text("MOCK SIG", encoding="utf-8")
    return dest, sig_path


def _file_catalog_yaml(catalog_path: Path) -> str:
    return yaml.safe_dump({"catalogs": [{
        "id": "test-file",
        "tier": "self",
        "mode": "file",
        "path": str(catalog_path),
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": ".*",
        },
    }]})


@pytest.fixture
def fetch_env(monkeypatch, tmp_path):
    """Build a coding_agent pkg, stage it as a file catalog, point env
    knobs at tmp paths, and return the relevant paths.
    """
    # Build a real pkg.  Filename matters: the file-mode catalog
    # parses ``<name>-<version>.accpkg`` from the filename and prepends
    # ``@<scope-dir>/`` to form the package name.  The pkg's manifest
    # declares ``@acc/coding-agent`` so the filename must be
    # ``coding-agent-<ver>.accpkg`` (NOT ``acc-coding-agent-...``).
    src = _write_source(tmp_path / "src")
    pkg = tmp_path / "dist" / "coding-agent-0.1.0.accpkg"
    pkg.parent.mkdir()
    build(src, pkg)
    # Stage as file catalog
    catalog_root = tmp_path / "catalog"
    cat_pkg, cat_sig = _stage_file_catalog(catalog_root, pkg)
    # System catalog YAML
    sys_cat = tmp_path / "system-catalog.yaml"
    sys_cat.write_text(_file_catalog_yaml(catalog_root), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    # Install target
    install_root = tmp_path / "install-root"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(install_root))
    return {
        "pkg": pkg,
        "cat_pkg": cat_pkg,
        "cat_sig": cat_sig,
        "install_root": install_root,
    }


def _ok_cosign(*a, **kw):
    return subprocess.CompletedProcess(args=a, returncode=0, stdout="OK\n", stderr="")


def _mock_cosign():
    return (
        patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"),
        patch("acc.pkg.verify.subprocess.run", side_effect=_ok_cosign),
    )


# ---------------------------------------------------------------------------
# resolve_constraint
# ---------------------------------------------------------------------------


def test_resolve_constraint_picks_highest_satisfying(monkeypatch, tmp_path):
    src1 = _write_source(tmp_path / "src1", version="0.1.0")
    src2 = _write_source(tmp_path / "src2", version="0.5.0")
    src3 = _write_source(tmp_path / "src3", version="1.0.0")
    # File-mode catalog parses `<name>-<version>.accpkg` from the
    # *filename*, so build with names that match.
    p1 = tmp_path / "coding-agent-0.1.0.accpkg"; build(src1, p1)
    p2 = tmp_path / "coding-agent-0.5.0.accpkg"; build(src2, p2)
    p3 = tmp_path / "coding-agent-1.0.0.accpkg"; build(src3, p3)

    catalog_root = tmp_path / "catalog"
    for p in (p1, p2, p3):
        _stage_file_catalog(catalog_root, p, with_sig=False)

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(_file_catalog_yaml(catalog_root), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))

    # ``>=0.2 <1.0`` → 0.5.0 (not 0.1.0, not 1.0.0)
    resolved = resolve_constraint("@acc/coding-agent", ">=0.2 <1.0")
    assert resolved is not None
    assert resolved.entry.version == "0.5.0"


def test_resolve_constraint_returns_none_on_miss(fetch_env):
    # Catalog has 0.1.0; ask for ^2.0
    assert resolve_constraint("@acc/coding-agent", "^2.0") is None


# ---------------------------------------------------------------------------
# fetch_and_install — file-mode catalog (no HTTP)
# ---------------------------------------------------------------------------


def test_fetch_and_install_file_catalog_happy(fetch_env):
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        result = fetch_and_install("@acc/coding-agent", "^0.1")
    assert result.install.entry.name == "@acc/coding-agent"
    assert result.install.entry.version == "0.1.0"
    assert result.install.install_path.is_dir()
    # Registry knows
    reg = Registry()
    assert reg.find("@acc/coding-agent", "0.1.0") is not None


def test_fetch_and_install_idempotent(fetch_env):
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        r1 = fetch_and_install("@acc/coding-agent", "^0.1")
        r2 = fetch_and_install("@acc/coding-agent", "^0.1")
    assert not r1.install.was_already_installed
    assert r2.install.was_already_installed


def test_fetch_no_catalog_match_raises(fetch_env):
    with pytest.raises(CatalogResolutionFailed):
        fetch_and_install("@acc/no-such-pkg", "^1.0")


# ---------------------------------------------------------------------------
# fetch_and_install — signing floor
# ---------------------------------------------------------------------------


def test_fetch_refuses_unsigned_by_default(monkeypatch, tmp_path):
    """Catalog with no signature on the entry → fetch refuses."""
    src = _write_source(tmp_path / "src")
    pkg = tmp_path / "coding-agent-0.1.0.accpkg"; build(src, pkg)
    catalog_root = tmp_path / "catalog"
    _stage_file_catalog(catalog_root, pkg, with_sig=False)  # NO sig

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(_file_catalog_yaml(catalog_root), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "install"))

    with pytest.raises(VerifyError, match="signing floor"):
        fetch_and_install("@acc/coding-agent", "^0.1")


def test_fetch_allow_unsigned_bypass(monkeypatch, tmp_path, caplog):
    """Operator-explicit unsigned install completes + audit-logs."""
    src = _write_source(tmp_path / "src")
    pkg = tmp_path / "coding-agent-0.1.0.accpkg"; build(src, pkg)
    catalog_root = tmp_path / "catalog"
    _stage_file_catalog(catalog_root, pkg, with_sig=False)

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(_file_catalog_yaml(catalog_root), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "install"))

    import logging
    caplog.set_level(logging.WARNING, logger="acc.pkg.fetch")
    result = fetch_and_install(
        "@acc/coding-agent", "^0.1", allow_unsigned=True,
    )
    assert result.install.entry.name == "@acc/coding-agent"
    assert any(
        "AUDIT" in r.message and "allow-unsigned" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# HTTPS catalog — mocked
# ---------------------------------------------------------------------------


def test_fetch_https_catalog(monkeypatch, tmp_path):
    """Mock the HTTPS layer: ``index.json`` + tarball + signature
    served from a dict-keyed responder.
    """
    src = _write_source(tmp_path / "src")
    pkg = tmp_path / "coding-agent-0.1.0.accpkg"; build(src, pkg)
    pkg_bytes = pkg.read_bytes()

    index = json.dumps({
        "packages": [{
            "name": "@acc/coding-agent",
            "version": "0.1.0",
            "tarball_sha256": hashlib.sha256(pkg_bytes).hexdigest(),
            "tarball_url": "/packages/acc/coding-agent-0.1.0.accpkg",
            "signature_url": "/packages/acc/coding-agent-0.1.0.accpkg.sig",
        }]
    }).encode("utf-8")

    responses = {
        "https://hub.example.com/index.json": index,
        "https://hub.example.com/packages/acc/coding-agent-0.1.0.accpkg": pkg_bytes,
        "https://hub.example.com/packages/acc/coding-agent-0.1.0.accpkg.sig": b"MOCK SIG",
    }

    class FakeResponse:
        def __init__(self, data): self._data = data
        def read(self): return self._data
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(url, **kw):
        # urlopen receives either a string URL or a Request; extract.
        if hasattr(url, "full_url"):
            url = url.full_url
        return FakeResponse(responses[url])

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [{
        "id": "https-test",
        "tier": "trusted",
        "mode": "https",
        "url": "https://hub.example.com",
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": ".*",
        },
    }]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "install"))

    p_which, p_run = _mock_cosign()
    with patch("acc.pkg.catalog.urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("acc.pkg.fetch.urllib.request.urlopen", side_effect=fake_urlopen), \
         p_which, p_run:
        result = fetch_and_install("@acc/coding-agent", "^0.1")

    assert result.install.entry.name == "@acc/coding-agent"


def test_fetch_https_download_failure_raises(monkeypatch, tmp_path):
    """HTTP error on tarball fetch surfaces as TarballDownloadFailed."""
    src = _write_source(tmp_path / "src")
    pkg = tmp_path / "coding-agent-0.1.0.accpkg"; build(src, pkg)

    index = json.dumps({"packages": [{
        "name": "@acc/coding-agent", "version": "0.1.0",
        "tarball_sha256": "a" * 64,
        "tarball_url": "/packages/acc/coding-agent-0.1.0.accpkg",
        "signature_url": "/packages/acc/coding-agent-0.1.0.accpkg.sig",
    }]}).encode("utf-8")

    class FakeResponse:
        def __init__(self, d): self._d = d
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(url, **kw):
        if hasattr(url, "full_url"):
            url = url.full_url
        if url.endswith("/index.json"):
            return FakeResponse(index)
        import urllib.error
        raise urllib.error.URLError("connection refused")

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [{
        "id": "https-test", "tier": "trusted", "mode": "https",
        "url": "https://hub.example.com",
        "required_signer": {"issuer": "x", "subject_pattern": ".*"},
    }]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "install"))

    with patch("acc.pkg.catalog.urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("acc.pkg.fetch.urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(TarballDownloadFailed):
            fetch_and_install("@acc/coding-agent", "^0.1")
