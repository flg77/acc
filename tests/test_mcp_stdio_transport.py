"""Unit tests for :class:`acc.mcp.transports.StdioTransport`.

We exercise the transport against a real subprocess — a tiny Python
one-liner that reads JSON-RPC requests from stdin and emits responses
on stdout.  This catches wire-format regressions that mocks would
silently paper over (newline framing, stdin/stdout pipe lifecycle,
EOF-on-stdout handling, subprocess termination).

The :class:`MCPClient` integration is also covered end-to-end here:
``initialize`` + ``list_tools`` + ``call_tool`` against the same
script.

Stderr drain + timeout paths use mock subprocesses where end-to-end
fidelity isn't worth the test-time cost.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

from acc.mcp.client import MCPClient
from acc.mcp.errors import (
    MCPConnectionError,
    MCPProtocolError,
    MCPTransportError,
)
from acc.mcp.manifest import MCPManifest
from acc.mcp.transports import StdioTransport, build_transport


# ---------------------------------------------------------------------------
# Fixtures — tiny in-process Python MCP servers
# ---------------------------------------------------------------------------


# A minimal JSON-RPC 2.0 echo server.  Each request gets a response
# whose shape depends on the method:
#   - initialize          → protocolVersion + serverInfo
#   - tools/list          → one tool named "echo"
#   - tools/call name=echo → {"echoed": <args>}
#   - tools/call name=err  → error object (server-side error)
#   - anything else        → error -32601 method not found
_FAKE_SERVER_PY = textwrap.dedent(
    r"""
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "fake-stdio", "version": "0.0.1"},
            }}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {
                "tools": [{"name": "echo", "description": "round-trip"}],
            }}
        elif method == "tools/call":
            tname = params.get("name")
            args = params.get("arguments", {})
            if tname == "echo":
                resp = {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": args.get("text", "")}],
                }}
            elif tname == "err":
                resp = {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": 42, "message": "synthetic server error",
                }}
            else:
                resp = {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32602, "message": f"unknown tool {tname!r}",
                }}
        else:
            resp = {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": "method not found",
            }}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    """
).strip()


# A "crash on first request" server — exits immediately after receiving
# anything on stdin.  Drives the EOF-on-stdout error path.
_CRASH_SERVER_PY = textwrap.dedent(
    r"""
    import sys
    sys.stdin.readline()
    sys.exit(1)
    """
).strip()


# A server that emits malformed JSON on stdout (drives MCPProtocolError).
_GARBAGE_SERVER_PY = textwrap.dedent(
    r"""
    import sys
    sys.stdin.readline()
    sys.stdout.write("not json at all\n")
    sys.stdout.flush()
    """
).strip()


def _stdio_manifest(*, command: list[str], server_id: str = "stdio_test") -> MCPManifest:
    return MCPManifest(
        server_id=server_id,
        purpose="stdio test fixture",
        transport="stdio",
        command=command,
        timeout_s=5,
    )


# ---------------------------------------------------------------------------
# build_transport dispatch
# ---------------------------------------------------------------------------


def test_build_transport_dispatches_by_manifest_kind():
    http_m = MCPManifest(
        server_id="h", purpose="x", transport="http",
        url="http://localhost:9999/rpc",
    )
    stdio_m = _stdio_manifest(command=[sys.executable, "-c", "import sys"])
    from acc.mcp.transports import HTTPTransport, StdioTransport
    assert isinstance(build_transport(http_m), HTTPTransport)
    assert isinstance(build_transport(stdio_m), StdioTransport)


# ---------------------------------------------------------------------------
# Real-subprocess end-to-end via MCPClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_full_lifecycle_against_fake_server():
    """initialize → list_tools → call_tool round-trip against a real subprocess."""
    manifest = _stdio_manifest(command=[sys.executable, "-c", _FAKE_SERVER_PY])
    client = MCPClient(manifest)
    try:
        await client.initialize()
        tools = await client.list_tools()
        assert [t["name"] for t in tools] == ["echo"]

        result = await client.call_tool("echo", {"text": "hello stdio"})
        # MCP servers conventionally wrap call results in {"content": [...]}
        assert result["content"][0]["text"] == "hello stdio"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_server_error_surfaces_as_protocol_error():
    """JSON-RPC error response → MCPProtocolError with the server's message."""
    manifest = _stdio_manifest(command=[sys.executable, "-c", _FAKE_SERVER_PY])
    client = MCPClient(manifest)
    # tell the manifest's tool gate to allow the "err" tool too
    manifest.allowed_tools = []
    try:
        await client.initialize()
        with pytest.raises(MCPProtocolError, match="synthetic server error"):
            await client.call_tool("err")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_subprocess_crash_surfaces_as_transport_error():
    """Server exits after one request → MCPTransportError on next call."""
    manifest = _stdio_manifest(command=[sys.executable, "-c", _CRASH_SERVER_PY])
    client = MCPClient(manifest)
    # initialize sends the handshake → crash server reads + exits → next read
    # returns EOF.  Depending on timing the EOF may surface from initialize
    # OR from the subsequent call; both are MCPConnectionError-wrapped.
    with pytest.raises((MCPConnectionError, MCPTransportError)):
        await client.initialize()
    await client.close()


