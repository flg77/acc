"""MC-01 -- the MCP HTTP transport.

ACC's ``http`` transport posts a bare JSON-RPC envelope and expects a JSON
body.  That is JSON-RPC over HTTP, and it is not what MCP servers speak:
measured on 2026-09-12 against midojo's fastmcp server, ACC's POST came back
``-32600 Missing session ID``, which is why the AS-04 run needed a shim.

These tests drive real httpx through a scripted transport, so the headers and
the body shapes are the ones that go on the wire.
"""

from __future__ import annotations

import json

import httpx
import pytest

from acc.mcp.errors import MCPProtocolError, MCPTransportError
from acc.mcp.manifest import MCPManifest
from acc.mcp.transports import (
    SESSION_ID_HEADER,
    HTTPTransport,
    StreamableHTTPTransport,
    build_transport,
)

NL = chr(10)


def _manifest(**kw) -> MCPManifest:
    base = dict(server_id="midojo_weather", purpose="Weather.",
                transport="streamable-http", url="http://127.0.0.1:8081/mcp")
    base.update(kw)
    return MCPManifest(**base)


def _transport(handler) -> StreamableHTTPTransport:
    t = StreamableHTTPTransport(_manifest())
    t._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8081/mcp",
        transport=httpx.MockTransport(handler),
        headers={"Accept": "application/json, text/event-stream"},
    )
    return t


def _json_response(payload: dict, *, session: str = "", status: int = 200):
    headers = {"content-type": "application/json"}
    if session:
        headers[SESSION_ID_HEADER] = session
    return httpx.Response(status, json=payload, headers=headers)


def _sse_response(payload: dict, *, session: str = "", preamble=None):
    frames = []
    for extra in (preamble or []):
        frames.append("event: message")
        frames.append("data: " + json.dumps(extra))
        frames.append("")
    frames.append("event: message")
    frames.append("data: " + json.dumps(payload))
    frames.append("")
    headers = {"content-type": "text/event-stream"}
    if session:
        headers[SESSION_ID_HEADER] = session
    return httpx.Response(200, text=NL.join(frames), headers=headers)


# ---------------------------------------------------------------------------
# the handshake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_session_id_is_captured_and_echoed():
    """The whole reason the plain http transport fails on a real MCP server."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"method": body.get("method"),
                     "session": request.headers.get(SESSION_ID_HEADER, "")})
        if body.get("method") == "initialize":
            return _json_response({"jsonrpc": "2.0", "id": 1, "result": {}},
                                  session="sess-abc")
        return _json_response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}})

    t = _transport(handler)
    await t.send_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert t.session_id == "sess-abc"
    await t.send_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert seen[0]["session"] == ""
    assert seen[-1]["method"] == "tools/list"
    assert seen[-1]["session"] == "sess-abc"


@pytest.mark.asyncio
async def test_the_initialized_notification_follows_a_successful_handshake():
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        methods.append(body.get("method"))
        if body.get("method") == "initialize":
            return _json_response({"jsonrpc": "2.0", "id": 1, "result": {}},
                                  session="s1")
        return httpx.Response(202, text="")

    t = _transport(handler)
    await t.send_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert methods == ["initialize", "notifications/initialized"]


@pytest.mark.asyncio
async def test_no_notification_when_the_handshake_failed():
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(json.loads(request.content).get("method"))
        return _json_response({"jsonrpc": "2.0", "id": 1,
                               "error": {"code": -32600, "message": "nope"}})

    t = _transport(handler)
    out = await t.send_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert "error" in out
    assert methods == ["initialize"]


@pytest.mark.asyncio
async def test_a_server_that_refuses_the_notification_keeps_the_session():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return _json_response({"jsonrpc": "2.0", "id": 1, "result": {}},
                                  session="s1")
        raise httpx.ConnectError("no")

    t = _transport(handler)
    out = await t.send_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert "result" in out and t.session_id == "s1"


# ---------------------------------------------------------------------------
# the two response shapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_json_body_is_read():
    def handler(request):
        return _json_response({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})

    out = await _transport(handler).send_rpc(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    assert out["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_an_sse_stream_is_read():
    def handler(request):
        return _sse_response({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})

    out = await _transport(handler).send_rpc(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call"})
    assert out["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_the_answer_is_taken_from_the_end_of_the_stream():
    """Progress notifications may precede the response; the answer ends it."""
    def handler(request):
        return _sse_response(
            {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}},
            preamble=[{"jsonrpc": "2.0", "method": "notifications/progress"}],
        )

    out = await _transport(handler).send_rpc(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call"})
    assert out["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_an_empty_stream_is_a_protocol_error():
    def handler(request):
        return httpx.Response(200, text="event: ping" + NL + NL,
                              headers={"content-type": "text/event-stream"})

    with pytest.raises(MCPProtocolError, match="no JSON-RPC message"):
        await _transport(handler).send_rpc(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})


# ---------------------------------------------------------------------------
# failures keep their existing shapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_server_error_is_a_transport_error():
    def handler(request):
        return httpx.Response(503, text="down")

    with pytest.raises(MCPTransportError, match="HTTP 503"):
        await _transport(handler).send_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


@pytest.mark.asyncio
async def test_a_timeout_is_a_transport_error():
    def handler(request):
        raise httpx.TimeoutException("slow")

    with pytest.raises(MCPTransportError, match="timeout"):
        await _transport(handler).send_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


@pytest.mark.asyncio
async def test_a_non_json_body_is_a_protocol_error():
    def handler(request):
        return httpx.Response(200, text="<html>nope</html>",
                              headers={"content-type": "text/html"})

    with pytest.raises(MCPProtocolError, match="non-JSON"):
        await _transport(handler).send_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def test_the_manifest_selects_the_transport():
    assert isinstance(build_transport(_manifest()), StreamableHTTPTransport)


def test_plain_http_is_untouched():
    """Plain JSON-RPC endpoints exist and ACC already talks to them."""
    m = _manifest(transport="http", url="http://acc-mcp-echo:8080/rpc")
    assert isinstance(build_transport(m), HTTPTransport)


def test_streamable_http_requires_a_url():
    with pytest.raises(ValueError, match="requires"):
        MCPManifest(server_id="s", purpose="p", transport="streamable-http")


@pytest.mark.asyncio
async def test_close_ends_the_session():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "DELETE":
            return httpx.Response(200, text="")
        return _json_response({"jsonrpc": "2.0", "id": 1, "result": {}}, session="s1")

    t = _transport(handler)
    await t.send_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    await t.close()
    assert "DELETE" in calls


@pytest.mark.asyncio
async def test_close_without_a_session_does_not_delete():
    calls = []

    def handler(request):
        calls.append(request.method)
        return _json_response({"jsonrpc": "2.0", "id": 1, "result": {}})

    t = _transport(handler)
    await t.close()
    assert "DELETE" not in calls


@pytest.mark.asyncio
async def test_a_server_mounted_at_mcp_is_not_lost_to_a_redirect():
    """Found on lighthouse: the handshake died on a 307.

    httpx normalises an empty path to a trailing slash, a fastmcp server
    mounted at /mcp answers /mcp/ with a 307, and httpx does not follow by
    default -- so initialise read the redirect body and failed with
    ``non-JSON response (HTTP 307)``.  We post the exact endpoint now.
    """
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return _json_response({"jsonrpc": "2.0", "id": 1, "result": {}},
                              session="s1")

    t = _transport(handler)
    await t.send_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert paths[0] == "/mcp", paths
