"""Verify every skill and MCP against its recorded digest before first use (061).

The operator, 2026-10-18: "From now on we ensure that this matters and we fail
with an explicit message."  Before the skill and MCP registries load a
capability, :func:`gate` recomputes its tree digest
(:func:`acc.pkg.build.tree_digest`) and compares it with the expected one:

1. **Pack manifest first.**  A capability under ``<install_path>/skills/<name>``
   or ``<install_path>/mcps/<name>`` takes the ``sha256`` its pack's signed
   manifest (``<install_path>/accpkg.yaml``, schema v2) records.  A v1 pack
   records none.
2. **Then the built-in manifest** (:mod:`acc.pkg.builtin_capabilities`) for the
   image- and RPM-shipped trees.

A mismatch or a missing digest fails the check.  What happens then depends on
``ACC_CAPABILITY_VERIFY``:

* ``report`` (the default this release): log the explicit message at WARNING
  and load the capability anyway.
* ``enforce``: log it at ERROR and refuse the capability — the registry drops
  it as it drops any capability that fails to load.

The message names the capability, the expected digest, the actual digest, the
source the expectation came from, and what to do.  Results are cached per
directory and keyed by every file's mtime and size, so a reload only re-hashes
a tree that changed.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("acc.capability_verify")

MODE_ENV = "ACC_CAPABILITY_VERIFY"
MODES = ("report", "enforce")
DEFAULT_MODE = "report"

VERIFIED = "verified"
MISMATCH = "mismatch"
MISSING_DIGEST = "missing_digest"
UNREADABLE = "unreadable"

_KIND_DIR = {"skill": "skills", "mcp": "mcps"}


def mode() -> str:
    """The configured mode; an unknown value falls back to ``report`` loudly."""
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if not raw:
        return DEFAULT_MODE
    if raw not in MODES:
        logger.warning(
            "%s=%r is not one of %s; using %r", MODE_ENV, raw, "|".join(MODES), DEFAULT_MODE,
        )
        return DEFAULT_MODE
    return raw


@dataclass(frozen=True)
class CapabilityCheck:
    """The outcome of checking one capability directory."""

    kind: str          # "skill" | "mcp"
    name: str
    path: str
    status: str        # VERIFIED | MISMATCH | MISSING_DIGEST | UNREADABLE
    expected: str      # "" when no digest is recorded
    actual: str        # "" when the tree could not be read
    source: str        # where the expectation came from, human-readable

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED

    def message(self) -> str:
        """One line that says what failed and what to change."""
        head = f"{self.kind} {self.name!r} at {self.path}"
        if self.status == VERIFIED:
            return f"{head}: verified sha256 {self.actual} ({self.source})"
        if self.status == MISMATCH:
            return (
                f"{head}: digest mismatch — expected sha256 {self.expected}, "
                f"actual sha256 {self.actual} (source: {self.source}). The files "
                "changed after they were signed: reinstall the pack, or rebuild "
                "the image, or for a deliberate in-tree edit regenerate the "
                "built-in manifest (python -m acc.pkg.builtin_capabilities --write)."
            )
        if self.status == MISSING_DIGEST:
            return (
                f"{head}: no recorded digest — expected sha256 <none>, actual "
                f"sha256 {self.actual or '<unreadable>'} (source: {self.source}). "
                "Rebuild the pack with a schema v2 manifest (acc-pkg build), or "
                "list the capability in the built-in manifest "
                "(python -m acc.pkg.builtin_capabilities --write)."
            )
        return (
            f"{head}: cannot compute its digest — expected sha256 "
            f"{self.expected or '<none>'}, actual sha256 <unreadable> "
            f"(source: {self.source}). Remove symlinks and unreadable files."
        )


# ---------------------------------------------------------------------------
# Expected digest lookup
# ---------------------------------------------------------------------------


def _pack_expectation(kind: str, name: str, cap_dir: Path) -> tuple[str, str] | None:
    """``(expected, source)`` from the owning pack's manifest, or None if not in a pack."""
    install_path = cap_dir.parent.parent
    manifest_path = install_path / "accpkg.yaml"
    if cap_dir.parent.name != _KIND_DIR[kind] or not manifest_path.is_file():
        return None
    import yaml  # noqa: PLC0415
    from acc.pkg.manifest import AccPkgManifest  # noqa: PLC0415

    try:
        manifest = AccPkgManifest.model_validate(
            yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        )
    except Exception as exc:  # noqa: BLE001 — a bad manifest is a missing digest, not a crash
        return "", f"pack manifest {manifest_path} is unreadable: {exc}"
    pack = f"pack {manifest.name}@{manifest.version}"
    refs = manifest.skills if kind == "skill" else manifest.mcps
    ref = next((r for r in refs if r.name == name), None)
    if ref is None:
        return "", f"{pack} does not list this {kind}"
    if not ref.sha256:
        return "", f"{pack} (schema v{manifest.schema_version}, no per-capability digest)"
    return ref.sha256, f"{pack} signed manifest"


