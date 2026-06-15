"""Flock-protected package registry — Stage 0 slice 4.

The registry is a JSON index at ``<root>/registry.json`` listing
every installed ``.accpkg`` (one entry per ``(name, version)`` pair).
It is the single source of truth for "is package X@v installed and
where" and the input to:

* the catalog resolver's alternates display (slice 6),
* the installer's cycle + idempotency checks (slice 5),
* GC (cache trim — deferred to a Stage 1 follow-up),
* and any debugging / inspection tool that wants to know what's on
  disk.

Concurrency model
-----------------

All read+modify+write operations grab an exclusive POSIX ``flock``
on a sibling ``<root>/registry.lock`` file before touching
``registry.json``.  Two concurrent ``acc-pkg install`` runs from
different processes serialise; the second sees the first's update.
On platforms without ``fcntl`` (Windows dev) the lock degrades to a
no-op — same posture as :mod:`acc._atomic_write`.

The root path defaults to ``/var/lib/acc/packages`` but is
overridden via ``ACC_PACKAGES_ROOT`` for tests and for the bootc
edge bundler (which lays packages under a different prefix).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, ConfigDict, Field

from acc._atomic_write import _file_lock, atomic_write_text

logger = logging.getLogger("acc.pkg.registry")

REGISTRY_JSON = "registry.json"
REGISTRY_LOCK = "registry.lock"

DEFAULT_ROOT = Path("/var/lib/acc/packages")


# In-process thread serialisation for the registry.  POSIX flock
# guarantees cross-process safety; this dict adds in-process safety
# (and is the only safety on Windows, where fcntl.flock is unavailable
# and ``os.replace`` would otherwise lose to "destination is open in
# another handle" races).  One lock per resolved root path so multiple
# Registry instances at the same root share serialization.
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(root: Path) -> threading.Lock:
    key = str(root)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class RegistryEntry(BaseModel):
    """One installed package version."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    content_sha256: str = Field(..., min_length=64, max_length=64)
    install_path: str = Field(
        ..., description="Absolute path of the unpacked package tree"
    )
    installed_at: str = Field(
        ..., description="ISO-8601 UTC timestamp of when this entry was added"
    )

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.version)


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------


def default_root() -> Path:
    """Resolve the packages root.

    Precedence: ``ACC_PACKAGES_ROOT`` env > module default
    (``/var/lib/acc/packages``).
    """
    raw = os.environ.get("ACC_PACKAGES_ROOT", "").strip()
    return Path(raw) if raw else DEFAULT_ROOT


