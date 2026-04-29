"""ACC MCP — Model Context Protocol client surface (Phase 4.2).

An **MCP server** is an external process that exposes tools, resources,
and prompts to an LLM via the Model Context Protocol (Anthropic's open
spec).  ACC agents talk to MCP servers as clients: a role declares
which servers it may consume via ``role_definition.allowed_mcps``, the
LLM emits a tool-call request, and the agent's CognitiveCore (Phase
4.3) routes the call through :class:`MCPRegistry` to the correct
server.

Biological framing: where Skills are *organelles* the cell builds
in-house, MCP servers are *symbiotic bacteria* the cell hosts but does
not synthesise.  Each one is a separate process with its own
governance contract; the ``allowed_mcps`` whitelist is the cell wall
that decides which symbionts are admitted.

Transports supported in this PR:

* ``http``  — JSON-RPC 2.0 over HTTP POST (production path; httpx
  already ships in core deps).
* ``stdio`` — subprocess pipe (reserved for a future PR; the manifest
  enum accepts the value, the client raises ``NotImplementedError`` if
  asked to use it).

Public API::

    from acc.mcp import MCPRegistry, MCPManifest, MCPClient

    reg = MCPRegistry()
    reg.load_from("mcps")                 # reads $ACC_MCPS_ROOT or ./mcps
    client = await reg.client("echo_server")
    tools = await client.list_tools()
    result = await client.call_tool("echo", {"text": "ping"})
"""

from __future__ import annotations

from acc.mcp.client import MCPClient
from acc.mcp.errors import (
    MCPConnectionError,
    MCPError,
    MCPProtocolError,
    MCPServerNotFoundError,
    MCPToolNotFoundError,
    MCPTransportError,
)
from acc.mcp.manifest import MCPManifest, MCPTransport
from acc.mcp.registry import MCPRegistry

__all__ = [
    "MCPClient",
    "MCPConnectionError",
    "MCPError",
    "MCPManifest",
    "MCPProtocolError",
    "MCPRegistry",
    "MCPServerNotFoundError",
    "MCPToolNotFoundError",
    "MCPTransport",
    "MCPTransportError",
]
