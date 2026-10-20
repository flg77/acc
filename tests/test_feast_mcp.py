"""The ``feast`` MCP manifest — Feast's feature server, read-only (backlog 019 A4).

Feast's feature server exposes its FastAPI routes as MCP tools (fastapi-mcp),
including writes (push, write_to_online_store, materialize). The manifest
allows reads only. These tests replay a session recorded against a real
``feast serve`` (feast 0.66.0, ``mcp_transport: http``) through ACC's real
client: the tool list it advertised, and an online-features read.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from acc.mcp import client as mcp_client
from acc.mcp.errors import MCPToolNotFoundError
from acc.mcp.registry import MCPRegistry
from acc.mcp.transports import SESSION_ID_HEADER, StreamableHTTPTransport

_REPO = Path(__file__).resolve().parent.parent
_SESSION = json.loads((_REPO / "tests" / "fixtures" / "feast" / "mcp_session.json").read_text())

READS = {"get_online_features_get_online_features_post", "health_health_get"}
WRITES = {
    "push_push_post",
    "write_to_online_store_write_to_online_store_post",
    "materialize_materialize_post",
    "materialize_incremental_materialize_incremental_post",
}


@pytest.fixture(scope="module")
def manifest():
    reg = MCPRegistry()
    reg.load_from(str(_REPO / "mcps"))
    return reg.manifest("feast")


def test_manifest_contract(manifest):
    assert manifest.transport == "streamable-http"
    assert manifest.url == "http://acc-mcp-feast:6566/mcp"
    assert manifest.risk_level == "LOW"
    assert set(manifest.allowed_tools) == READS
    assert WRITES <= set(manifest.denied_tools)
    assert {t.name for t in manifest.tools} == READS


def test_only_reads_survive_the_recorded_tool_list(manifest):
    advertised = set(_SESSION["tools_list"])
    assert WRITES <= advertised  # the server really offers writes
    assert {n for n in advertised if manifest.is_tool_allowed(n)} == READS
    assert not manifest.is_tool_allowed("delete_feature_view_delete_feature_view_post")


def _replay_server(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append(body)
        method = body.get("method")
        headers = {"content-type": "application/json", SESSION_ID_HEADER: "feast-1"}
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "feast-capacity", "version": "1.0.0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in _SESSION["tools_list"]]}
        elif method == "tools/call":
            result = _SESSION["result"]
        else:
            return httpx.Response(202, text="")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result},
                              headers=headers)
    return handler


@pytest.mark.asyncio
async def test_acc_client_reads_features_and_cannot_write(manifest, monkeypatch):
    calls: list = []

    def build(m):
        t = StreamableHTTPTransport(m)
        t._client = httpx.AsyncClient(base_url=m.url, transport=httpx.MockTransport(_replay_server(calls)),
                                      headers={"Accept": "application/json, text/event-stream"})
        return t

    monkeypatch.setattr(mcp_client, "build_transport", build)
    c = mcp_client.MCPClient(manifest)
    assert {t["name"] for t in await c.list_tools()} == READS

    out = await c.call_tool(_SESSION["call"]["name"], _SESSION["call"]["arguments"])
    data = json.loads(out["content"][0]["text"])
    names = data["metadata"]["feature_names"]
    rows = [{n: col["values"][i] for n, col in zip(names, data["results"])} for i in range(2)]
    assert rows[0] == {"model_deployment": "flg1/diffusiongemma-26b-a4b",
                       "requests_per_min": 12, "gpu_util_p95_1h": 0.42}
    assert data["results"][1]["statuses"][1] == "NOT_FOUND"

    n_before = len(calls)
    for write in sorted(WRITES):
        with pytest.raises(MCPToolNotFoundError):
            await c.call_tool(write, {})
    assert not any(b.get("method") == "tools/call" for b in calls[n_before:])
    await c.close()


def test_declares_no_gate_category(manifest):
    """fastapi-mcp's "..._post" names would otherwise read as acts_on_behalf."""
    from acc.operating_modes import gate_categories, should_gate_invocation

    assert manifest.system_access is False and manifest.acts_on_behalf is False
    for tool in manifest.allowed_tools:
        target = "feast." + tool
        assert gate_categories("mcp", target, manifest) == frozenset()
        assert not should_gate_invocation("AUTO", kind="mcp", target=target,
                                          risk_level=manifest.risk_level,
                                          categories=gate_categories("mcp", target, manifest))
