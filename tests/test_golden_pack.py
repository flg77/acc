"""Gap #5 — export the golden store as a pack, and prove the a→b→c loop
closes: build → install → auto-detect (gap #4) → load."""

from __future__ import annotations

import gzip
import tarfile

import pytest


def _seed_store(monkeypatch, store):
    from acc.golden_prompts import GoldenPrompt, save_prompt

    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    save_prompt(GoldenPrompt(
        name="uc_alpha", prompt="do X", target_role="coding_agent"))
    save_prompt(GoldenPrompt(
        name="uc_beta", prompt="do Y", target_role="analyst"))


def test_build_golden_pack_manifest_and_golden_entries(tmp_path, monkeypatch):
    from acc.pkg.golden_pack import build_golden_pack

    _seed_store(monkeypatch, tmp_path / "store")
    out = tmp_path / "uc.accpkg"
    result = build_golden_pack("@you/uc", "0.1.0", output_path=out)

    assert out.is_file()
    assert result.manifest.name == "@you/uc"
    assert result.manifest.version == "0.1.0"
    # No capabilities declared — it's a golden-only pack.
    assert result.manifest.roles == [] and result.manifest.skills == []

    with gzip.open(out, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        names = tar.getnames()
    assert any(n.endswith("golden/uc_alpha.yaml") for n in names), names
    assert any(n.endswith("golden/uc_beta.yaml") for n in names), names


def test_empty_store_raises(tmp_path, monkeypatch):
    from acc.pkg.golden_pack import build_golden_pack

    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path / "empty"))
    with pytest.raises(ValueError, match="no golden prompts"):
        build_golden_pack("@you/uc", "0.1.0", output_path=tmp_path / "x.accpkg")


def test_build_install_autodetect_roundtrip(tmp_path, monkeypatch):
    """The full loop: author → export-as-pack → install → gap-#4 auto-detect."""
    from acc.golden_prompts import golden_roots, load_merged
    from acc.pkg.golden_pack import build_golden_pack
    from acc.pkg.install import install
    from acc.pkg.registry import Registry, installed_capability_dirs

    _seed_store(monkeypatch, tmp_path / "store")

    # export as pack
    out = tmp_path / "uc.accpkg"
    build_golden_pack("@you/uc", "0.1.0", output_path=out)

    # install into a fresh packages root (no signature step at this layer)
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "packages"))
    install(out, registry=Registry())

    # the installed pack's golden/ dir is discovered...
    dirs = installed_capability_dirs("golden")
    assert dirs, "installed pack golden/ dir not discovered"
    assert any((d / "uc_alpha.yaml").is_file() for d in dirs)
    assert any(d in golden_roots() for d in dirs)

    # ...and with the writable store now empty, the prompts load ONLY from
    # the installed pack — proving boot auto-detect.
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path / "empty-store"))
    names = {p.name for p in load_merged()}
    assert {"uc_alpha", "uc_beta"} <= names, names
