"""Deterministic ``.accpkg`` builder — Stage 0 slice 3.

Build flow
----------

1. Read ``<source_dir>/accpkg.yaml`` and validate it as
   :class:`acc.pkg.manifest.AccPkgManifest`.
2. Walk the source tree (everything except ``accpkg.yaml`` itself).
3. Compute a **content-tree hash**: sha256 of the sorted concatenation
   of ``"<relpath>:<file_sha256>\n"`` lines.  This is the value that
   gets stamped into ``manifest.content_sha256`` — it is not a
   self-referential tarball hash, so it can be recomputed after install
   directly from the unpacked tree.
4. Emit a deterministic gzip-wrapped tar.  Determinism rules:
   * Entries sorted alphabetically.
   * ``accpkg.yaml`` always first (so streaming readers see the
     manifest before any file body).
   * ``mtime = 0`` on every entry + on the gzip header.
   * ``uid = gid = 0``; ``uname = gname = ""``.
   * Mode normalized: ``0644`` for files, ``0755`` for directories.
   * No symlinks, no special files — refused at build time.
5. Return the updated manifest.

The tarball's sha256 (a transport-integrity check distinct from the
content-tree hash) is computed by ``install`` against the catalog's
declared sha256; build doesn't need it for the manifest.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import tarfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from acc.pkg.manifest import AccPkgManifest

logger = logging.getLogger("acc.pkg.build")

MANIFEST_NAME = "accpkg.yaml"

# Fixed file modes for determinism — owner+group can't read execute bits
# from the source tree because tar would then capture them.
_FILE_MODE = 0o644
_DIR_MODE = 0o755


@dataclass(frozen=True)
class BuildResult:
    """Result of a successful build."""

    manifest: AccPkgManifest
    output_path: Path
    content_sha256: str

    @property
    def tarball_sha256(self) -> str:
        """sha256 of the output file bytes (transport integrity)."""
        return _sha256_file(self.output_path)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_tree_hash(file_entries: list[tuple[str, Path]]) -> str:
    """Compute the content-tree hash over a sorted list of files.

    ``file_entries`` is ``[(relpath, abspath), ...]`` (must be sorted by
    relpath).  Returns the hex sha256 of the joined
    ``"<relpath>:<file_sha256>\\n"`` lines.
    """
    h = hashlib.sha256()
    for rel, abs_ in file_entries:
        # Forward slashes for stable cross-platform output.
        norm = rel.replace("\\", "/")
        h.update(f"{norm}:{_sha256_file(abs_)}\n".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Source walk
# ---------------------------------------------------------------------------


def _walk_source(source_dir: Path) -> list[tuple[str, Path]]:
    """Return ``[(relpath, abspath), ...]`` for every regular file under
    ``source_dir`` except the top-level ``accpkg.yaml``.

    Refuses symlinks + special files — they would break determinism /
    introduce attack surface across hosts.  Sorted by relpath.
    """
    out: list[tuple[str, Path]] = []
    manifest_path = source_dir / MANIFEST_NAME
    for p in sorted(source_dir.rglob("*")):
        if p == manifest_path:
            continue
        if p.is_symlink():
            raise ValueError(
                f"symlinks are not allowed in a package source tree: {p}"
            )
        if p.is_dir():
            continue
        if not p.is_file():
            raise ValueError(
                f"only regular files allowed; got special file: {p}"
            )
        rel = p.relative_to(source_dir).as_posix()
        out.append((rel, p))
    return out


# ---------------------------------------------------------------------------
# Deterministic tarball emission
# ---------------------------------------------------------------------------


def _make_tarinfo(name: str, size: int, *, is_dir: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    info.mode = _DIR_MODE if is_dir else _FILE_MODE
    return info


def _emit_tarball(
    output_path: Path,
    manifest_bytes: bytes,
    file_entries: list[tuple[str, Path]],
) -> None:
    """Write the gzipped tar to ``output_path``.

    Layout: ``accpkg.yaml`` first, then files in the order
    ``file_entries`` provides (caller has already sorted).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as raw:
        # mtime=0 zeroes the gzip header timestamp and filename=""
        # suppresses the FNAME field (which would otherwise embed the
        # output file's path and break byte-determinism across paths).
        with gzip.GzipFile(
            filename="",
            fileobj=raw,
            mode="wb",
            mtime=0,
            compresslevel=9,
        ) as gz:
            # USTAR (not PAX) for byte-level determinism — PAX writes a
            # global header that can carry implementation-dependent
            # state.  USTAR caps filename at 100 chars; acceptable for
            # the package layout (manifest at root, roles/<name>/role.yaml
            # etc. stay well under).
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as tar:
                # Manifest first
                manifest_info = _make_tarinfo(MANIFEST_NAME, len(manifest_bytes))
                tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

                # Then files in given order
                for rel, abs_ in file_entries:
                    data = abs_.read_bytes()
                    info = _make_tarinfo(rel, len(data))
                    tar.addfile(info, io.BytesIO(data))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_source_manifest(source_dir: Path) -> AccPkgManifest:
    """Load + validate ``<source_dir>/accpkg.yaml`` (without stamping)."""
    manifest_path = source_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"no {MANIFEST_NAME} at {manifest_path}"
        )
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    return AccPkgManifest.model_validate(raw)


def build(source_dir: Path, output_path: Path) -> BuildResult:
    """Build ``<source_dir>`` into a deterministic ``.accpkg`` at
    ``output_path``.

    Returns the stamped manifest + paths + content hash.  Raises
    :class:`FileNotFoundError` if ``accpkg.yaml`` is missing,
    :class:`ValueError` on invalid source layout, and
    :class:`pydantic.ValidationError` on a malformed manifest.
    """
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()

    # Step 1: load + validate source manifest.  Source manifests have
    # an empty content_sha256; we'll stamp it below.
    manifest = load_source_manifest(source_dir)
    if manifest.content_sha256:
        raise ValueError(
            "source manifest must not pre-declare content_sha256; "
            "build computes it"
        )

    # Step 2-3: walk + content-tree hash
    file_entries = _walk_source(source_dir)
    content_hash = _content_tree_hash(file_entries)

    # Step 4: stamp content_sha256 into a fresh manifest copy
    stamped = manifest.model_copy(update={"content_sha256": content_hash})

    # Re-render the manifest deterministically.  ``model_dump`` returns
    # a dict with insertion order matching field order — pyyaml's
    # safe_dump with sort_keys=False then preserves it.
    manifest_bytes = yaml.safe_dump(
        stamped.model_dump(mode="json"),
        sort_keys=False,
        default_flow_style=False,
        width=999,
    ).encode("utf-8")

    # Step 5: emit tarball
    _emit_tarball(output_path, manifest_bytes, file_entries)

    logger.info(
        "built %s (content_sha256=%s, %d files)",
        output_path.name,
        content_hash[:12],
        len(file_entries),
    )

    return BuildResult(
        manifest=stamped,
        output_path=output_path,
        content_sha256=content_hash,
    )
