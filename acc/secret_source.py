"""Where a credential is read from, at the moment it is used.

``20260926-secrets-from-kubernetes-and-a-live-broker`` (lane F3). Until this module,
every credential was an ``os.environ.get`` at its point of use, and "the source" was
whatever put the variable there before the process started.

Two sources:

* **env** (the default) — the process environment, exactly as before.
* **mounted** — one file per name under ``ACC_SECRET_DIR`` (default
  ``/var/run/acc/secrets``): a Kubernetes Secret mounted as a volume. The kubelet
  rewrites those files when the Secret changes, so a credential read here **at call
  time** rotates without a restart, and it is never in ``os.environ``. It is also
  how OpenBao / Vault reach a cluster: the External Secrets Operator and the Vault
  Secrets Operator sync into ordinary Secrets, and this reads the mount.

A name the mount does not hold falls back to the environment, so a deployment
moving one credential at a time keeps working. :func:`origin` says which one
answered.

What this does **not** do: keep a credential from the agent process. A file the
process can read, a tool with filesystem access can read too. Keeping it out of the
agent entirely means the broker runs in another process (the OpenShell gateway, an
egress sidecar). ``doctor --check secrets`` says so rather than letting a mounted
Secret look like isolation.

Values never leave this module except to the caller that asked: nothing here logs
one, and every error names the credential, never its value.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("acc.secret_source")

SOURCE_VAR = "ACC_SECRET_SOURCE"
DIR_VAR = "ACC_SECRET_DIR"
DEFAULT_DIR = "/var/run/acc/secrets"

ENV = "env"
MOUNTED = "mounted"
SOURCES = (ENV, MOUNTED)


def _env(environ: dict[str, str] | None) -> dict[str, str] | os._Environ:
    return environ if environ is not None else os.environ


_WARNED: set[str] = set()


def kind(environ: dict[str, str] | None = None) -> str:
    """The configured source. An unknown value is ``env`` -- logged once, because
    a typo here silently changes nothing else, and this runs on every read."""
    raw = str(_env(environ).get(SOURCE_VAR, "") or "").strip().lower()
    if not raw:
        return ENV
    if raw not in SOURCES:
        if raw not in _WARNED:
            _WARNED.add(raw)
            logger.warning("secret_source: %s=%r is not one of %s; using env",
                           SOURCE_VAR, raw, ", ".join(SOURCES))
        return ENV
    return raw


def directory(environ: dict[str, str] | None = None) -> Path:
    return Path(str(_env(environ).get(DIR_VAR, "") or "").strip() or DEFAULT_DIR)


def _safe_name(name: str) -> bool:
    # A credential name is an environment-variable name; anything else would let
    # a caller walk the filesystem through this function.
    return bool(name) and all(c.isalnum() or c in "_-." for c in name) and ".." not in name


def _from_mount(name: str, environ: dict[str, str] | None) -> str | None:
    if not _safe_name(name):
        return None
    path = directory(environ) / name
    try:
        # A Secret volume stores values as written; a trailing newline from
        # `kubectl create secret --from-file` is almost never part of a key.
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("secret_source: %s is unreadable (%s)", name, type(exc).__name__)
        return None


def get(name: str, *, environ: dict[str, str] | None = None) -> str:
    """The credential called *name*, or ``""`` when no source has it.

    Read every time it is called -- a caller that wants rotation calls this per
    request, not once at construction.
    """
    if not name:
        return ""
    if kind(environ) == MOUNTED:
        value = _from_mount(name, environ)
        if value is not None:
            return value
    return str(_env(environ).get(name, "") or "")


def origin(name: str, *, environ: dict[str, str] | None = None) -> str:
    """Which source would answer for *name*: ``mounted``, ``env`` or ``""``."""
    if kind(environ) == MOUNTED and _from_mount(name, environ) is not None:
        return MOUNTED
    return ENV if str(_env(environ).get(name, "") or "") else ""


def names(*, environ: dict[str, str] | None = None) -> list[str]:
    """The names the mount holds (never values). Empty for the ``env`` source."""
    if kind(environ) != MOUNTED:
        return []
    root = directory(environ)
    try:
        # A Secret volume also holds the kubelet's `..data` symlink and its
        # timestamped directory; neither is a credential.
        return sorted(p.name for p in root.iterdir()
                      if not p.name.startswith(".") and p.is_file())
    except OSError:
        return []


def describe(environ: dict[str, str] | None = None) -> dict[str, object]:
    """For ``doctor`` and status output: where credentials come from. No values."""
    source = kind(environ)
    out: dict[str, object] = {"source": source}
    if source == MOUNTED:
        root = directory(environ)
        out.update({
            "directory": str(root),
            "present": root.is_dir(),
            "names": names(environ=environ),
        })
    return out
