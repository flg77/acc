"""Capture a deployment, and put it back.

ACC can preserve volumes; it cannot capture a *deployment*. Configuration, the
package registry, installed trees and the session tracelog live in different
places, and reconstructing a host has meant remembering all of them.

The position on secrets is the design decision, and it is deliberately
conservative: **an archive contains no secret values.** It records which secret
*names* the deployment needs, so a restore can say precisely what must be
provisioned out of band — and refuse rather than hand back a deployment that
looks restored and cannot authenticate.

That refusal is the point. A backup format that carried credentials would be a
credential store with none of the handling a credential store gets: copied to
laptops, attached to tickets, kept long after the key was rotated.

Two further refusals:

* **restore will not clobber a running deployment** without an explicit
  acknowledgement — the moment someone restores the wrong archive is the moment
  they most need it to have asked;
* **restore across an incompatible ACC version refuses with a reason** rather
  than writing files a newer schema will reject at boot.
"""

from __future__ import annotations

import json
import logging
import os
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("acc.backup")

MANIFEST_NAME = "acc-backup.json"
ARCHIVE_VERSION = 1

#: What an archive may hold, in tiers.
TIERS = ("config", "packages", "tracelog")
DEFAULT_TIERS = ("config", "packages")

#: A value matching any of these key shapes is redacted before it is written.
#: Belt and braces: the config collector already skips secret-marked keys, and
#: this catches anything a future schema addition forgets to mark.
_SECRET_MARKERS = ("password", "secret", "token", "signing_key", "api_key")


class BackupError(Exception):
    """A backup or restore was refused. The message is operator-facing."""


@dataclass
class Manifest:
    """What this archive is, and what it needs that it does not carry."""

    archive_version: int = ARCHIVE_VERSION
    acc_version: str = ""
    created_at: float = 0.0
    label: str = ""
    host: str = ""
    tiers: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    required_secrets: list[str] = field(default_factory=list)
    collectives: list[str] = field(default_factory=list)
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_version": self.archive_version,
            "acc_version": self.acc_version,
            "created_at": self.created_at,
            "label": self.label,
            "host": self.host,
            "tiers": self.tiers,
            "files": self.files,
            "required_secrets": self.required_secrets,
            "collectives": self.collectives,
            "notes": self.notes,
        }


def _acc_version() -> str:
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return version("acc")
    except Exception:  # pragma: no cover — source checkout
        return "unknown"


def _is_secret_key(dotted: str) -> bool:
    leaf = dotted.rsplit(".", 1)[-1].lower()
    if leaf.endswith("_env"):
        return False  # names a variable; carries nothing
    return any(marker in leaf for marker in _SECRET_MARKERS)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def required_secret_names(repo_root: Path | None = None) -> list[str]:
    """Secret names this deployment needs but the archive will not carry."""
    names: set[str] = set()

    # Credentials for models a role is actually BOUND to. The registry lists
    # every provider ACC can talk to; demanding all of them on restore would
    # block a deployment that legitimately uses one, which is the same
    # false-positive that made the health report unreadable before it was
    # scoped the same way.
    try:
        from acc.models import load_models, load_role_chains  # noqa: PLC0415

        bound: set[str] = set()
        for chain in load_role_chains().values():
            bound.update(chain)
        for entry in load_models():
            if entry.model_id in bound and entry.api_key_env:
                names.add(entry.api_key_env.strip())
    except Exception:  # pragma: no cover
        logger.debug("backup: model registry unreadable", exc_info=True)

    # Plus whatever this deployment already sets: if a name is in the live
    # .env it is in use here, and a restore that dropped it would produce a
    # deployment missing something the source had.
    try:
        from acc import configschema as schema  # noqa: PLC0415

        env_path = schema.resolve_path("env", repo_root=repo_root)
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    name = line.split("=", 1)[0].strip()
                    if name and _is_secret_key(name):
                        names.add(name)
    except Exception:  # pragma: no cover
        logger.debug("backup: env file unreadable", exc_info=True)

    return sorted(n for n in names if n)


