"""Web Fetch MCP server unit tests (E2).

Loads ``container/production/web_fetch/main.py`` via importlib.
Mirrors ``tests/test_echo_mcp_server.py``.

Coverage:
* JSON-RPC dispatch shape (initialize / tools/list / tools/call).
* Argument validation (missing url, non-http url, bad max_chars).
* Paywall detection — HTTP 401 / 402 / content-pattern.
* HTML stripping reduces a full HTML doc to a sensible
  markdown-ish text.
* max_chars truncation flag is honoured.
* Network errors surface in the result's ``error`` field rather
  than raising.
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_SERVER_PATH = (
    Path(__file__).resolve().parent.parent
    / "container" / "production" / "web_fetch" / "main.py"
)


@pytest.fixture(scope="module")
def fetch_module():
    spec = importlib.util.spec_from_file_location(
        "web_fetch_main", _SERVER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# JSON-RPC dispatch contract
# ---------------------------------------------------------------------------


def test_initialize_returns_protocol_version_and_server_info(fetch_module):
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
    })
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "acc-mcp-web-fetch"


def test_tools_list_advertises_fetch(fetch_module):
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    })
    tools = response["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "fetch"
    assert "url" in tools[0]["inputSchema"]["required"]


def test_unknown_method_returns_minus_32601(fetch_module):
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {},
    })
    assert response["error"]["code"] == -32601


def test_unknown_tool_returns_minus_32601(fetch_module):
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "shell.exec", "arguments": {}},
    })
    assert response["error"]["code"] == -32601


def test_non_http_url_rejected(fetch_module):
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch",
                   "arguments": {"url": "ftp://example.org/file"}},
    })
    assert response["error"]["code"] == -32602


def test_missing_url_rejected(fetch_module):
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch", "arguments": {}},
    })
    assert response["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# Paywall detection
# ---------------------------------------------------------------------------


def test_http_401_marks_paywalled(fetch_module, monkeypatch):
    def _raise_401(req, timeout=None):
        raise urllib.error.HTTPError(
            url=req.full_url, code=401, msg="Unauthorized",
            hdrs=None, fp=io.BytesIO(b"Subscribe to read"),
        )

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", _raise_401)

    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch",
                   "arguments": {"url": "https://paywalled.example/article"}},
    })
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["paywalled"] is True
    assert decoded["status_code"] == 401


def test_http_402_marks_paywalled(fetch_module, monkeypatch):
    def _raise_402(req, timeout=None):
        raise urllib.error.HTTPError(
            url=req.full_url, code=402, msg="Payment Required",
            hdrs=None, fp=io.BytesIO(b""),
        )

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", _raise_402)

    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch",
                   "arguments": {"url": "https://example.org/x"}},
    })
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["paywalled"] is True


def test_200_with_paywall_pattern_marks_paywalled(fetch_module, monkeypatch):
    """A 200 response whose body contains a known paywall snippet is
    flagged as paywalled even though the HTTP status is OK."""
    body = (
        b"<html><body><p>This article is for subscribers only.</p>"
        b"<p>Lead paragraph visible to non-subscribers.</p></body></html>"
    )

    class _FakeResp:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            return body[:n] if n else body

    monkeypatch.setattr(
        fetch_module.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResp(),
    )
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch",
                   "arguments": {"url": "https://example.org/article"}},
    })
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["status_code"] == 200
    assert decoded["paywalled"] is True
    # Visible body still returned so the persona can quote the lede.
    assert "Lead paragraph" in decoded["markdown"]


def test_200_no_paywall_pattern_clears_flag(fetch_module, monkeypatch):
    body = (
        b"<html><body><p>Free content for all readers.</p></body></html>"
    )

    class _FakeResp:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            return body[:n] if n else body

    monkeypatch.setattr(
        fetch_module.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResp(),
    )
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch",
                   "arguments": {"url": "https://example.org/free"}},
    })
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["paywalled"] is False


# ---------------------------------------------------------------------------
# HTML stripping + truncation
# ---------------------------------------------------------------------------


def test_html_stripping_reduces_to_plain_text(fetch_module):
    html = (
        "<html><head><script>var x = 1;</script><style>.a{}</style></head>"
        "<body><h1>Title</h1><p>One paragraph.</p>"
        "<p>Another <b>paragraph</b>.</p></body></html>"
    )
    text = fetch_module._strip_html(html)
    assert "var x = 1" not in text       # script body dropped
    assert "{" not in text                # style body dropped
    assert "Title" in text
    assert "One paragraph." in text
    assert "Another paragraph." in text


def test_truncation_flag_set_when_body_exceeds_max_chars(fetch_module, monkeypatch):
    huge = b"<html><body>" + b"abcde " * 20_000 + b"</body></html>"

    class _FakeResp:
        status = 200
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            return huge[:n] if n else huge

    monkeypatch.setattr(
        fetch_module.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResp(),
    )
    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch",
                   "arguments": {"url": "https://example.org/huge",
                                 "max_chars": 1000}},
    })
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["truncated"] is True
    assert len(decoded["markdown"]) <= 1000


# ---------------------------------------------------------------------------
# Network errors surface as data, not exceptions
# ---------------------------------------------------------------------------


def test_network_error_returns_structured_error(fetch_module, monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.URLError("DNS resolution failed")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", _raise)

    response = fetch_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch",
                   "arguments": {"url": "https://nonexistent.invalid"}},
    })
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["status_code"] == 0
    assert "DNS" in decoded["error"]
    assert decoded["paywalled"] is False
