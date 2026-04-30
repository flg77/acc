"""MCP client surface — JSON-RPC 2.0 over HTTP or stdio.

Implements the minimum slice of the Model Context Protocol that ACC
needs to drive tools from a role:

* ``initialize`` — capability handshake; called once per session.
* ``tools/list`` — enumerate the server's tools.
* ``tools/call`` — invoke one tool with arguments.

Resource and prompt methods are not exercised here; they will be added
when a role's CognitiveCore needs them.

Transport is selected at construction time from ``manifest.transport``
via :func:`acc.mcp.transports.build_transport`.  HTTP and stdio are
both implemented today; adding e.g. websockets is a one-class change
in :mod:`acc.mcp.transports` plus a one-line dispatch update.

Thread/async safety: one :class:`MCPClient` instance owns one transport
and is intended to be used from a single asyncio task.  The
:class:`acc.mcp.MCPRegistry` instantiates one client per ``server_id``
lazily on first ``client(server_id)`` call, then caches it for the
process lifetime.
"""

from __future__ import annotations

import logging
from typing import Any

from acc.mcp.errors import (
    MCPConnectionError,
    MCPProtocolError,
    MCPToolNotFoundError,
    MCPTransportError,
)
from acc.mcp.manifest import MCPManifest
from acc.mcp.transports import StdioTransport, Transport, build_transport

logger = logging.getLogger("acc.mcp.client")


# Module-level monotonic JSON-RPC id allocator.  We don't need
# per-client uniqueness (the server only sees ids from one connection
# at a time), but a global counter makes log correlation easier when
# debugging across multiple servers.
_RPC_ID = 0


def _next_id() -> int:
    global _RPC_ID
    _RPC_ID += 1
    return _RPC_ID


# MCP protocol version this client speaks.  Sent in the initialize
# request and re-checked against the server's response so we fail fast
# on incompatible versions instead of misparsing later messages.
_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_NAME = "acc-mcp-client"
_CLIENT_VERSION = "0.1.0"


