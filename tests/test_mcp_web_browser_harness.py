"""Browser-harness MCP server unit tests (E2).

Loads ``container/production/web_browser_harness/main.py`` via
importlib.  Tests do NOT require Chromium or the ``browser_use``
package to be installed — the JSON-RPC dispatcher's ``run_browse_task``
helper is monkey-patched to return a synthetic structure so the
test environment can stay light.

Coverage:
* JSON-RPC dispatch shape (initialize / tools/list / tools/call).
* Argument validation (missing / non-string task).
* tools/call drives ``run_browse_task`` and round-trips its result
  via the canonical content envelope.
* When ``run_browse_task`` reports ``success: false`` the agent
  receives the structured error rather than a -32603.
* max_steps clamping at the hard cap.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SERVER_PATH = (
    Path(__file__).resolve().parent.parent
    / "container" / "production" / "web_browser_harness" / "main.py"
)


@pytest.fixture(scope="module")
def harness_module():
    spec = importlib.util.spec_from_file_location(
        "web_browser_harness_main", _SERVER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Dispatch contract
# ---------------------------------------------------------------------------


def test_initialize_returns_protocol_version_and_server_info(harness_module):
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
    })
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "acc-mcp-web-browser-harness"


def test_tools_list_advertises_browse(harness_module):
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    })
    tools = response["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "browse"
    schema = tools[0]["inputSchema"]
    assert "task" in schema["required"]
    # max_steps + start_url are optional.
    assert "max_steps" in schema["properties"]
    assert "start_url" in schema["properties"]


def test_unknown_method_returns_minus_32601(harness_module):
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {},
    })
    assert response["error"]["code"] == -32601


def test_unknown_tool_returns_minus_32601(harness_module):
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "shell.exec", "arguments": {"task": "x"}},
    })
    assert response["error"]["code"] == -32601


def test_missing_task_rejected(harness_module):
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "browse", "arguments": {}},
    })
    assert response["error"]["code"] == -32602


def test_non_string_task_rejected(harness_module):
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "browse", "arguments": {"task": 12345}},
    })
    assert response["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# tools/call drives run_browse_task — monkey-patched stub
# ---------------------------------------------------------------------------


def _install_stub(harness_module, *, success: bool = True,
                  result: str = "did the thing",
                  steps: list[str] | None = None,
                  error: str = ""):
    """Patch ``run_browse_task`` with a synthetic async stub.

    Returns the captured-args dict so tests can assert what the
    handler passed in.
    """
    captured: dict = {}

    async def _stub(task: str, *, max_steps: int, start_url: str):
        captured["task"] = task
        captured["max_steps"] = max_steps
        captured["start_url"] = start_url
        return {
            "result": result, "steps": steps or [],
            "success": success, "error": error,
        }

    harness_module.run_browse_task = _stub
    return captured


def test_browse_call_round_trips_synthetic_result(harness_module):
    captured = _install_stub(harness_module, result="found it")
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "browse",
                   "arguments": {"task": "find the GA date"}},
    })
    assert "error" not in response
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["success"] is True
    assert decoded["result"] == "found it"
    assert captured["task"] == "find the GA date"
    # default max_steps from the manifest schema.
    assert captured["max_steps"] == 25
    assert captured["start_url"] == ""


def test_browse_max_steps_clamped_to_hard_cap(harness_module):
    captured = _install_stub(harness_module)
    harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "browse",
                   "arguments": {"task": "x", "max_steps": 9999}},
    })
    # Hard cap is 60 in the module.
    assert captured["max_steps"] == 60


def test_browse_failure_surfaces_in_result_not_jsonrpc_error(harness_module):
    """A harness failure (e.g. browser-use ImportError, network blow
    up) returns success:false in the structured payload — the
    agent's CognitiveCore reads the field and decides whether to
    fall back to web_search."""
    _install_stub(
        harness_module,
        success=False,
        result="",
        error="browser_use unavailable",
    )
    response = harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "browse", "arguments": {"task": "x"}},
    })
    # NO -32603 — the harness shape is "data, not exceptions".
    assert "error" not in response
    decoded = json.loads(response["result"]["content"][0]["text"])
    assert decoded["success"] is False
    assert "browser_use unavailable" in decoded["error"]


def test_browse_start_url_threaded_through(harness_module):
    captured = _install_stub(harness_module)
    harness_module.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "browse", "arguments": {
            "task": "extract the announcement",
            "start_url": "https://aws.amazon.com/news/2025-bedrock-agents/",
        }},
    })
    assert "aws.amazon.com" in captured["start_url"]
