"""Tests for the recreate-on-select apply-request protocol (PR-X).

Covers acc.workspace_apply: the request file location resolution, the
atomic write/read roundtrip, and the path-safety guard the host
watcher reuses to refuse out-of-bounds host paths.
"""

from __future__ import annotations

from pathlib import Path

from acc.workspace_apply import (
    apply_dir,
    apply_request_path,
    is_within_base,
    read_apply_request,
    write_apply_request,
)


# ---------------------------------------------------------------------------
# apply_dir resolution
# ---------------------------------------------------------------------------


def test_apply_dir_override_wins(tmp_path):
    assert apply_dir(tmp_path) == tmp_path


def test_apply_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ACC_APPLY_DIR", str(tmp_path))
    assert apply_dir() == tmp_path


def test_apply_dir_container_default(monkeypatch):
    monkeypatch.delenv("ACC_APPLY_DIR", raising=False)
    assert apply_dir() == Path("/app/.acc-apply")


# ---------------------------------------------------------------------------
# write / read roundtrip
# ---------------------------------------------------------------------------


def test_write_then_read_roundtrip(tmp_path):
    req = write_apply_request("/home/flg/projects/foo", override=tmp_path)
    assert req == apply_request_path(tmp_path)
    data = read_apply_request(tmp_path)
    assert data is not None
    assert data["host_path"] == "/home/flg/projects/foo"
    assert data["requested_by"] == "tui"
    assert isinstance(data["ts"], float)


def test_write_creates_dir(tmp_path):
    nested = tmp_path / "a" / "b"
    write_apply_request("/home/x", override=nested)
    assert (nested / "workspace.request").is_file()


def test_write_is_atomic_no_tmp_left(tmp_path):
    write_apply_request("/home/x", override=tmp_path)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], leftovers


def test_read_missing_returns_none(tmp_path):
    assert read_apply_request(tmp_path) is None


def test_read_malformed_returns_none(tmp_path):
    (tmp_path / "workspace.request").write_text("{not json", encoding="utf-8")
    assert read_apply_request(tmp_path) is None


def test_second_write_overwrites(tmp_path):
    write_apply_request("/home/a", override=tmp_path)
    write_apply_request("/home/b", override=tmp_path)
    assert read_apply_request(tmp_path)["host_path"] == "/home/b"


# ---------------------------------------------------------------------------
# is_within_base — the host-path safety guard
# ---------------------------------------------------------------------------


class TestIsWithinBase:
    def test_subpath_allowed(self, tmp_path):
        base = tmp_path / "home" / "flg"
        base.mkdir(parents=True)
        assert is_within_base(str(base / "projects" / "foo"), str(base))

    def test_base_itself_allowed(self, tmp_path):
        base = tmp_path / "home"
        base.mkdir()
        assert is_within_base(str(base), str(base))

    def test_outside_base_rejected(self, tmp_path):
        base = tmp_path / "home" / "flg"
        base.mkdir(parents=True)
        assert is_within_base("/etc/passwd", str(base)) is False

    def test_parent_traversal_rejected(self, tmp_path):
        base = tmp_path / "home" / "flg"
        base.mkdir(parents=True)
        # ../../etc climbs out of base.
        assert is_within_base(str(base / ".." / ".." / "etc"), str(base)) is False

    def test_relative_candidate_rejected(self, tmp_path):
        assert is_within_base("projects/foo", str(tmp_path)) is False

    def test_relative_base_rejected(self):
        assert is_within_base("/home/flg/x", "home/flg") is False

    def test_symlink_escape_rejected(self, tmp_path):
        base = tmp_path / "home"
        base.mkdir()
        outside = tmp_path / "secret"
        outside.mkdir()
        link = base / "escape"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            import pytest
            pytest.skip("symlinks not supported on this host")
        # base/escape resolves to tmp_path/secret which is outside base.
        assert is_within_base(str(link / "x"), str(base)) is False