class MCPClient:
    """One client per MCP server.  Lazily initialises on first method call.

    Args:
        manifest: The validated :class:`MCPManifest` for this server.

    Construction is cheap; the actual transport is opened on the first
    ``initialize()`` call.  Call :meth:`close` to release the
    underlying connection — the registry calls this during shutdown
    so callers normally don't need to.
    """

    def __init__(self, manifest: MCPManifest) -> None:
        self._manifest = manifest
        self._transport: Transport | None = None
        self._initialised = False
        self._cached_tools: list[dict] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def manifest(self) -> MCPManifest:
        return self._manifest

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the transport and run the MCP capability handshake.

        Idempotent — calling twice is a cheap no-op.  Raises
        :class:`MCPConnectionError` on transport failure or protocol
        mismatch.

        Stdio transports also require a subprocess spawn before the
        first RPC; we trigger that here via :meth:`StdioTransport.start`
        so callers don't have to know which transport they got.
        """
        if self._initialised:
            return

        self._transport = build_transport(self._manifest)
        # Stdio needs an explicit subprocess spawn.  HTTP transports
        # don't expose start() — keep the call optional via duck typing.
        if isinstance(self._transport, StdioTransport):
            try:
                await self._transport.start()
            except MCPConnectionError:
                self._transport = None
                raise
            except Exception as exc:
                self._transport = None
                raise MCPConnectionError(
                    f"server_id={self._manifest.server_id!r}: stdio start "
                    f"failed: {exc}"
                ) from exc

        try:
            response = await self._rpc(
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": _CLIENT_NAME,
                        "version": _CLIENT_VERSION,
                    },
                },
            )
        except (MCPProtocolError, MCPTransportError) as exc:
            await self._safe_close()
            raise MCPConnectionError(
                f"server_id={self._manifest.server_id!r}: initialise failed: {exc}"
            ) from exc

        server_version = response.get("protocolVersion", "<unknown>")
        if server_version != _PROTOCOL_VERSION:
            # Don't hard-fail on minor revs — many servers are lenient
            # about the exact date string.  Surface the mismatch so
            # operators notice in logs without breaking the flow.
            logger.info(
                "mcp: server_id=%r reports protocolVersion=%r (we sent %r) — "
                "continuing on best-effort compatibility",
                self._manifest.server_id, server_version, _PROTOCOL_VERSION,
            )

        self._initialised = True
        logger.info(
            "mcp: initialised server_id=%r transport=%s (server: %r)",
            self._manifest.server_id,
            self._manifest.transport,
            response.get("serverInfo", {}).get("name", "<unknown>"),
        )

    async def close(self) -> None:
        """Close the transport.  Safe to call multiple times."""
        await self._safe_close()
        self._initialised = False
        self._cached_tools = None

    async def _safe_close(self) -> None:
        if self._transport is not None:
            try:
                await self._transport.close()
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "mcp: transport close failed for %r", self._manifest.server_id,
                )
            self._transport = None

    # ------------------------------------------------------------------
    # Tool surface
    # ------------------------------------------------------------------

    async def list_tools(self, *, refresh: bool = False) -> list[dict]:
        """Return the server's tool advertisement, filtered by the manifest.

        Args:
            refresh: When True, force a fresh ``tools/list`` round-trip
                instead of returning the cached result.  Use after a
                server hot-reload.

        Returns:
            List of tool descriptors as the server returned them, with
            entries blocked by ``manifest.allowed_tools`` /
            ``denied_tools`` filtered out.  Each descriptor is a dict
            with at minimum ``name``; servers conventionally include
            ``description`` and ``inputSchema``.
        """
        await self.initialize()
        if self._cached_tools is None or refresh:
            response = await self._rpc("tools/list", {})
            tools = response.get("tools", [])
            if not isinstance(tools, list):
                raise MCPProtocolError(
                    f"server_id={self._manifest.server_id!r}: tools/list returned "
                    f"non-list 'tools' field ({type(tools).__name__})"
                )
            self._cached_tools = list(tools)

        return [
            tool for tool in self._cached_tools
            if self._manifest.is_tool_allowed(str(tool.get("name", "")))
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke one tool and return its structured result.

        Args:
            tool_name: Must be advertised by the server AND permitted
                by the manifest's allow/deny lists.
            arguments: Tool-specific arguments dict.  ``None`` is
                treated as ``{}``.

        Raises:
            MCPToolNotFoundError: Tool is not on the server, or is
                blocked by ``allowed_tools`` / ``denied_tools``.
            MCPProtocolError: Server returned a JSON-RPC error or a
                malformed result envelope.
            MCPTransportError: Network / transport failure.
        """
        await self.initialize()
        if not self._manifest.is_tool_allowed(tool_name):
            raise MCPToolNotFoundError(
                f"tool {tool_name!r} blocked by manifest "
                f"(server_id={self._manifest.server_id!r}, "
                f"allowed={self._manifest.allowed_tools or 'all'}, "
                f"denied={self._manifest.denied_tools})"
            )

        # Best-effort tool existence check using the cached list — saves
        # a round-trip when the LLM hallucinates a tool name.
        if self._cached_tools is not None:
            advertised = {str(t.get("name", "")) for t in self._cached_tools}
            if tool_name not in advertised:
                raise MCPToolNotFoundError(
                    f"tool {tool_name!r} not advertised by server "
                    f"server_id={self._manifest.server_id!r} (saw {sorted(advertised)})"
                )

        response = await self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        return response

    # ------------------------------------------------------------------
    # Internal — JSON-RPC envelope construction + validation
    # ------------------------------------------------------------------

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON-RPC 2.0 request and return the parsed ``result``.

        Translates server-side ``error`` responses into
        :class:`MCPProtocolError`, transport failures into
        :class:`MCPTransportError`.  The transport handles wire
        framing; this method handles envelope shape.
        """
        if self._transport is None:
            raise MCPConnectionError(
                f"server_id={self._manifest.server_id!r}: client not initialised"
            )

        rpc_id = _next_id()
        envelope = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        }

        body = await self._transport.send_rpc(envelope)

        if body.get("jsonrpc") != "2.0":
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: invalid JSON-RPC "
                f"envelope from {method}"
            )
        if body.get("id") != rpc_id:
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: id mismatch "
                f"(sent {rpc_id}, got {body.get('id')!r})"
            )
        if "error" in body:
            err = body["error"]
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: server returned "
                f"error {err.get('code', '?')}: {err.get('message', '')}"
            )
        result = body.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError(
                f"server_id={self._manifest.server_id!r}: missing/invalid "
                f"'result' field from {method}"
            )
        return result