def _config_files(repo_root: Path | None) -> list[Path]:
    """The configuration surfaces, EXCLUDING the secret-bearing one."""
    from acc import configschema as schema  # noqa: PLC0415

    out: list[Path] = []
    for spec in schema.FILES:
        if spec.secret_bearing:
            # .env is never captured. Its NAMES are recorded in the manifest.
            continue
        path = schema.resolve_path(spec.id, repo_root=repo_root)
        if path.is_file():
            out.append(path)
    return out


def _redact_config(text: str) -> str:
    """Blank any secret-looking scalar, in case the schema missed one."""
    import re  # noqa: PLC0415

    pattern = re.compile(
        r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?!\s*$)(.*)$", re.M
    )

    def _sub(match: "re.Match[str]") -> str:
        indent, key, value = match.groups()
        if _is_secret_key(key) and value.strip() not in ('""', "''", ""):
            return f'{indent}{key}: ""   # redacted by acc backup'
        return match.group(0)

    return pattern.sub(_sub, text)


def _package_paths(repo_root: Path | None) -> list[Path]:
    root = Path(repo_root or Path(__file__).resolve().parent.parent)
    out: list[Path] = []
    for name in ("catalogs.yaml", ".acc"):
        candidate = root / name
        if candidate.exists():
            out.append(candidate)
    return out


def _tracelog_paths() -> list[Path]:
    try:
        from acc import tracelog  # noqa: PLC0415

        root = getattr(tracelog, "tracelog_root", None)
        if callable(root):
            path = Path(root())
            return [path] if path.exists() else []
    except Exception:  # pragma: no cover
        logger.debug("backup: tracelog root unavailable", exc_info=True)
    return []


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def create(
    output: str | Path,
    *,
    tiers: Iterable[str] = DEFAULT_TIERS,
    label: str = "",
    repo_root: Path | None = None,
) -> Manifest:
    """Write an archive. Never includes a secret value.

    Raises:
        BackupError: an unknown tier, or the archive cannot be written.
    """
    chosen = [t for t in tiers]
    unknown = sorted(set(chosen) - set(TIERS))
    if unknown:
        raise BackupError(
            f"unknown backup tier(s): {', '.join(unknown)}. Known: {', '.join(TIERS)}"
        )

    root = Path(repo_root or Path(__file__).resolve().parent.parent)
    manifest = Manifest(
        # Read at call time, not from the dataclass default: the default is
        # bound when the class is defined, so a build that changes the format
        # version would keep stamping the old one.
        archive_version=ARCHIVE_VERSION,
        acc_version=_acc_version(),
        created_at=time.time(),
        label=label,
        host=os.environ.get("HOSTNAME", "") or os.environ.get("COMPUTERNAME", ""),
        tiers=chosen,
        required_secrets=required_secret_names(repo_root),
        notes=(
            "This archive contains NO secret values. required_secrets lists the "
            "names the deployment needs; provision them before restoring."
        ),
    )

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(out_path, "w:gz") as tar:
            if "config" in chosen:
                for path in _config_files(repo_root):
                    _add_redacted(tar, path, f"config/{path.name}")
                    manifest.files.append(f"config/{path.name}")
            if "packages" in chosen:
                for path in _package_paths(repo_root):
                    arc = f"packages/{path.name}"
                    tar.add(path, arcname=arc)
                    manifest.files.append(arc)
            if "tracelog" in chosen:
                for path in _tracelog_paths():
                    arc = f"tracelog/{path.name}"
                    tar.add(path, arcname=arc)
                    manifest.files.append(arc)

            _add_bytes(
                tar,
                MANIFEST_NAME,
                json.dumps(manifest.as_dict(), indent=2, sort_keys=True).encode(),
            )
    except OSError as exc:
        raise BackupError(f"cannot write {out_path}: {exc}") from exc

    logger.info(
        "backup: wrote %s (%d file(s), tiers: %s)",
        out_path, len(manifest.files), ", ".join(chosen),
    )
    return manifest


def _add_redacted(tar: tarfile.TarFile, path: Path, arcname: str) -> None:
    """Add a config file with any secret-looking scalar blanked."""
    text = path.read_text(encoding="utf-8", errors="replace")
    _add_bytes(tar, arcname, _redact_config(text).encode("utf-8"))


