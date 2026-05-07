"""Brave Search MCP server unit tests (E2).

Loads ``container/production/web_search_brave/main.py`` via importlib
so the test suite doesn't need to add a sys.path entry for the
container/ tree.  Mirrors the pattern in
``tests/test_echo_mcp_server.py``.

Coverage:
* JSON-RPC initialize / tools/list / tools/call dispatch shape.
* Bad arg paths (missing query, non-string query, wrong tool name).
* Brave API call path is exercised via a monkey-patch on the
  module's ``urllib.request.urlopen`` so no real HTTP traffic.
* Missing BRAVE_API_KEY surfaces as a -32603 error with a clear
  operator-readable message.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest


_SERVER_PATH = (
    Path(__file__).resolve().parent.parent
    / "container" / "production" / "web_search_brave" / "main.py"
)


@pytest.fixture(scope="module")
def brave_module():
    spec = importlib.util.spec_from_file_location(
        "web_search_brave_main", _SERVER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Initialize / tools/list / id-echo invariant
# ---------------------------------------------------------------------------


def test_initialize_returns_protocol_version_and_server_info(brave_module):
    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
    })
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "acc-mcp-web-search-brave"
    assert "capabilities" in result


def test_tools_list_advertises_search(brave_module):
    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {},
    })
    tools = response["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "search"
    assert "query" in tools[0]["inputSchema"]["required"]


def test_response_id_always_echoes_request_id(brave_module):
    for rid in (1, 99, "abc", "", None):
        response = brave_module.handle_jsonrpc({
            "jsonrpc": "2.0", "id": rid, "method": "initialize", "params": {},
        })
        assert response["id"] == rid


# ---------------------------------------------------------------------------
# tools/call — argument validation
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_error(brave_module):
    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "shell.exec", "arguments": {}},
    })
    assert response["error"]["code"] == -32601
    assert "shell.exec" in response["error"]["message"]


def test_missing_query_returns_param_error(brave_module):
    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "search", "arguments": {}},
    })
    assert response["error"]["code"] == -32602
    assert "query" in response["error"]["message"]


def test_non_string_query_returns_param_error(brave_module):
    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": 12345}},
    })
    assert response["error"]["code"] == -32602


def test_unknown_method_returns_method_not_found(brave_module):
    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {},
    })
    assert response["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Brave API path — mocked urlopen
# ---------------------------------------------------------------------------


def test_missing_api_key_surfaces_clear_error(brave_module, monkeypatch):
    """No BRAVE_API_KEY → -32603 with an operator-readable message.
    The personas can fall back to web_browser_harness when this fires."""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": "ACC test"}},
    })
    assert response["error"]["code"] == -32603
    assert "BRAVE_API_KEY" in response["error"]["message"]


def test_search_call_normalises_brave_response(brave_module, monkeypatch):
    """Mock the upstream API response and assert the result shape the
    persona consumes is the four-field reduced view."""
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")

    fake_payload = {
        "web": {
            "results": [
                {
                    "title": "ACC overview",
                    "url": "https://example.org/acc",
                    "description": "Agentic Cell Corpus reference",
                    "age": "2 weeks",
                    "deep_links": [{"url": "..."}],   # field we drop
                    "schema_org": {"foo": "bar"},     # field we drop
                },
                {
                    "title": "ACC PR #41",
                    "url": "https://github.com/flg77/acc/pull/41",
                    "description": "Iteration loop",
                    "age": "1 day",
                },
            ],
        },
    }

    class _FakeResp:
        status = 200
        headers = {"Content-Encoding": ""}

        def __init__(self, body: bytes):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps(fake_payload).encode("utf-8"))

    monkeypatch.setattr(brave_module.urllib.request, "urlopen", _fake_urlopen)

    response = brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": "ACC overview"}},
    })
    assert "error" not in response
    text = response["result"]["content"][0]["text"]
    decoded = json.loads(text)
    assert decoded["query"] == "ACC overview"
    results = decoded["results"]
    assert len(results) == 2
    assert results[0]["title"] == "ACC overview"
    assert results[0]["url"] == "https://example.org/acc"
    assert results[0]["description"] == "Agentic Cell Corpus reference"
    # Lossy reduction: deep_links / schema_org dropped.
    assert "deep_links" not in results[0]
    assert "schema_org" not in results[0]


def test_search_count_clamps_to_max(brave_module, monkeypatch):
    """Operator-supplied count > 20 is clamped to the API ceiling."""
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    captured: dict = {}

    class _FakeResp:
        status = 200
        headers = {}

        def __init__(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"web": {"results": []}}).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp()

    monkeypatch.setattr(brave_module.urllib.request, "urlopen", _fake_urlopen)

    brave_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "search", "arguments": {
            "query": "x", "count": 999,
        }},
    })
    # 999 → clamped to 20.
    assert "count=20" in captured["url"]
