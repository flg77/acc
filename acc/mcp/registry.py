"""Filesystem-discovered registry of MCP server manifests.

Mirrors :class:`acc.skills.SkillRegistry` semantics so contributors
only learn one discovery pattern:

* ``mcps/<server_id>/mcp.yaml`` defines one server.
* ``mcps/_base/mcp.yaml`` provides defaults that deep-merge into every
  child manifest (lists replaced wholesale, dicts merged key-by-key).
* ``_base``, ``TEMPLATE``, and ``__pycache__`` directory names are
  excluded from discovery.

The registry caches one :class:`MCPClient` per ``server_id``; clients
are constructed lazily on the first :meth:`MCPRegistry.client` call.
Call :meth:`MCPRegistry.close_all` during agent shutdown to release
HTTP connections cleanly.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from acc.mcp.client import MCPClient
from acc.mcp.errors import (
    MCPManifestError,
    MCPServerNotFoundError,
)
from acc.mcp.manifest import MCPManifest

logger = logging.getLogger("acc.mcp.registry")

_BASE_DIR_NAME = "_base"
_EXCLUDED_DIR_NAMES = {"_base", "TEMPLATE", "__pycache__"}


def _mcps_root_default() -> str:
    """Resolve the ``mcps/`` directory.

    Order: ``ACC_MCPS_ROOT`` env var → literal ``"mcps"`` (relative to
    the cwd).  Same convention as :func:`acc.skills.registry._skills_root_default`.
    """
    return os.environ.get("ACC_MCPS_ROOT", "mcps")


def list_mcp_server_ids(base_dir: str | Path = "mcps") -> list[str]:
    """Return alphabetically sorted server ids under *base_dir*."""
    root = Path(base_dir)
    if not root.is_dir():
        logger.debug("list_mcp_server_ids: directory not found: %s", root)
        return []

    names: list[str] = []
    for candidate in root.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name in _EXCLUDED_DIR_NAMES:
            continue
        if not (candidate / "mcp.yaml").is_file():
            continue
        names.append(candidate.name)
    return sorted(names)


def _deep_merge(base: dict, override: dict) -> dict:
    """Same merge semantics as the role and skills loaders."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_manifest(base_dir: Path, server_id: str) -> MCPManifest:
    """Read + deep-merge + validate one server's mcp.yaml."""
    manifest_path = base_dir / server_id / "mcp.yaml"
    if not manifest_path.is_file():
        raise MCPManifestError(
            f"server {server_id!r}: mcp.yaml not found at {manifest_path}"
        )

    base_data: dict[str, Any] = {}
    base_path = base_dir / _BASE_DIR_NAME / "mcp.yaml"
    if base_path.is_file():
        try:
            base_data = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise MCPManifestError(
                f"_base/mcp.yaml is not valid YAML: {exc}"
            ) from exc

    try:
        child_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise MCPManifestError(
            f"server {server_id!r}: mcp.yaml is not valid YAML: {exc}"
        ) from exc

    merged = _deep_merge(base_data, child_data)
    merged.setdefault("server_id", server_id)

    try:
        return MCPManifest(**merged)
    except ValidationError as exc:
        raise MCPManifestError(
            f"server {server_id!r}: manifest validation failed: {exc}"
        ) from exc


class MCPRegistry:
    """In-process catalogue of MCP server manifests + lazily-built clients.

    Construction is cheap; populate via :meth:`load_from`.  Calling
    ``load_from`` again replaces every entry — useful for hot-reload
    on SIGHUP in long-running agents.
    """

    def __init__(self) -> None:
        self._manifests: dict[str, MCPManifest] = {}
        self._clients: dict[str, MCPClient] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from(self, base_dir: str | Path | None = None) -> int:
        """Discover every ``mcps/<id>/mcp.yaml`` under *base_dir*.

        Returns:
            Count of manifests successfully validated.  Failures are
            logged at WARNING and dropped so one bad manifest does not
            knock out the rest.
        """
        root = Path(base_dir) if base_dir is not None else Path(_mcps_root_default())
        ids = list_mcp_server_ids(root)
        manifests: dict[str, MCPManifest] = {}
        for server_id in ids:
            try:
                manifests[server_id] = _load_manifest(root, server_id)
            except MCPManifestError as exc:
                logger.warning("mcp_registry: %s", exc)
            except Exception:
                logger.exception(
                    "mcp_registry: unexpected failure loading %r", server_id
                )

        # Replace state, but keep any cached client whose manifest is
        # unchanged so HTTP connections persist across hot-reloads.
        old_clients = self._clients
        self._manifests = manifests
        self._clients = {
            sid: client
            for sid, client in old_clients.items()
            if sid in manifests and manifests[sid] == client.manifest
        }
        # Clients whose manifest changed (or was removed) need closing.
        stale = [sid for sid in old_clients if sid not in self._clients]
        # Closing is async — we cannot await here.  Stash the stale
        # clients on the registry so an external coroutine can drain
        # them via close_stale() if it cares.  For most call sites the
        # garbage collector + httpx finaliser is sufficient; logged
        # for visibility.
        if stale:
            logger.info(
                "mcp_registry: %d cached client(s) released after reload (%s)",
                len(stale), ", ".join(stale),
            )

        logger.info(
            "mcp_registry: loaded %d server(s) from %s (%s)",
            len(manifests), root, ", ".join(sorted(manifests.keys())) or "<empty>",
        )
        return len(manifests)

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    def list_server_ids(self) -> list[str]:
        return sorted(self._manifests.keys())

    def manifest(self, server_id: str) -> MCPManifest | None:
        return self._manifests.get(server_id)

    def manifests(self) -> dict[str, MCPManifest]:
        """Snapshot of every loaded manifest — safe to mutate the
        returned dict; the registry's view is unaffected."""
        return dict(self._manifests)

    def __contains__(self, server_id: str) -> bool:
        return server_id in self._manifests

    def __len__(self) -> int:
        return len(self._manifests)

    # ------------------------------------------------------------------
    # Client cache
    # ------------------------------------------------------------------

    async def client(self, server_id: str) -> MCPClient:
        """Return a connected :class:`MCPClient` for *server_id*.

        Lazily constructs and initialises the client on first call;
        cached for subsequent calls so HTTP connections are reused.
        """
        manifest = self._manifests.get(server_id)
        if manifest is None:
            raise MCPServerNotFoundError(
                f"server {server_id!r} not in registry "
                f"(loaded: {sorted(self._manifests.keys())})"
            )
        client = self._clients.get(server_id)
        if client is None:
            client = MCPClient(manifest)
            self._clients[server_id] = client
        await client.initialize()  # idempotent
        return client

    async def close_all(self) -> None:
        """Close every cached client.  Call during agent shutdown."""
        for server_id, client in list(self._clients.items()):
            try:
                await client.close()
            except Exception:
                logger.exception("mcp_registry: close failed for %r", server_id)
        self._clients.clear()
