"""ACC RoleLoader — file-system-first role definition discovery (ACC-10).

Integrates as **tier-0** into the :class:`acc.role_store.RoleStore` load order,
sitting above the existing ConfigMap / Redis / LanceDB / config tiers:

  0. ``roles/{role_name}/role.yaml``  (this module — highest priority)
  1. File at ACC_ROLE_CONFIG_PATH     (RoleStore tier 1)
  2. Redis key                        (RoleStore tier 2)
  3. LanceDB                          (RoleStore tier 3)
  4. acc-config.yaml default          (RoleStore tier 4)

Merge semantics
---------------
:meth:`RoleLoader.load` performs a **deep merge** of:
    ``roles/_base/role.yaml``  ←  ``roles/{role_name}/role.yaml``

Child values win; missing child keys fall back to base defaults.  The merge
is shallow within each top-level section (e.g. ``category_b_overrides`` is
merged key-by-key, not replaced wholesale).

Hot-reload
----------
When ``watch=True`` (default on Linux) the loader monitors
``roles/{role_name}/role.yaml`` for changes using ``watchdog`` (if available)
or falls back to polling every ``poll_interval_s`` seconds.

When a change is detected and the new version differs from the cached version,
:meth:`RoleLoader.on_reload` is called with the updated
:class:`~acc.config.RoleDefinitionConfig`.  Callers register a callback via
:meth:`RoleLoader.register_reload_callback`.

Usage::

    from acc.role_loader import RoleLoader
    from acc.config import AgentRole

    loader = RoleLoader(roles_root="roles", role_name="coding_agent")
    role_def = loader.load()          # one-shot load
    loader.register_reload_callback(my_callback)
    await loader.start_watch()        # start async watcher (optional)
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import ValidationError

from acc.config import RoleDefinitionConfig

logger = logging.getLogger("acc.role_loader")

_BASE_ROLE_NAME = "_base"
_EXCLUDED_ROLE_NAMES = {"_base", "TEMPLATE"}


def list_roles(base_dir: str | Path = "roles") -> list[str]:
    """Return alphabetically sorted role names found in *base_dir* (REQ-TUI-050).

    Scans ``{base_dir}/*/role.yaml`` and returns the parent directory name for
    each matching file, excluding ``_base`` and ``TEMPLATE`` directories.

    Args:
        base_dir: Path to the ``roles/`` directory.  Defaults to ``"roles"``
            relative to the current working directory.

    Returns:
        Alphabetically sorted list of role name strings.  Empty list if
        *base_dir* does not exist or contains no valid role directories.

    Example::

        >>> from acc.role_loader import list_roles
        >>> list_roles("roles")
        ['account_executive', 'analyst', 'arbiter', 'coding_agent', ...]
    """
    root = Path(base_dir)
    if not root.is_dir():
        logger.debug("list_roles: directory not found: %s", root)
        return []

    names: list[str] = []
    for candidate in root.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name in _EXCLUDED_ROLE_NAMES:
            continue
        if (candidate / "role.yaml").exists():
            names.append(candidate.name)

    return sorted(names)


def _compute_rubric_hash(rubric_path: Path) -> str:
    """Compute the SHA-256 hash of a canonical eval_rubric.yaml file.

    The hash is computed over the **canonical** YAML representation produced
    by ``yaml.dump(..., sort_keys=True, default_flow_style=False)`` so that
    semantically equivalent YAML with different key orderings or whitespace
    produces the same hash.

    Returns:
        64-character lowercase hex digest string, or ``""`` if the file does
        not exist or cannot be parsed.
    """
    if not rubric_path.exists():
        return ""
    try:
        with rubric_path.open(encoding="utf-8") as fh:
            raw_data = yaml.safe_load(fh)
        if raw_data is None:
            return ""
        canonical = yaml.dump(raw_data, sort_keys=True, default_flow_style=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception as exc:  # noqa: BLE001
        logger.warning("RoleLoader: failed to hash rubric at %s: %s", rubric_path, exc)
        return ""


def _deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict that is *base* deep-merged with *override*.

    Rules:
    - If both values are dicts: recurse.
    - Otherwise: *override* wins.
    - Keys present only in *base* are preserved.
    - Keys present only in *override* are added.
    """
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


class RoleLoader:
    """File-system role definition loader with base merge and hot-reload.

    Args:
        roles_root: Path to the ``roles/`` directory.  Defaults to
            ``"roles"`` relative to the current working directory.
        role_name: The role name to load (e.g. ``"coding_agent"``).
            Must correspond to a subdirectory under *roles_root*.
        poll_interval_s: Polling interval in seconds for file-change
            detection when watchdog is unavailable.  Defaults to 60.
    """

    def __init__(
        self,
        roles_root: str | Path = "roles",
        role_name: str = "",
        poll_interval_s: int = 60,
    ) -> None:
        self._root = Path(roles_root)
        self._role_name = role_name
        self._poll_interval_s = poll_interval_s
        self._cached: Optional[RoleDefinitionConfig] = None
        self._cached_mtime: float = 0.0
        self._reload_callbacks: list[Callable[[RoleDefinitionConfig], None]] = []
        self._watch_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if ``roles/{role_name}/role.yaml`` exists."""
        return self._role_yaml_path().exists()

    def load(self) -> Optional[RoleDefinitionConfig]:
        """Load and return the merged role definition, or None if not available.

        On each call the file modification time is compared to the cached value.
        If the file has changed, it is re-read and the cache is updated.

        Returns:
            :class:`~acc.config.RoleDefinitionConfig` or ``None`` if the role
            directory / file does not exist.
        """
        role_path = self._role_yaml_path()
        if not role_path.exists():
            logger.debug("RoleLoader: %s not found — skipping tier-0", role_path)
            return None

        try:
            mtime = role_path.stat().st_mtime
        except OSError:
            return None

        if self._cached is not None and mtime == self._cached_mtime:
            return self._cached

        role_def = self._load_and_merge(role_path)
        if role_def is not None:
            self._cached = role_def
            self._cached_mtime = mtime
        return role_def

    def register_reload_callback(
        self,
        callback: Callable[[RoleDefinitionConfig], None],
    ) -> None:
        """Register a callback to be invoked when a hot-reload occurs.

        The callback receives the new :class:`~acc.config.RoleDefinitionConfig`
        as its only argument.  Multiple callbacks may be registered; they are
        called in registration order.

        Args:
            callback: Callable accepting a single ``RoleDefinitionConfig`` arg.
        """
        self._reload_callbacks.append(callback)

    async def start_watch(self) -> None:
        """Start the async polling watcher for hot-reload.

        Creates an asyncio Task that polls ``roles/{role_name}/role.yaml``
        every ``poll_interval_s`` seconds.  If the file changes and the new
        version differs from the cached version, registered callbacks are
        invoked.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._watch_task is not None:
            return
        self._watch_task = asyncio.create_task(
            self._poll_loop(), name=f"role-loader-watch-{self._role_name}"
        )
        logger.info(
            "RoleLoader: started hot-reload watcher for '%s' (interval=%ds)",
            self._role_name,
            self._poll_interval_s,
        )

    async def stop_watch(self) -> None:
        """Cancel the hot-reload watcher task if running."""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _role_yaml_path(self) -> Path:
        return self._root / self._role_name / "role.yaml"

    def _base_yaml_path(self) -> Path:
        return self._root / _BASE_ROLE_NAME / "role.yaml"

    def _load_and_merge(
        self, role_path: Path
    ) -> Optional[RoleDefinitionConfig]:
        """Read base + role YAML, deep-merge, and validate as RoleDefinitionConfig."""
        base_data: dict[str, Any] = {}
        base_path = self._base_yaml_path()
        if base_path.exists():
            try:
                with base_path.open() as fh:
                    raw = yaml.safe_load(fh) or {}
                base_data = raw.get("role_definition", {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("RoleLoader: failed to load base role.yaml: %s", exc)

        try:
            with role_path.open() as fh:
                raw = yaml.safe_load(fh) or {}
            role_data = raw.get("role_definition", {})
        except Exception as exc:  # noqa: BLE001
            logger.error("RoleLoader: failed to load %s: %s", role_path, exc)
            return None

        merged = _deep_merge(base_data, role_data)

        try:
            role_def = RoleDefinitionConfig.model_validate(merged)
        except ValidationError as exc:
            logger.error(
                "RoleLoader: validation failed for '%s': %s", self._role_name, exc
            )
            return None

        # ACC-11: compute eval_rubric_hash from the canonical rubric YAML.
        # The rubric file is located next to role.yaml in the role directory.
        if not role_def.eval_rubric_hash:
            rubric_ref = role_def.eval_rubric_ref if hasattr(role_def, "eval_rubric_ref") else "eval_rubric.yaml"
            rubric_path = role_path.parent / rubric_ref
            role_def.eval_rubric_hash = _compute_rubric_hash(rubric_path)

        return role_def

    async def _poll_loop(self) -> None:
        """Async polling loop — checks mtime every poll_interval_s seconds."""
        while True:
            await asyncio.sleep(self._poll_interval_s)
            role_path = self._role_yaml_path()
            if not role_path.exists():
                continue
            try:
                mtime = role_path.stat().st_mtime
            except OSError:
                continue
            if mtime == self._cached_mtime:
                continue

            prev_version = self._cached.version if self._cached else ""
            new_def = self._load_and_merge(role_path)
            if new_def is None:
                continue

            if new_def.version == prev_version:
                # File touched but version unchanged — update cache silently
                self._cached = new_def
                self._cached_mtime = mtime
                continue

            logger.info(
                "RoleLoader: hot-reload '%s' %s → %s",
                self._role_name,
                prev_version,
                new_def.version,
            )
            self._cached = new_def
            self._cached_mtime = mtime
            for cb in self._reload_callbacks:
                try:
                    cb(new_def)
                except Exception as exc:  # noqa: BLE001
                    logger.error("RoleLoader: reload callback error: %s", exc)