def root_is_host_writable(root: Path | None = None) -> bool:
    """Return ``True`` iff this process can create/write the packages root.

    Walks up to the nearest existing ancestor and tests write access
    there — **non-destructive**, no directory is created.  Returns
    ``False`` for the containerized-stack case where the root is an
    in-container volume (e.g. ``/var/lib/acc/packages``) that the host
    can neither create nor write: callers should then defer package
    resolution to the in-container registry instead of crashing with a
    ``PermissionError`` (see acc-spearhead#85).
    """
    probe = (root or default_root()).resolve()
    while True:
        try:
            if probe.exists():
                return os.access(probe, os.W_OK)
        except OSError:
            return False
        if probe.parent == probe:  # reached the filesystem root
            return False
        probe = probe.parent


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class Registry:
    """Read+modify+write of ``<root>/registry.json`` under flock.

    All public methods are concurrency-safe — they grab the
    registry-wide lock around the read+modify+write sequence.  Callers
    that need to bundle multiple operations into one critical section
    can use :meth:`transaction` directly.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_root()).resolve()
        self.json_path = self.root / REGISTRY_JSON
        self.lock_path = self.root / REGISTRY_LOCK

    # -- lock plumbing --------------------------------------------------

    @contextmanager
    def transaction(self, *, create: bool = True) -> Iterator[None]:
        """Hold the registry's exclusive lock for the duration of the
        ``with`` block.  Useful when an external caller does
        ``read → external work → write`` and needs the read state to
        stay consistent.

        With ``create=False`` (read paths), a registry root that was
        never created is not materialised: the block runs lock-free and
        the subsequent read sees "nothing installed".  This lets a host
        that can't write the packages root (the in-container
        ``acc-packages`` volume) still *read* an empty registry instead
        of crashing with ``PermissionError`` (acc-spearhead#85).
        """
        if not create:
            try:
                lock_exists = self.lock_path.exists()
            except OSError:
                lock_exists = False
            if not lock_exists:
                yield
                return
        self.root.mkdir(parents=True, exist_ok=True)
        # ``touch`` is idempotent + cheap; needed because flock needs an
        # existing file to lock on.
        self.lock_path.touch(exist_ok=True)
        # Two-layer lock: in-process thread lock (all platforms) wraps
        # POSIX flock (cross-process, no-op on Windows).
        with _thread_lock_for(self.root):
            with self.lock_path.open("rb+") as fh:
                with _file_lock(fh):
                    yield

    # -- low-level read/write (must be called under lock) --------------

    def _read_unlocked(self) -> list[RegistryEntry]:
        if not self.json_path.is_file():
            return []
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning(
                "registry.json corrupt at %s — treating as empty; "
                "previous content preserved at %s.bak",
                self.json_path, self.json_path,
            )
            # Don't lose evidence — back up the corrupted file once.
            backup = self.json_path.with_suffix(".json.corrupt")
            if not backup.exists():
                backup.write_bytes(self.json_path.read_bytes())
            return []
        return [RegistryEntry.model_validate(e) for e in data.get("entries", [])]

    def _write_unlocked(self, entries: list[RegistryEntry]) -> None:
        # Sort for deterministic on-disk content — helps diffs and
        # bug-report attachments.
        sorted_entries = sorted(entries, key=lambda e: (e.name, e.version))
        payload = json.dumps(
            {
                "schema_version": 1,
                "entries": [e.model_dump(mode="json") for e in sorted_entries],
            },
            indent=2,
            sort_keys=True,
        )
        atomic_write_text(
            self.json_path,
            payload + "\n",
            mode=0o644,
            backup=False,
        )

    # -- public ops ----------------------------------------------------

    def list(self) -> list[RegistryEntry]:
        """Return all installed entries, sorted by ``(name, version)``."""
        with self.transaction(create=False):
            entries = self._read_unlocked()
        return sorted(entries, key=lambda e: (e.name, e.version))

    def find(
        self, name: str, version: str | None = None
    ) -> RegistryEntry | None:
        """Return the entry matching ``name`` (optionally ``version``).

        With ``version=None``, returns the newest installed version (by
        plain string sort — semver-aware sort lives in the installer's
        constraint resolver).
        """
        with self.transaction(create=False):
            entries = self._read_unlocked()
        matches = [
            e for e in entries
            if e.name == name and (version is None or e.version == version)
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda e: e.version, reverse=True)[0]

    def find_by_name(self, name: str) -> list[RegistryEntry]:
        """All installed versions of ``name``, oldest version first."""
        with self.transaction(create=False):
            entries = self._read_unlocked()
        return sorted(
            (e for e in entries if e.name == name),
            key=lambda e: e.version,
        )

    def add(self, entry: RegistryEntry) -> None:
        """Add (or replace) the entry for ``(name, version)``.

        Re-adding the same ``(name, version)`` is idempotent: the old
        entry is replaced (lets ``acc-pkg install`` re-run safely after
        an unpack hiccup without producing a duplicate).
        """
        with self.transaction():
            entries = self._read_unlocked()
            entries = [e for e in entries if e.key != entry.key]
            entries.append(entry)
            self._write_unlocked(entries)

    def remove(self, name: str, version: str) -> bool:
        """Remove the entry; returns ``True`` if anything was removed."""
        with self.transaction():
            entries = self._read_unlocked()
            new = [e for e in entries if e.key != (name, version)]
            if len(new) == len(entries):
                return False
            self._write_unlocked(new)
        return True

    def make_entry(
        self,
        *,
        name: str,
        version: str,
        content_sha256: str,
        install_path: Path,
    ) -> RegistryEntry:
        """Construct a ``RegistryEntry`` with ``installed_at = now``.

        Convenience for the installer (slice 5).  Doesn't touch disk.
        """
        return RegistryEntry(
            name=name,
            version=version,
            content_sha256=content_sha256,
            install_path=str(install_path),
            installed_at=_now_iso(),
        )


def installed_capability_dirs(kind: str, registry: "Registry | None" = None) -> list[Path]:
    """Return existing ``<install_path>/<kind>`` dirs across installed packages.

    ``kind`` is ``"mcps"`` or ``"skills"``.  Used by the MCP/skill
    registries' dual-source loaders so capabilities bundled in an
    installed ``.accpkg`` are discovered alongside the in-tree ones
    (Stage 2 — the packaged skills/MCPs land under ``ACC_PACKAGES_ROOT``
    but were previously never scanned).

    Ordered by installed ``(name, version)`` so a newer pack's dir is
    returned after an older one's; callers scan in-tree LAST so core
    baseline capabilities stay authoritative on an id collision.
    Best-effort: a missing/empty registry returns ``[]``.
    """
    try:
        reg = registry or Registry()
        entries = reg.list()
    except Exception:  # noqa: BLE001 — discovery must never crash agent boot
        return []
    dirs: list[Path] = []
    for entry in entries:
        d = Path(entry.install_path) / kind
        if d.is_dir():
            dirs.append(d)
    return dirs
