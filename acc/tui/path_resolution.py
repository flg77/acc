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

    # --- 2. Repo-anchored ---
    repo_candidate = (_repo_root() / default_dir_name).resolve()
    if repo_candidate.exists():
        logger.debug(
            "path_resolution: %s resolved via repo anchor → %s",
            default_dir_name, repo_candidate,
        )
        return repo_candidate

    # --- 3. CWD fallback ---
    cwd_candidate = (Path.cwd() / default_dir_name).resolve()
    logger.debug(
        "path_resolution: %s fell back to cwd → %s "
        "(repo anchor %s and env %s did not resolve)",
        default_dir_name, cwd_candidate, repo_candidate, env_var,
    )
    return cwd_candidate