def _add_bytes(tar: tarfile.TarFile, arcname: str, payload: bytes) -> None:
    import io  # noqa: PLC0415

    info = tarfile.TarInfo(name=arcname)
    info.size = len(payload)
    info.mtime = int(time.time())
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(payload))


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def read_manifest(archive: str | Path) -> Manifest:
    """Read an archive's manifest without extracting anything.

    Raises:
        BackupError: not an ACC archive, or unreadable.
    """
    path = Path(archive)
    try:
        with tarfile.open(path, "r:*") as tar:
            member = tar.extractfile(MANIFEST_NAME)
            if member is None:
                raise BackupError(f"{path.name} has no {MANIFEST_NAME}")
            raw = json.loads(member.read().decode("utf-8"))
    except BackupError:
        raise
    except (OSError, tarfile.TarError, ValueError, KeyError) as exc:
        raise BackupError(f"{path.name} is not a readable ACC backup: {exc}") from exc

    manifest = Manifest()
    for key, value in raw.items():
        if hasattr(manifest, key):
            setattr(manifest, key, value)
    return manifest


@dataclass
class RestorePlan:
    """What a restore would do, before it does it."""

    manifest: Manifest
    would_replace: list[str] = field(default_factory=list)
    would_create: list[str] = field(default_factory=list)
    missing_secrets: list[str] = field(default_factory=list)
    version_problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.version_problem and not self.missing_secrets

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.as_dict(),
            "would_replace": self.would_replace,
            "would_create": self.would_create,
            "missing_secrets": self.missing_secrets,
            "version_problem": self.version_problem,
            "ok": self.ok,
        }


def plan(
    archive: str | Path,
    *,
    repo_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> RestorePlan:
    """Exactly what restoring this archive would replace, and what is missing."""
    env = environ if environ is not None else os.environ
    manifest = read_manifest(archive)
    result = RestorePlan(manifest=manifest)

    if manifest.archive_version != ARCHIVE_VERSION:
        result.version_problem = (
            f"archive format v{manifest.archive_version}; this build reads "
            f"v{ARCHIVE_VERSION}. Restore with a matching ACC version."
        )

    root = Path(repo_root or Path(__file__).resolve().parent.parent)
    for arc in manifest.files:
        target = root / Path(arc).name
        (result.would_replace if target.exists() else result.would_create).append(
            str(target)
        )

    result.missing_secrets = [
        name for name in manifest.required_secrets if not str(env.get(name, "")).strip()
    ]
    return result


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(
    archive: str | Path,
    *,
    repo_root: Path | None = None,
    force: bool = False,
    allow_missing_secrets: bool = False,
    environ: dict[str, str] | None = None,
) -> RestorePlan:
    """Restore an archive.

    Raises:
        BackupError: the archive is for another format version, required
            secrets are absent, or files would be overwritten without *force*.
            Nothing is written in any of those cases.
    """
    result = plan(archive, repo_root=repo_root, environ=environ)

    if result.version_problem:
        raise BackupError(result.version_problem)

    if result.missing_secrets and not allow_missing_secrets:
        raise BackupError(
            "these secrets are not present, and the archive does not carry them:\n  "
            + "\n  ".join(result.missing_secrets)
            + "\nProvision them and retry, or pass --allow-missing-secrets to "
            "restore a deployment that cannot yet authenticate."
        )

    if result.would_replace and not force:
        raise BackupError(
            "restore would replace existing files:\n  "
            + "\n  ".join(result.would_replace)
            + "\nRe-run with --force to acknowledge."
        )

    root = Path(repo_root or Path(__file__).resolve().parent.parent)
    with tarfile.open(Path(archive), "r:*") as tar:
        for arc in result.manifest.files:
            member = tar.extractfile(arc)
            if member is None:
                continue
            target = root / Path(arc).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(member.read())

    logger.info("backup: restored %d file(s) into %s", len(result.manifest.files), root)
    return result


def scan_for_secrets(archive: str | Path, values: Iterable[str]) -> list[str]:
    """Which of *values* appear anywhere in the archive.

    Exists so the no-secrets claim is testable against a real archive rather
    than asserted about the code that wrote it.
    """
    found: list[str] = []
    wanted = [v for v in values if v]
    if not wanted:
        return found
    with tarfile.open(Path(archive), "r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            blob = handle.read().decode("utf-8", "replace")
            for value in wanted:
                if value in blob and value not in found:
                    found.append(value)
    return found
