"""CatalogStore — the verify-before-list trust path (thread 12, marketplace P0).

These tests exercise the real cosign binary end to end (build a tiny .accpkg,
keypair-sign it, feed it through the store) so the signing floor is verified for
real, not mocked.  They skip when cosign isn't installed.  No fastapi needed —
the store is framework-agnostic.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from acc.catalog.store import CatalogStore, RejectedUpload
from acc.pkg.build import build
from acc.pkg.catalog import RequiredSigner
from acc.pkg.publish import sign_blob
from acc.pkg.verify import is_cosign_available

pytestmark = pytest.mark.skipif(
    not is_cosign_available(), reason="cosign binary not on PATH"
)


def _cosign() -> str:
    """The cosign binary acc uses (ACC_COSIGN_BIN), so keypair generation in the
    test matches the sign/verify path under test (acc pins cosign 2.4.x; a 3.x
    on PATH speaks a different, bundle-only dialect)."""
    return os.environ.get("ACC_COSIGN_BIN", "cosign")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def keypair(tmp_path, monkeypatch):
    """Generate a throwaway cosign keypair (empty password)."""
    monkeypatch.setenv("COSIGN_PASSWORD", "")
    keydir = tmp_path / "keys"
    keydir.mkdir()
    subprocess.run(
        [_cosign(), "generate-key-pair"],
        cwd=keydir, check=True, capture_output=True,
        env={**os.environ, "COSIGN_PASSWORD": ""},
    )
    return keydir / "cosign.key", keydir / "cosign.pub"


def _build_pkg(src_root: Path, name: str, version: str) -> Path:
    """Build a minimal valid .accpkg (manifest only — no components)."""
    src = src_root / f"{name.split('/')[-1]}-{version}-src"
    src.mkdir(parents=True)
    (src / "accpkg.yaml").write_text(
        f'schema_version: 1\nname: "{name}"\nversion: "{version}"\n'
        f'description: "test pack"\n',
        encoding="utf-8",
    )
    out = src_root / f"{name.split('/')[-1]}-{version}.accpkg"
    build(src, out)
    return out


def _store(tmp_path, pub: Path) -> CatalogStore:
    return CatalogStore(
        tmp_path / "catalog-root",
        required_signer=RequiredSigner(
            issuer="lab-keypair", subject_pattern=".*", key_path=str(pub)
        ),
        tier="community",
    )


# ---------------------------------------------------------------------------
# Happy path — signed package lists only after the .sig arrives
# ---------------------------------------------------------------------------


def test_signed_package_promotes_and_indexes(tmp_path, keypair):
    priv, pub = keypair
    store = _store(tmp_path, pub)
    pkg = _build_pkg(tmp_path / "build", "@test/demo", "1.0.0")
    art = sign_blob(pkg, key_path=str(priv))

    # 1) tarball alone → staged, NOT promoted, index empty
    r1 = store.stage(pkg.name, pkg.read_bytes())
    assert r1["promoted"] is False
    assert store.index()["packages"] == []

    # 2) signature arrives → verify passes → promoted + indexed
    r2 = store.stage(art.signature_path.name, art.signature_path.read_bytes())
    assert r2["promoted"] is True
    assert r2["name"] == "@test/demo"
    assert r2["version"] == "1.0.0"

    index = store.index()
    assert index["schema_version"] == 1
    assert index["tier"] == "community"
    assert len(index["packages"]) == 1
    entry = index["packages"][0]
    assert entry["name"] == "@test/demo"
    assert entry["version"] == "1.0.0"
    assert len(entry["tarball_sha256"]) == 64
    assert entry["tarball_url"] == "/packages/test/demo-1.0.0.accpkg"
    assert entry["signature_url"] == "/packages/test/demo-1.0.0.accpkg.sig"

    # served artefacts + sidecars exist; attestation recorded
    served = store.packages_dir / "test"
    assert (served / "demo-1.0.0.accpkg").is_file()
    assert (served / "demo-1.0.0.accpkg.sig").is_file()
    assert (served / "demo-1.0.0.accpkg.sha256").is_file()
    att = json.loads((served / "demo-1.0.0.accpkg.att.json").read_text())
    assert att[0]["kind"] == "eval_pass"
    assert len(att[0]["sha256"]) == 64

    # staging is drained
    assert not any(store.staging_dir.iterdir())


# ---------------------------------------------------------------------------
# Signing floor — a bad signature is refused and never lists
# ---------------------------------------------------------------------------


def test_wrong_key_signature_is_rejected_and_not_listed(tmp_path, keypair, monkeypatch):
    priv, pub = keypair
    store = _store(tmp_path, pub)
    pkg = _build_pkg(tmp_path / "build", "@test/evil", "0.1.0")

    # Sign with a DIFFERENT key than the store trusts.
    otherdir = tmp_path / "otherkeys"
    otherdir.mkdir()
    subprocess.run(
        [_cosign(), "generate-key-pair"],
        cwd=otherdir, check=True, capture_output=True,
        env={**os.environ, "COSIGN_PASSWORD": ""},
    )
    art = sign_blob(pkg, key_path=str(otherdir / "cosign.key"))

    store.stage(pkg.name, pkg.read_bytes())
    with pytest.raises(RejectedUpload) as ei:
        store.stage(art.signature_path.name, art.signature_path.read_bytes())
    assert "verification failed" in str(ei.value)

    # Nothing leaked into the served tree or the index, and staging is cleaned.
    assert store.index()["packages"] == []
    assert not (store.packages_dir / "test").exists()
    assert not any(store.staging_dir.iterdir())


# ---------------------------------------------------------------------------
# Path-safety — traversal / junk filenames are refused up front
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["../escape.accpkg", "a/b.accpkg", ".hidden.accpkg", "demo-1.0.0.tar.gz", ""],
)
def test_unsafe_or_unsupported_filenames_rejected(tmp_path, keypair, bad):
    _priv, pub = keypair
    store = _store(tmp_path, pub)
    with pytest.raises(RejectedUpload):
        store.stage(bad, b"x")


def test_rebuild_index_is_idempotent_from_disk(tmp_path, keypair):
    priv, pub = keypair
    store = _store(tmp_path, pub)
    pkg = _build_pkg(tmp_path / "build", "@test/demo", "1.0.0")
    art = sign_blob(pkg, key_path=str(priv))
    store.stage(pkg.name, pkg.read_bytes())
    store.stage(art.signature_path.name, art.signature_path.read_bytes())

    first = store.index()
    # A fresh store over the same root rebuilds the identical index.
    store2 = _store(tmp_path, pub)
    assert store2.rebuild_index()["packages"] == first["packages"]
