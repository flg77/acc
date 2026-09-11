"""Where ACC lives on this host -- one discovery rule for every default.

`20260909-acc-install` IN-01 (proposal 055).  Until now every default was
relative to the current directory: ``acc-config.yaml``, the roles root (two
different defaults in two modules), ``models.yaml`` / ``collective.yaml`` /
``catalogs.yaml``, the skills and mcps trees.  That is why ``acc-cli`` worked
inside a checkout and nowhere else.  This module answers three questions
once, and every default reads the answer:

* :func:`home` -- the operator's ACC directory (their configs and overlays):
  ``$ACC_HOME``; else ``~/.config/acc`` when it holds ``acc-config.yaml``;
  else ``/etc/acc`` when it does; else the checkout the current directory is
  in (``$ACC_REPO_ROOT`` or a walk up to ``acc-deploy.sh``); else nothing.
* :func:`share` -- the read-only data trees (roles, skills, mcps,
  collectives, container): ``$ACC_SHARE``; else the home if it carries a
  ``roles/`` tree; else ``<sys.prefix>/share/acc``; else ``/usr/share/acc``;
  else the checkout.
* :func:`resolve` -- one named thing (``config``, ``models``, ``roles`` …)
  as a path **and the source it came from** (``env``, ``home``, ``share``,
  ``checkout``, ``cwd``, ``default``), so ``acc-cli doctor --paths`` can show
  an operator exactly why a file was picked.

Environment variables keep winning everywhere: nothing that works today
stops working, and a container's ``/app/...`` layout is just a home the
image sets.  Legacy cwd-relative defaults remain the last resort, so a
developer inside a checkout sees no change at all.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

#: Markers that identify an ACC checkout when walking up from the cwd.
CHECKOUT_MARKER = "acc-deploy.sh"
_WALK_UP_LIMIT = 8

#: kind -> (env var, relative name, family).  ``file`` kinds are operator
#: configs (home, then cwd); ``tree`` kinds are read-only data (share, then
#: cwd); ``state`` kinds are written at runtime (home-derived state).
KINDS: dict[str, tuple[str, str, str]] = {
    "config": ("ACC_CONFIG_PATH", "acc-config.yaml", "file"),
    "models": ("ACC_MODELS_PATH", "models.yaml", "file"),
    "collective": ("ACC_COLLECTIVE_PATH", "collective.yaml", "file"),
    "catalogs": ("ACC_SYSTEM_CATALOG", "catalogs.yaml", "file"),
    "env": ("ACC_ENV_FILE", ".env", "file"),
    "roles": ("ACC_ROLES_ROOT", "roles", "tree"),
    "skills": ("ACC_SKILLS_ROOT", "skills", "tree"),
    "mcps": ("ACC_MCPS_ROOT", "mcps", "tree"),
    "collectives": ("ACC_COLLECTIVES_ROOT", "collectives", "tree"),
    "container": ("ACC_CONTAINER_ROOT", "container", "tree"),
    "deploy": ("ACC_DEPLOY_SCRIPT", "acc-deploy.sh", "tree"),
    "packages": ("ACC_PACKAGES_ROOT", "packages", "state"),
    "instances": ("ACC_INSTANCES_DIR", "instances", "state"),
    "sessions": ("ACC_SESSIONS_DIR", "sessions", "state"),
    "trace": ("ACC_TRACELOG_DIR", "trace", "state"),
    "workspaces": ("ACC_WORKSPACE_HOST_DIR", "workspaces", "state"),
}

#: The legacy defaults (what the code used before this module), kept as
#: the last resort so nothing changes inside a checkout or a container.
LEGACY_DEFAULTS: dict[str, str] = {
    "config": "acc-config.yaml",
    "models": "models.yaml",
    "collective": "collective.yaml",
    "catalogs": "catalogs.yaml",
    "env": ".env",
    "roles": "roles",
    "skills": "skills",
    "mcps": "mcps",
    "collectives": "collectives",
    "container": "container",
    "deploy": "acc-deploy.sh",
    "packages": "/var/lib/acc/packages",
    "instances": "instances",
    "sessions": str(Path.home() / ".acc" / "sessions"),
    "trace": "/logs/sessions",
    "workspaces": "workspaces",
}

#: Extra names a kind answers to in a home.  A dotfile is a poor `%config` in a
#: package, so the RPM installs the secrets as ``acc.env``; discovery has to
#: find it where the package put it.
ALT_NAMES: dict[str, tuple[str, ...]] = {
    "env": ("acc.env",),
}


@dataclass(frozen=True)
class Resolved:
    kind: str
    path: Path
    source: str          # env | home | share | package | checkout | cwd | state | default
    exists: bool

    def __str__(self) -> str:
        mark = "" if self.exists else "  (missing)"
        return f"{self.kind:<12} {self.source:<9} {self.path}{mark}"


def _is_file(p: Path) -> bool:
    """``p.is_file()`` that survives a directory we may not read.

    Discovery probes candidates that belong to other users -- ``/etc/acc`` is
    ``root:acc``, and an operator outside that group cannot even ``stat`` inside
    it.  A candidate we cannot look into is simply not ours; it is never a
    reason to abort the command.
    """
    try:
        return p.is_file()
    except OSError:
        return False


def _is_dir(p: Path) -> bool:
    """``p.is_dir()``, unreadable candidates included -- see :func:`_is_file`."""
    try:
        return p.is_dir()
    except OSError:
        return False


def _exists(p: Path) -> bool:
    """``p.exists()``, unreadable candidates included -- see :func:`_is_file`."""
    try:
        return p.exists()
    except OSError:
        return False


def _cwd() -> Path | None:
    """The current directory, or ``None`` when we may not look at it.

    Found on acc1: the `acc` system user started in another user's home dies in
    ``os.getcwd()``.  A directory we cannot see is not a checkout and holds no
    configuration -- it must not take the command down with it.
    """
    try:
        return Path.cwd()
    except OSError:
        return None


def _dir(value: str | None) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    p = Path(text).expanduser()
    return p.resolve() if _is_dir(p) else None


def checkout(start: Path | None = None) -> Path | None:
    """The ACC checkout the current directory is in, if any.

    ``$ACC_REPO_ROOT`` wins (the same variable the TUI honours); else walk up
    from *start* (the cwd) looking for ``acc-deploy.sh``.  A ``pyproject.toml``
    beside an ``acc/`` package counts too, so a fresh clone without the deploy
    script is still a checkout.
    """
    explicit = _dir(os.environ.get("ACC_REPO_ROOT"))
    if explicit is not None:
        return explicit
    here = start or _cwd()
    if here is None:
        return None
    cwd = here.resolve()
    for ancestor in [cwd, *cwd.parents][:_WALK_UP_LIMIT]:
        if _is_file(ancestor / CHECKOUT_MARKER):
            return ancestor
        if _is_file(ancestor / "pyproject.toml") and _is_dir(ancestor / "acc"):
            return ancestor
    return None


def user_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base).expanduser() if base else Path.home() / ".config") / "acc"


def system_config_dir() -> Path:
    return Path("/etc/acc")


#: A system install's state root, owned by the ``acc`` service user.  It is
#: shared with the operator through the ``acc`` group (IN-07, operator
#: 2026-09-11): the package makes it setgid and group-writable, the unit runs
#: with ``UMask=0002``, and the operator's own commands adopt the same umask.
SYSTEM_STATE = Path("/var/lib/acc")
SERVICE_GROUP = "acc"


def shared_state() -> bool:
    """This process's state is the system install's -- shared with the service."""
    found = state_root()
    if found is None:
        return False
    try:
        return found[0].resolve() == SYSTEM_STATE.resolve()
    except OSError:
        return False


def adopt_shared_umask() -> None:
    """Keep what this process writes into shared state writable for the group.

    Called first by every host command (``acc``, ``acc-cli``, ``acc-pkg``).  It
    only clears the group-write bit of the current umask, and only when the
    state is the system install's: a package the operator installs must stay
    writable for the service, and the reverse.  Nothing else changes.
    """
    if not shared_state():
        return
    current = os.umask(0)
    os.umask(current & ~0o020)


def state_hint() -> str:
    """What to do when the shared state is not writable for this user, or ``""``."""
    if not shared_state() or os.access(SYSTEM_STATE, os.W_OK):
        return ""
    return (
        f"note: {SYSTEM_STATE} is the service's state and you cannot write it -- "
        f"join its group: sudo usermod -aG {SERVICE_GROUP} $USER (then log in again)"
    )


def home() -> tuple[Path, str] | None:
    """The operator's ACC directory and how it was found."""
    explicit = _dir(os.environ.get("ACC_HOME"))
    if explicit is not None:
        return explicit, "env"
    for candidate, source in ((user_config_dir(), "home"), (system_config_dir(), "home")):
        if _is_file(candidate / "acc-config.yaml"):
            return candidate.resolve(), source
    repo = checkout()
    if repo is not None:
        return repo, "checkout"
    return None