def expected_digest(kind: str, name: str, cap_dir: Path) -> tuple[str, str]:
    """``(expected sha256 or "", source)`` for one capability directory."""
    pack = _pack_expectation(kind, name, cap_dir)
    if pack is not None:
        return pack
    from acc.pkg import builtin_capabilities  # noqa: PLC0415

    builtin = builtin_capabilities.load()
    source = f"built-in manifest ({builtin.trust}: {builtin.detail})"
    if builtin.trust in ("absent", "rejected"):
        return "", source
    return builtin.expected(kind, name), source


# ---------------------------------------------------------------------------
# Check + cache
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[tuple, CapabilityCheck]] = {}
_CACHE_LOCK = threading.Lock()


def _stat_key(cap_dir: Path) -> tuple:
    """mtime + size of every file: the cache key for a tree's digest."""
    out = []
    for p in sorted(cap_dir.rglob("*")):
        if "__pycache__" in p.parts or not p.is_file():
            continue
        st = p.stat()
        out.append((p.relative_to(cap_dir).as_posix(), st.st_mtime_ns, st.st_size))
    return tuple(out)


def check(kind: str, name: str, cap_dir: Path) -> CapabilityCheck:
    """Compare one capability directory with its expected digest (cached)."""
    from acc.pkg.build import tree_digest  # noqa: PLC0415

    cap_dir = Path(cap_dir)
    key_path = str(cap_dir.resolve())
    expected, source = expected_digest(kind, name, cap_dir)
    try:
        stat_key = (_stat_key(cap_dir), expected)
    except OSError:
        stat_key = None
    if stat_key is not None:
        with _CACHE_LOCK:
            hit = _CACHE.get(key_path)
        if hit is not None and hit[0] == stat_key:
            return hit[1]

    try:
        actual = tree_digest(cap_dir)
    except (OSError, ValueError):
        actual = ""
    if not actual:
        status = UNREADABLE
    elif not expected:
        status = MISSING_DIGEST
    elif expected == actual:
        status = VERIFIED
    else:
        status = MISMATCH
    result = CapabilityCheck(
        kind=kind, name=name, path=str(cap_dir), status=status,
        expected=expected, actual=actual, source=source,
    )
    if stat_key is not None:
        with _CACHE_LOCK:
            _CACHE[key_path] = (stat_key, result)
    return result


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def gate(kind: str, name: str, cap_dir: Path) -> bool:
    """Check one capability before it loads; True when the registry may load it.

    ``report`` mode always returns True and logs a failure at WARNING;
    ``enforce`` mode returns False on any failure and logs it at ERROR.
    """
    result = check(kind, name, cap_dir)
    if result.ok:
        logger.debug("capability_verify: %s", result.message())
        return True
    if mode() == "enforce":
        logger.error("capability_verify: REFUSED %s", result.message())
        return False
    logger.warning("capability_verify: [report] %s", result.message())
    return True

