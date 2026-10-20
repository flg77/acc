"""The ``aisimulators`` MCP manifest — ConfigIQ's AISimulators API (backlog 019).

The server is fastapi-mcp over a FastAPI app, so its tool names are FastAPI
operation ids (``<handler>_<path>_<method>``). These tests pin the manifest's
contract: streamable HTTP (ACC does not speak fastapi-mcp's SSE mount), a
bounded tool surface, ``/metrics`` and the deprecated ``/estimate`` denied —
and drive ACC's real client over a scripted server that advertises every
operation the upstream app has.
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

# Every operation configiq services/aisimulators/tools/api_service/app.py
# declares (configiq 1cd8145), as fastapi-mcp names them.
UPSTREAM_TOOLS = [
    "get_backends_backends_get",
    "post_recommend_recommend_post",
    "post_predict_predict_post",
    "post_predict_estimate_post",
    "post_memory_memory_post",
    "get_models_models_get",
    "get_systems_systems_get",
    "get_metrics_metrics_get",
]
ALLOWED = {
    "post_recommend_recommend_post", "post_predict_predict_post", "post_memory_memory_post",
    "get_systems_systems_get", "get_models_models_get", "get_backends_backends_get",
}


@pytest.fixture(scope="module")
def manifest():
    reg = MCPRegistry()
    reg.load_from(str(_REPO / "mcps"))
    return reg.manifest("aisimulators")


def test_manifest_contract(manifest):
    assert manifest.transport == "streamable-http"
    assert manifest.url == "http://acc-mcp-aisimulators:7860/mcp/http"
    assert manifest.risk_level == "LOW"
    assert set(manifest.allowed_tools) == ALLOWED
    assert {"get_metrics_metrics_get", "post_predict_estimate_post"} <= set(manifest.denied_tools)
    # Every documented tool is one the manifest permits.
    assert {t.name for t in manifest.tools} == ALLOWED


def test_bounded_surface_over_every_upstream_operation(manifest):
    permitted = {name for name in UPSTREAM_TOOLS if manifest.is_tool_allowed(name)}
    assert permitted == ALLOWED
    # A tool upstream adds later is not silently granted.
    assert not manifest.is_tool_allowed("post_new_thing_new_thing_post")


def _scripted_server(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        method = body.get("method")
        calls.append(body)
        headers = {"content-type": "application/json", SESSION_ID_HEADER: "s1"}
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "aisimulators", "version": "1.0.0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in UPSTREAM_TOOLS]}
        elif method == "tools/call":
            text = json.dumps({"configs": [{"tp": 2, "replicas_needed": 3}]})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:  # notifications/initialized
            return httpx.Response(202, text="")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result},
                              headers=headers)
    return handler


@pytest.mark.asyncio
async def test_acc_client_sees_and_calls_only_the_allowed_tools(manifest, monkeypatch):
    calls: list = []

    def build(m):
        t = StreamableHTTPTransport(m)
        t._client = httpx.AsyncClient(
            base_url=m.url, transport=httpx.MockTransport(_scripted_server(calls)),
            headers={"Accept": "application/json, text/event-stream"},
        )
        return t

    monkeypatch.setattr(mcp_client, "build_transport", build)
    c = mcp_client.MCPClient(manifest)
    tools = await c.list_tools()
    assert {t["name"] for t in tools} == ALLOWED

    out = await c.call_tool("post_recommend_recommend_post", {
        "model_path": "meta-llama/Llama-3.1-70B-Instruct", "system": "h100_sxm",
        "ttft": 2000, "tpot": 30, "target_concurrency": 64})
    assert json.loads(out["content"][0]["text"])["configs"][0]["replicas_needed"] == 3
    sent = [b for b in calls if b.get("method") == "tools/call"][-1]
    assert sent["params"]["name"] == "post_recommend_recommend_post"

    n_before = len(calls)
    for denied in ("get_metrics_metrics_get", "post_predict_estimate_post"):
        with pytest.raises(MCPToolNotFoundError):
            await c.call_tool(denied, {})
    assert not any(b.get("method") == "tools/call" for b in calls[n_before:])
    await c.close()


def test_declares_no_gate_category(manifest):
    """fastapi-mcp's "..._post" names would otherwise read as acts_on_behalf."""
    from acc.operating_modes import gate_categories, should_gate_invocation

    assert manifest.system_access is False and manifest.acts_on_behalf is False
    for tool in manifest.allowed_tools:
        target = "aisimulators." + tool
        assert gate_categories("mcp", target, manifest) == frozenset()
        assert not should_gate_invocation("AUTO", kind="mcp", target=target,
                                          risk_level=manifest.risk_level,
                                          categories=gate_categories("mcp", target, manifest))