def package_share_dir() -> Path:
    """The tree setup.py ships inside the wheel (``acc/_share``; IN-05)."""
    return Path(__file__).resolve().parent / "_share"


def share() -> tuple[Path, str] | None:
    """The read-only data trees and how they were found."""
    explicit = _dir(os.environ.get("ACC_SHARE"))
    if explicit is not None:
        return explicit, "env"
    found = home()
    if found is not None and _is_dir(found[0] / "roles"):
        return found[0], found[1] if found[1] == "checkout" else "share"
    packaged = package_share_dir()
    if _is_dir(packaged / "roles"):
        return packaged.resolve(), "package"
    for candidate in (Path(sys.prefix) / "share" / "acc", Path("/usr/share/acc")):
        if _is_dir(candidate / "roles"):
            return candidate.resolve(), "share"
    repo = checkout()
    if repo is not None:
        return repo, "checkout"
    return None


def state_root() -> tuple[Path, str] | None:
    """Where runtime state goes when nothing more specific is configured."""
    explicit = _dir(os.environ.get("ACC_STATE"))
    if explicit is not None:
        return explicit, "env"
    found = home()
    if found is None:
        return None
    root, source = found
    if source == "checkout":
        return root, "checkout"                       # the legacy in-tree layout
    if root == system_config_dir().resolve():
        return Path("/var/lib/acc"), "state"
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    return ((Path(base).expanduser() if base else Path.home() / ".local" / "state") / "acc"), "state"


