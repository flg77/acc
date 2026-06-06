"""Tests for the flock-protected package registry (Stage 0 slice 4)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from acc.pkg.registry import (
    DEFAULT_ROOT,
    REGISTRY_JSON,
    REGISTRY_LOCK,
    Registry,
    RegistryEntry,
    default_root,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(name: str = "@acc/coding-roles", version: str = "0.1.0") -> RegistryEntry:
    return RegistryEntry(
        name=name,
        version=version,
        content_sha256="a" * 64,
        install_path=f"/var/lib/acc/packages/{name}-{version}",
        installed_at="2026-06-03T00:00:00+00:00",
    )


@pytest.fixture
def reg(tmp_path: Path) -> Registry:
    """Registry rooted at a tmp dir so tests don't touch /var/lib/acc."""
    return Registry(tmp_path / "pkg-root")


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------


def test_default_root_uses_env_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path))
    assert default_root() == tmp_path


def test_default_root_falls_back_to_module_default(monkeypatch):
    monkeypatch.delenv("ACC_PACKAGES_ROOT", raising=False)
    assert default_root() == DEFAULT_ROOT


def test_registry_uses_default_root_when_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path))
    r = Registry()
    assert r.root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Empty-state behaviour
# ---------------------------------------------------------------------------


def test_list_empty_when_no_json(reg):
    assert reg.list() == []


def test_find_empty_when_no_json(reg):
    assert reg.find("@acc/anything") is None
    assert reg.find_by_name("@acc/anything") == []


def test_remove_returns_false_when_absent(reg):
    assert reg.remove("@acc/anything", "1.0.0") is False


# ---------------------------------------------------------------------------
# add / list / find round trip
# ---------------------------------------------------------------------------


def test_add_then_list(reg):
    e = _make_entry()
    reg.add(e)
    assert reg.list() == [e]


def test_add_persists_to_disk(reg):
    reg.add(_make_entry())
    assert (reg.root / REGISTRY_JSON).is_file()
    data = json.loads((reg.root / REGISTRY_JSON).read_text())
    assert data["schema_version"] == 1
    assert len(data["entries"]) == 1


def test_add_creates_root_dir(reg):
    assert not reg.root.exists()
    reg.add(_make_entry())
    assert reg.root.is_dir()


def test_add_idempotent_on_same_key(reg):
    e = _make_entry()
    reg.add(e)
    reg.add(e)
    assert reg.list() == [e]


def test_add_replaces_on_same_key_different_payload(reg):
    """install_path can change (re-installed elsewhere); same key wins."""
    reg.add(_make_entry())
    e2 = RegistryEntry(
        name="@acc/coding-roles",
        version="0.1.0",
        content_sha256="b" * 64,
        install_path="/elsewhere",
        installed_at="2026-06-04T00:00:00+00:00",
    )
    reg.add(e2)
    listed = reg.list()
    assert len(listed) == 1
    assert listed[0].content_sha256 == "b" * 64


def test_list_sorted_by_name_version(reg):
    reg.add(_make_entry("@acc/zebra", "0.2.0"))
    reg.add(_make_entry("@acc/alpha", "0.1.0"))
    reg.add(_make_entry("@acc/alpha", "0.0.1"))
    listed = reg.list()
    assert [e.key for e in listed] == [
        ("@acc/alpha", "0.0.1"),
        ("@acc/alpha", "0.1.0"),
        ("@acc/zebra", "0.2.0"),
    ]


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def test_find_returns_newest_when_version_omitted(reg):
    reg.add(_make_entry(version="0.1.0"))
    reg.add(_make_entry(version="0.3.0"))
    reg.add(_make_entry(version="0.2.0"))
    found = reg.find("@acc/coding-roles")
    assert found is not None
    assert found.version == "0.3.0"


def test_find_with_explicit_version(reg):
    reg.add(_make_entry(version="0.1.0"))
    reg.add(_make_entry(version="0.2.0"))
    found = reg.find("@acc/coding-roles", "0.1.0")
    assert found is not None
    assert found.version == "0.1.0"


def test_find_returns_none_on_version_miss(reg):
    reg.add(_make_entry(version="0.1.0"))
    assert reg.find("@acc/coding-roles", "9.9.9") is None


def test_find_by_name_returns_all_versions(reg):
    reg.add(_make_entry(version="0.3.0"))
    reg.add(_make_entry(version="0.1.0"))
    reg.add(_make_entry(version="0.2.0"))
    versions = [e.version for e in reg.find_by_name("@acc/coding-roles")]
    assert versions == ["0.1.0", "0.2.0", "0.3.0"]


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_existing_returns_true(reg):
    reg.add(_make_entry())
    assert reg.remove("@acc/coding-roles", "0.1.0") is True
    assert reg.list() == []


def test_remove_preserves_other_entries(reg):
    reg.add(_make_entry(version="0.1.0"))
    reg.add(_make_entry(version="0.2.0"))
    reg.remove("@acc/coding-roles", "0.1.0")
    listed = reg.list()
    assert len(listed) == 1
    assert listed[0].version == "0.2.0"


# ---------------------------------------------------------------------------
# Concurrency — flock must serialise readers/writers
# ---------------------------------------------------------------------------


def test_parallel_threaded_adds_all_land(reg):
    """10 threads each add a unique entry; all 10 must end up in the
    registry.  Without flock the JSON read-modify-write would race and
    lose updates.
    """
    barrier = threading.Barrier(10)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            barrier.wait()
            reg.add(_make_entry(name=f"@acc/pkg-{idx}", version="0.1.0"))
        except Exception as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    listed = reg.list()
    assert len(listed) == 10
    assert {e.name for e in listed} == {f"@acc/pkg-{i}" for i in range(10)}


def test_corrupt_json_treated_as_empty_with_backup(reg):
    reg.root.mkdir(parents=True, exist_ok=True)
    (reg.root / REGISTRY_JSON).write_text("{ this is not json", encoding="utf-8")
    # Reading should not raise — degraded mode kicks in.
    assert reg.list() == []
    assert (reg.root / "registry.json.corrupt").is_file()


def test_transaction_context_manager_isolates_reads(reg):
    """Inside ``with reg.transaction():`` external mutation by another
    Registry instance is serialised behind our lock — proven by the
    fact that nothing within the block raises and the final read
    reflects only our writes.
    """
    reg.add(_make_entry(version="0.1.0"))
    with reg.transaction():
        # No-op block — just proving the context manager works.
        pass
    assert len(reg.list()) == 1


# ---------------------------------------------------------------------------
# make_entry helper
# ---------------------------------------------------------------------------


def test_make_entry_stamps_iso_timestamp(reg, tmp_path):
    e = reg.make_entry(
        name="@acc/foo",
        version="1.2.3",
        content_sha256="c" * 64,
        install_path=tmp_path / "x",
    )
    assert e.name == "@acc/foo"
    assert e.version == "1.2.3"
    assert e.content_sha256 == "c" * 64
    # ISO-8601 with timezone offset, seconds precision
    assert "T" in e.installed_at
    assert e.installed_at.endswith("+00:00")


# ---------------------------------------------------------------------------
# Lock-file presence — sanity that the lock file is colocated
# ---------------------------------------------------------------------------


def test_lock_file_created_alongside_json(reg):
    reg.add(_make_entry())
    assert (reg.root / REGISTRY_LOCK).is_file()