@pytest.mark.asyncio
async def test_stdio_garbage_response_surfaces_as_protocol_error():
    """Server emits non-JSON on stdout → MCPProtocolError-wrapped."""
    manifest = _stdio_manifest(command=[sys.executable, "-c", _GARBAGE_SERVER_PY])
    client = MCPClient(manifest)
    with pytest.raises(MCPConnectionError):
        # MCPClient.initialize wraps protocol errors as connection errors
        # (the handshake is what failed) — we just want to assert it
        # doesn't crash with an unhelpful traceback.
        await client.initialize()
    await client.close()


@pytest.mark.asyncio
async def test_stdio_command_not_found_raises_connection_error():
    """Spawning a non-existent binary → MCPConnectionError, not crash."""
    manifest = _stdio_manifest(
        command=["/does/not/exist/never/will-acc-mcp-test"],
        server_id="missing",
    )
    client = MCPClient(manifest)
    with pytest.raises(MCPConnectionError, match="command not found|spawn"):
        await client.initialize()


@pytest.mark.asyncio
async def test_stdio_close_is_idempotent():
    """close() called twice → no error, no second subprocess.wait()."""
    manifest = _stdio_manifest(command=[sys.executable, "-c", _FAKE_SERVER_PY])
    client = MCPClient(manifest)
    await client.initialize()
    await client.close()
    await client.close()  # must not raise


# ---------------------------------------------------------------------------
# Timeout — drive via low timeout_s + slow-ish server
# ---------------------------------------------------------------------------


_SLOW_SERVER_PY = textwrap.dedent(
    r"""
    import json, sys, time
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        # Eat the request, sleep, then never reply.
        time.sleep(5)
    """
).strip()


@pytest.mark.asyncio
async def test_stdio_response_timeout_raises_transport_error():
    """Server never replies → MCPTransportError 'timeout reading response'."""
    manifest = MCPManifest(
        server_id="slow",
        purpose="timeout fixture",
        transport="stdio",
        command=[sys.executable, "-c", _SLOW_SERVER_PY],
        timeout_s=1,  # tight cap; server sleeps 5s
    )
    transport = StdioTransport(manifest)
    await transport.start()
    try:
        with pytest.raises(MCPTransportError, match="timeout"):
            await transport.send_rpc({
                "jsonrpc": "2.0", "id": 1, "method": "ping", "params": {},
            })
    finally:
        await transport.close()


# ---------------------------------------------------------------------------
# Concurrency — two send_rpc calls share one pipe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_concurrent_rpcs_serialise_via_lock():
    """Two parallel send_rpc calls produce two well-formed responses.

    Confirms the asyncio.Lock keeps requests + responses paired
    correctly when two callers race on the same transport.
    """
    manifest = _stdio_manifest(command=[sys.executable, "-c", _FAKE_SERVER_PY])
    transport = StdioTransport(manifest)
    await transport.start()
    try:
        envelope_a = {
            "jsonrpc": "2.0", "id": 100, "method": "initialize", "params": {},
        }
        envelope_b = {
            "jsonrpc": "2.0", "id": 200, "method": "tools/list", "params": {},
        }
        # gather forces both calls to race for the lock.
        result_a, result_b = await asyncio.gather(
            transport.send_rpc(envelope_a),
            transport.send_rpc(envelope_b),
        )
        # Each response carries the matching id.  If the lock failed we'd
        # see crossed responses (id 100 with tools, id 200 without).
        assert result_a["id"] == 100
        assert "serverInfo" in result_a["result"]
        assert result_b["id"] == 200
        assert result_b["result"]["tools"][0]["name"] == "echo"
    finally:
        await transport.close()


# ---------------------------------------------------------------------------
# Manifest validator — stdio still rejects empty command
# ---------------------------------------------------------------------------


def test_stdio_manifest_requires_command():
    """Validator rule from PR 4.2 must still fire — empty command rejected."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MCPManifest(
            server_id="bad", purpose="x", transport="stdio", command=[],
        )