def resolve(kind: str) -> Resolved:
    """One named path with its source.  Unknown kinds raise ``KeyError``."""
    env_var, name, family = KINDS[kind]
    raw = os.environ.get(env_var, "").strip() if env_var else ""
    if raw:
        p = Path(raw).expanduser()
        return Resolved(kind, p, "env", _exists(p))
    if family == "file":
        found = home()
        if found is not None:
            for candidate in (name, *ALT_NAMES.get(kind, ())):
                if _exists(found[0] / candidate):
                    return Resolved(kind, found[0] / candidate, found[1], True)
    elif family == "tree":
        for found in (share(), home()):
            if found is not None and _exists(found[0] / name):
                return Resolved(kind, found[0] / name, found[1], True)
    else:  # state
        found = state_root()
        if found is not None:
            p = found[0] / name
            return Resolved(kind, p, found[1], _exists(p))
    here = _cwd()
    if here is not None:
        cwd_candidate = here / name
        if _exists(cwd_candidate):
            return Resolved(kind, cwd_candidate, "cwd", True)
    legacy = Path(LEGACY_DEFAULTS[kind])
    return Resolved(kind, legacy, "default", _exists(legacy))


def path_of(kind: str) -> str:
    """The resolved path as a string -- the drop-in for a legacy default
    (a legacy default keeps its literal spelling, e.g. ``/var/lib/acc/packages``)."""
    r = resolve(kind)
    return LEGACY_DEFAULTS[kind] if r.source == "default" else str(r.path)


def report(kinds: Iterable[str] | None = None) -> list[Resolved]:
    """Every kind resolved, for ``acc-cli doctor --paths``."""
    return [resolve(k) for k in (kinds or KINDS)]


def describe() -> str:
    """The layout as text: where home, share and state come from, then each path."""
    lines = []
    for label, found in (("home", home()), ("share", share()), ("state", state_root())):
        lines.append(f"{label:<12} {found[1]:<9} {found[0]}" if found else f"{label:<12} -         (none)")
    hint = state_hint()
    if hint:
        lines.append(hint)
    lines.append("")
    lines.extend(str(r) for r in report())
    return "\n".join(lines)
