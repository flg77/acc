"""Tests for the deterministic ``.accpkg`` builder (Stage 0 slice 3).

Coverage:

* Build happy path: source → output file + stamped manifest.
* **Determinism**: two builds of identical input produce identical bytes.
* Content-tree hash changes when a file changes.
* Source manifest validation: missing/empty/pre-stamped refused.
* Source layout: symlinks refused.
* core_baseline leakage refused at build time (delegated to manifest).
* Tarball is well-formed (untar via :mod:`tarfile`, manifest first).
"""

from __future__ import annotations

import gzip
import hashlib
import os
import tarfile
import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acc.pkg.build import MANIFEST_NAME, build, load_source_manifest
from acc.pkg.manifest import AccPkgManifest


# ---------------------------------------------------------------------------
# Source-tree fixture helpers
# ---------------------------------------------------------------------------


def _write_source(
    root: Path,
    *,
    name: str = "@acc/coding-agent",
    version: str = "0.1.0",
    extra_files: dict[str, str] | None = None,
    skills: list[dict] | None = None,
    mcps: list[dict] | None = None,
) -> Path:
    """Create a minimal valid package source tree at ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "roles": [
            {"name": "coding_agent", "path": "roles/coding_agent/role.yaml"},
        ],
        "skills": skills or [],
        "mcps": mcps or [],
    }
    (root / MANIFEST_NAME).write_text(yaml.safe_dump(manifest), encoding="utf-8")

    # Single role.yaml stand-in
    role_dir = root / "roles" / "coding_agent"
    role_dir.mkdir(parents=True)
    (role_dir / "role.yaml").write_text(
        "role_definition:\n  purpose: test\n", encoding="utf-8"
    )

    for relpath, content in (extra_files or {}).items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return root


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_happy_path(tmp_path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "dist" / "pkg.accpkg"
    result = build(src, out)

    assert out.is_file()
    assert out.stat().st_size > 0
    assert result.content_sha256
    assert len(result.content_sha256) == 64
    assert result.manifest.content_sha256 == result.content_sha256
    assert result.manifest.name == "@acc/coding-agent"


def test_build_creates_output_dir(tmp_path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "deeply" / "nested" / "missing" / "pkg.accpkg"
    build(src, out)
    assert out.is_file()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_build_is_byte_deterministic(tmp_path):
    """Two builds of the same source produce identical bytes."""
    src = _write_source(tmp_path / "src")
    out1 = tmp_path / "first.accpkg"
    out2 = tmp_path / "second.accpkg"

    build(src, out1)
    # Touch a file's mtime to prove file mtime doesn't leak into output
    role = src / "roles" / "coding_agent" / "role.yaml"
    far_future = time.time() + 60 * 60 * 24 * 365  # +1y
    os.utime(role, (far_future, far_future))
    # Sleep a hair to make sure any cached time is past
    time.sleep(0.01)
    build(src, out2)

    h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
    assert h1 == h2, "build output is not byte-deterministic"


def test_content_hash_changes_when_file_changes(tmp_path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "pkg.accpkg"

    r1 = build(src, out)

    # Mutate a role file
    (src / "roles" / "coding_agent" / "role.yaml").write_text(
        "role_definition:\n  purpose: CHANGED\n", encoding="utf-8"
    )
    r2 = build(src, out)

    assert r1.content_sha256 != r2.content_sha256


def test_content_hash_stable_across_two_identical_builds(tmp_path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "pkg.accpkg"
    r1 = build(src, out)
    r2 = build(src, out)
    assert r1.content_sha256 == r2.content_sha256


# ---------------------------------------------------------------------------
# Source manifest validation
# ---------------------------------------------------------------------------


def test_missing_manifest_refused(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "roles").mkdir()  # but no accpkg.yaml
    with pytest.raises(FileNotFoundError):
        build(src, tmp_path / "pkg.accpkg")


def test_invalid_manifest_refused(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / MANIFEST_NAME).write_text(
        yaml.safe_dump({"schema_version": 1, "name": "no-scope", "version": "0.1.0"}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        build(src, tmp_path / "pkg.accpkg")


def test_pre_stamped_content_sha256_refused(tmp_path):
    """Source manifests must not pre-declare ``content_sha256`` —
    build owns that field.
    """
    src = tmp_path / "src"
    src.mkdir()
    bogus_hash = "a" * 64
    (src / MANIFEST_NAME).write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": "@acc/x",
                "version": "0.1.0",
                "content_sha256": bogus_hash,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="content_sha256"):
        build(src, tmp_path / "pkg.accpkg")


def test_core_baseline_skill_in_source_refused(tmp_path):
    """The manifest validator rejects core_baseline leakage; build
    propagates that failure.
    """
    src = _write_source(
        tmp_path / "src",
        skills=[{"name": "shell_exec", "tier": "bundle_in_role", "path": "skills/shell_exec/"}],
    )
    with pytest.raises(ValidationError, match="core_baseline"):
        build(src, tmp_path / "pkg.accpkg")


# ---------------------------------------------------------------------------
# Source layout
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symlink test requires unix-style symlinks")
def test_symlink_in_source_refused(tmp_path):
    src = _write_source(tmp_path / "src")
    target = src / "roles" / "coding_agent" / "role.yaml"
    link = src / "roles" / "coding_agent" / "alias.yaml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        build(src, tmp_path / "pkg.accpkg")


# ---------------------------------------------------------------------------
# Tarball shape
# ---------------------------------------------------------------------------


def test_tarball_manifest_is_first_entry(tmp_path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "pkg.accpkg"
    build(src, out)

    with gzip.open(out, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            first = next(iter(tar))
            assert first.name == MANIFEST_NAME


def test_tarball_entries_have_zero_mtime_uid_gid(tmp_path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "pkg.accpkg"
    build(src, out)

    with gzip.open(out, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r:") as tar:
            for info in tar.getmembers():
                assert info.mtime == 0, info.name
                assert info.uid == 0, info.name
                assert info.gid == 0, info.name
                assert info.uname == "", info.name
                assert info.gname == "", info.name


def test_tarball_manifest_carries_stamped_content_sha256(tmp_path):
    src = _write_source(tmp_path / "src")
    out = tmp_path / "pkg.accpkg"
    result = build(src, out)

    with gzip.open(out, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r:") as tar:
            f = tar.extractfile(MANIFEST_NAME)
            assert f is not None
            data = yaml.safe_load(f.read())
    assert data["content_sha256"] == result.content_sha256


# ---------------------------------------------------------------------------
# load_source_manifest helper
# ---------------------------------------------------------------------------


def test_load_source_manifest_returns_model(tmp_path):
    src = _write_source(tmp_path / "src")
    m = load_source_manifest(src)
    assert isinstance(m, AccPkgManifest)
    assert m.name == "@acc/coding-agent"
    assert m.content_sha256 == ""  # source mode
