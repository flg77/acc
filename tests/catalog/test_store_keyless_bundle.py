"""CatalogStore — a keyless-signed package must be acceptable at all.

These tests need NO cosign: they cover the two structural reasons a keyless
catalog could never accept a correctly signed package, both of which bite
before any signature is checked.

The live case that found this: `@acc/mortgage-roles@1.0.0`, signed keyless by
acc-ecosystem-spearhead's release workflow, could not be published to an
in-cluster catalog even though `acc-pkg verify` accepted it on the same host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.catalog.store import CatalogStore, RejectedUpload
from acc.pkg.build import build
from acc.pkg.catalog import RequiredSigner

KEYLESS_ISSUER = "https://token.actions.githubusercontent.com"
KEYLESS_SUBJECT = (
    r"^https://github\.com/flg77/acc-ecosystem-spearhead/"
    r"\.github/workflows/release\.yml@refs/heads/main$"
)


def _build_pkg(src_root: Path, name: str, version: str) -> Path:
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


def _keyless_store(tmp_path) -> CatalogStore:
    return CatalogStore(
        tmp_path / "catalog-root",
        required_signer=RequiredSigner(
            issuer=KEYLESS_ISSUER, subject_pattern=KEYLESS_SUBJECT
        ),
        tier="self",
    )


def test_a_sigstore_bundle_is_an_acceptable_upload(tmp_path):
    """The bundle is the artefact keyless verification needs, so refusing the
    filename outright makes a keyless catalog impossible to populate."""
    store = _keyless_store(tmp_path)

    result = store.stage("demo-1.0.0.accpkg.bundle", b"not-a-real-bundle")

    assert result["staged"] == "demo-1.0.0.accpkg.bundle"
    assert result["promoted"] is False


@pytest.mark.parametrize("bad", ["demo.tar.gz", "demo.accpkg.txt", "../escape.accpkg"])
def test_unsupported_names_are_still_refused(tmp_path, bad):
    """Accepting .bundle must not have opened the door to anything else."""
    store = _keyless_store(tmp_path)
    with pytest.raises(RejectedUpload):
        store.stage(bad, b"x")


def test_keyless_waits_for_the_bundle_instead_of_destroying_the_upload(tmp_path):
    """A keyless publisher uploads .accpkg, .sig and .bundle as three requests.

    If the store tries to promote as soon as the .sig lands, cosign is handed a
    detached signature with no certificate, refuses, and the rejection path
    deletes every staged file -- so the .bundle arriving a moment later has
    nothing left to complete, and the package can never be published no matter
    how correctly it was signed.

    It must wait instead, and keep the files.
    """
    store = _keyless_store(tmp_path)
    pkg = _build_pkg(tmp_path / "build", "@test/demo", "1.0.0")
    data = pkg.read_bytes()

    assert store.stage(pkg.name, data)["promoted"] is False
    result = store.stage(pkg.name + ".sig", b"detached-signature-bytes")

    assert result["promoted"] is False, "promoted without the certificate"
    # The crux: nothing was thrown away while waiting.
    assert (store.staging_dir / pkg.name).is_file()
    assert (store.staging_dir / (pkg.name + ".sig")).is_file()
    assert store.index()["packages"] == []


def test_keypair_mode_still_promotes_on_the_sig_alone(tmp_path, monkeypatch):
    """The wait is keyless-only: a keypair signer has the public key, needs no
    bundle, and must not be made to hang waiting for one."""
    store = CatalogStore(
        tmp_path / "catalog-root",
        required_signer=RequiredSigner(
            issuer="lab-keypair", subject_pattern=".*",
            key_path=str(tmp_path / "cosign.pub"),
        ),
        tier="community",
    )
    pkg = _build_pkg(tmp_path / "build", "@test/demo", "1.0.0")

    calls: list[dict] = []

    def _fake_verify(acc, sig, signer, **kw):
        calls.append({"bundle": kw.get("bundle_path")})
        return None

    monkeypatch.setattr("acc.catalog.store._verify", _fake_verify)

    store.stage(pkg.name, pkg.read_bytes())
    result = store.stage(pkg.name + ".sig", b"signature-bytes")

    assert result["promoted"] is True
    assert calls and calls[0]["bundle"] is None


def test_the_index_advertises_the_bundle_so_an_install_can_fetch_it(tmp_path, monkeypatch):
    """acc/pkg/fetch.py downloads entry.bundle_url when it is set. Nothing ever
    set it, so a keyless install fetched a .sig it could never verify."""
    store = _keyless_store(tmp_path)
    pkg = _build_pkg(tmp_path / "build", "@test/demo", "1.0.0")

    monkeypatch.setattr(
        "acc.catalog.store._verify", lambda acc, sig, signer, **kw: None
    )

    store.stage(pkg.name, pkg.read_bytes())
    store.stage(pkg.name + ".sig", b"signature-bytes")
    result = store.stage(pkg.name + ".bundle", b"bundle-bytes")

    assert result["promoted"] is True
    entry = store.index()["packages"][0]
    assert entry["bundle_url"].endswith(".accpkg.bundle")
    assert entry["signature_url"].endswith(".accpkg.sig")
