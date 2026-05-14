"""Manifest-root path resolution shared across TUI screens.

The TUI's Ecosystem screen needs to discover ``roles/``, ``skills/`` and
``mcps/`` directories at runtime.  Earlier code resolved these via
``os.environ.get("ACC_*_ROOT", "<literal>")``, which evaluates the literal
relative to ``os.getcwd()``.  When the TUI is launched from outside the
repo root — e.g. ``acc-tui`` after pip install, or a container with
``WORKDIR=/app`` — some manifest dirs resolve and some don't, depending
on cwd at process start.  The result was the bug surface PR-A fixes:

* SKILLS table reports "no skills loaded" even when ``skills/echo/`` exists.
* MCP SERVERS table same fault.
* ROLE LIBRARY may also intermittently fail under containerised launches.

This module replaces the per-screen helpers with one canonical resolver
that:

1. Honours an absolute env-var override (e.g. ``ACC_SKILLS_ROOT=/srv/...``).
2. Falls back to a **repo-anchored** path computed from this module's own
   filesystem location: ``<repo>/<default_dir_name>``, where ``<repo>``
   is the parent of the ``acc/`` package directory.  Works in editable
   installs and in container layouts where ``acc/`` lives next to the
   manifest dirs.
3. Falls back to the cwd-relative literal as a last resort, preserving
   backwards-compatibility for the unit-test harness which sets up
   fixtures under the test cwd.

Returns an absolute :class:`pathlib.Path` so downstream callers can pass
the result straight into :class:`acc.skills.SkillRegistry.load_from` /
:class:`acc.mcp.MCPRegistry.load_from` without re-resolving.

Usage::

    from acc.tui.path_resolution import resolve_manifest_root

    skills_root = resolve_manifest_root("ACC_SKILLS_ROOT", "skills")
    # → Path("/path/to/repo/skills")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("acc.tui.path_resolution")


def _repo_root() -> Path:
    """Repo root computed from this module's filesystem location.

    Layout assumption: ``<repo>/acc/tui/path_resolution.py`` — three
    parents up from this file gives ``<repo>``.  Confirmed against the
    canonical ACC layout; if the package is ever vendored or relocated
    this anchor moves with it (which is the desired behaviour).
    """
    return Path(__file__).resolve().parent.parent.parent


# TUI Review 14.5 — repo discovery for the pip-installed-from-non-
# repo-cwd failure mode.  When the operator installs ``acc`` into
# their venv's site-packages and runs ``acc-tui`` from anywhere
# other than the repo root, the module-anchored ``_repo_root()``
# returns ``<site-packages>`` — which has no ``roles/`` /
# ``skills/`` / ``mcps/``.  We extend the fallback chain with:
#
# 1. Explicit ``ACC_REPO_ROOT`` env var.
# 2. Walk-up from cwd looking for the ``acc-deploy.sh`` marker
#    (same marker the Podman Desktop extension uses).
#
# Resolution stays best-effort — discovery returns ``None`` when
# nothing matches and the resolver falls back to cwd-relative
# (preserving legacy behaviour for the unit-test fixtures that
# chdir into a tmp tree).
_REPO_MARKERS: tuple[str, ...] = ("acc-deploy.sh", "pyproject.toml")


def _discover_repo_root() -> Path | None:
    """Discover the operator's acc repo root.

    Resolution order:

    1. ``$ACC_REPO_ROOT`` if set AND the directory exists.
    2. Walk up from cwd looking for any of ``_REPO_MARKERS``.
       The deepest matching directory wins (closest ancestor).

    Returns ``None`` when nothing matches — caller decides what
    to do (the resolver falls back to its module-anchored or
    cwd-relative tier).
    """
    raw = os.environ.get("ACC_REPO_ROOT", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate.is_dir():
            logger.debug(
                "path_resolution: repo root via ACC_REPO_ROOT → %s",
                candidate,
            )
            return candidate
        logger.warning(
            "path_resolution: ACC_REPO_ROOT=%r but directory does not exist — "
            "falling back to cwd walk-up",
            raw,
        )

    cwd = Path.cwd().resolve()
    # Bound the walk to 8 levels — protects against rare cases of
    # circular symlinks while still covering deep operator
    # checkouts (~/git/foo/bar/baz/repo/ etc.).
    for ancestor in [cwd, *list(cwd.parents)][:8]:
        if any((ancestor / marker).is_file() for marker in _REPO_MARKERS):
            # ``pyproject.toml`` alone is too generic (every Python
            # repo has one) — require BOTH markers when relying on
            # pyproject, OR accept acc-deploy.sh on its own as a
            # strong ACC-specific signal.
            has_deploy = (ancestor / "acc-deploy.sh").is_file()
            has_pyproject = (ancestor / "pyproject.toml").is_file()
            if has_deploy or (has_pyproject and (ancestor / "acc").is_dir()):
                logger.debug(
                    "path_resolution: repo root via cwd walk-up → %s",
                    ancestor,
                )
                return ancestor
    return None


def resolve_manifest_root(env_var: str, default_dir_name: str) -> Path:
    """Resolve a manifest-root directory to an absolute :class:`Path`.

    Resolution order:

    1. ``os.environ[env_var]`` if set AND the path it names exists.
       The env var wins even when the path is relative — caller is
       trusted to know what they're doing.
    2. Repo-anchored: ``<repo>/<default_dir_name>`` where ``<repo>`` is
       three parents up from this module.  Used when no env override
       is present and the repo-relative dir actually exists on disk.
    3. CWD-relative fallback: ``Path.cwd() / default_dir_name``.  Used
       when neither (1) nor (2) hit — preserves the legacy behaviour
       of pre-PR-A code so the test fixtures (which chdir into a tmp
       dir) keep working.

    Args:
        env_var: Name of the env var that overrides the default
            (e.g. ``"ACC_SKILLS_ROOT"``).
        default_dir_name: Last path segment of the manifest root
            (e.g. ``"skills"``, ``"mcps"``, ``"roles"``).

    Returns:
        Absolute :class:`Path` — caller does not need to ``.resolve()``
        again.  The path is NOT guaranteed to exist; callers that
        require existence should check ``.is_dir()`` themselves.
    """
    # --- 1. Env-var override ---
    raw = os.environ.get(env_var, "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate.exists():
            logger.debug(
                "path_resolution: %s resolved via env var → %s",
                env_var, candidate,
            )
            return candidate
        # Env var set but path missing — log and fall through.  Don't
        # silently use a bad env value; surface the misconfiguration.
        logger.warning(
            "path_resolution: %s=%r but path does not exist — falling back",
            env_var, raw,
        )

    # --- 2. Module-anchored repo root ---
    repo_candidate = (_repo_root() / default_dir_name).resolve()
    if repo_candidate.exists():
        logger.debug(
            "path_resolution: %s resolved via module anchor → %s",
            default_dir_name, repo_candidate,
        )
        return repo_candidate

    # --- 3. Discovered repo root (TUI Review 14.5) ---
    # When acc/ is pip-installed (module anchor → site-packages,
    # which has no roles/ etc.) we discover the operator's repo via
    # the ACC_REPO_ROOT env var or by walking up from cwd looking
    # for the acc-deploy.sh marker.
    discovered = _discover_repo_root()
    if discovered is not None:
        discovered_candidate = (discovered / default_dir_name).resolve()
        if discovered_candidate.exists():
            logger.debug(
                "path_resolution: %s resolved via discovered repo → %s",
                default_dir_name, discovered_candidate,
            )
            return discovered_candidate

    # --- 4. CWD fallback ---
    cwd_candidate = (Path.cwd() / default_dir_name).resolve()
    logger.debug(
        "path_resolution: %s fell back to cwd → %s "
        "(env %s + module anchor %s + discovery did not resolve)",
        default_dir_name, cwd_candidate, env_var, repo_candidate,
    )
    return cwd_candidate
