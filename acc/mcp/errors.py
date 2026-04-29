"""MCP error hierarchy.

Mirrors the structure of :mod:`acc.skills.skill_runtime` errors so
contributors who learnt the Skills error tree have nothing new to
absorb here.

Every error inherits from :class:`MCPError` so calling code can catch
"any MCP failure" with one ``except`` clause when finer detail is not
needed.
"""

from __future__ import annotations


class MCPError(Exception):
    """Base class for every error raised by the MCP package."""


class MCPManifestError(MCPError):
    """An ``mcps/<id>/mcp.yaml`` failed pydantic validation, or
    referenced an unsupported transport."""


class MCPServerNotFoundError(MCPError):
    """The registry has no server with the requested ``server_id``."""


class MCPToolNotFoundError(MCPError):
    """The connected MCP server did not advertise the requested tool
    in its ``tools/list`` response."""


class MCPConnectionError(MCPError):
    """The transport could not establish a working session — the server
    is unreachable, the URL is wrong, or the initialise handshake
    failed."""


class MCPProtocolError(MCPError):
    """The server returned a JSON-RPC error response, or sent a
    payload that does not match the protocol envelope (missing
    ``jsonrpc`` field, mismatched ``id``, etc.)."""


class MCPTransportError(MCPError):
    """Catch-all for transport-level failures (HTTP 5xx, socket
    timeouts, malformed bytes).  Distinct from
    :class:`MCPProtocolError` which is raised when the bytes were
    well-formed but the server's logical response was not."""
