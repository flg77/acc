"""Unit tests for the diagnostic echo MCP server.

The server lives at ``container/production/echo_mcp_server/main.py``
because it ships in its own (tiny) container image — but the
JSON-RPC dispatcher is a pure function and trivially testable here
without spinning up the HTTPServer.  We import the module directly
via importlib so the test suite doesn't need to add a sys.path entry
for the container/ tree.

Coverage:
* ``initialize`` returns the canonical handshake response with the
  expected protocolVersion + serverInfo fields.
* ``tools/list`` advertises exactly one tool named ``echo`` with a
  valid JSON Schema for its input.
* ``tools/call`` with ``name=echo`` round-trips the ``arguments.text``
  field in MCP's canonical content envelope.
* Unknown tool names yield a JSON-RPC error with a recognisable code.
* Wrong argument types yield a parameter error.
* Unknown methods yield -32601 method-not-found.
* Every response carries the same ``id`` as the request envelope so
  the ACC client's id-mismatch check (acc/mcp/client.py) is satisfied.

We also exercise the round-trip against the actual ``acc.mcp.client``
client + transport so this test catches breakage on either side of
the wire.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Module loading — server lives outside the importable acc/ package
# ---------------------------------------------------------------------------


_SERVER_PATH = (
    Path(__file__).resolve().parent.parent
    / "container" / "production" / "echo_mcp_server" / "main.py"
)


@pytest.fixture(scope="module")
def echo_module():
    """Load ``echo_mcp_server/main.py`` as a module without polluting
    ``sys.path``.  Module-scoped so the load runs once per test
    session — the module is stateless so caching is safe."""
    spec = importlib.util.spec_from_file_location(
        "echo_mcp_server_main", _SERVER_PATH,
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load {_SERVER_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# JSON-RPC dispatch — pure-function tests
# ---------------------------------------------------------------------------


def test_initialize_returns_protocol_version_and_server_info(echo_module):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
        },
    }
    response = echo_module.handle_jsonrpc(request)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    server_info = result["serverInfo"]
    assert server_info["name"] == "acc-mcp-echo"
    assert server_info["version"]
    # Capabilities present (even if empty dict) so clients can read it
    # without hitting a KeyError.
    assert "capabilities" in result


def test_tools_list_advertises_echo(echo_module):
    response = echo_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {},
    })
    assert response["id"] == 7
    tools = response["result"]["tools"]
    assert len(tools) == 1
    echo_tool = tools[0]
    assert echo_tool["name"] == "echo"
    # Valid JSON Schema fragment with a required text field.
    schema = echo_tool["inputSchema"]
    assert schema["type"] == "object"
    assert "text" in schema["required"]


def test_tools_call_echo_round_trips_text(echo_module):
    response = echo_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 42, "method": "tools/call",
        "params": {"name": "echo", "arguments": {"text": "hello world"}},
    })
    assert response["id"] == 42
    assert "error" not in response
    content = response["result"]["content"]
    # Canonical MCP content envelope: list of {type, text} items.
    assert content == [{"type": "text", "text": "hello world"}]


def test_tools_call_unknown_tool_returns_error(echo_module):
    response = echo_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "shell.exec", "arguments": {}},
    })
    assert response["id"] == 99
    assert "error" in response
    assert "shell.exec" in response["error"]["message"]


def test_tools_call_wrong_arg_type_returns_param_error(echo_module):
    response = echo_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "echo", "arguments": {"text": 12345}},
    })
    assert "error" in response
    assert response["error"]["code"] == -32602
    assert "text" in response["error"]["message"]


def test_unknown_method_returns_method_not_found(echo_module):
    response = echo_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {},
    })
    assert response["error"]["code"] == -32601
    assert "method not found" in response["error"]["message"].lower()


def test_response_id_always_matches_request_id(echo_module):
    """ACC client (acc/mcp/client.py:_rpc) raises MCPProtocolError on
    id mismatch — the server MUST echo the id verbatim across every
    method, including the empty-string and integer cases."""
    for rid in (1, 99999, "client-uuid-abc", "", None):
        response = echo_module.handle_jsonrpc({
            "jsonrpc": "2.0", "id": rid,
            "method": "initialize", "params": {},
        })
        assert response["id"] == rid


# ---------------------------------------------------------------------------
# End-to-end via acc.mcp.client — drives the real HTTPTransport
# against a server thread on localhost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_through_acc_mcp_client_against_live_server(
    echo_module, monkeypatch,
):
    """Spin up the real HTTPServer in a background thread + drive it
    via :class:`acc.mcp.client.MCPClient`.  Confirms wire-format
    compatibility on both sides — server response shape passes the
    client's JSON-RPC envelope validation.

    Uses an ephemeral port (0) so concurrent test runs don't clash.
    """
    import threading
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), echo_module._EchoMCPHandler)
    port = server.server_port

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        from acc.mcp.client import MCPClient
        from acc.mcp.manifest import MCPManifest

        manifest = MCPManifest(
            server_id="echo_test",
            purpose="round-trip test",
            transport="http",
            url=f"http://127.0.0.1:{port}/rpc",
            timeout_s=5,
            allowed_tools=["echo"],
        )
        client = MCPClient(manifest)
        try:
            await client.initialize()
            tools = await client.list_tools()
            assert [t["name"] for t in tools] == ["echo"]

            result = await client.call_tool("echo", {"text": "round trip"})
            # MCP convention: tool result has .content list of {type,text}.
            assert result["content"][0]["text"] == "round trip"
        finally:
            await client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
