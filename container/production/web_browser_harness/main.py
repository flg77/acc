"""Browser-harness MCP server — JSON-RPC 2.0 over HTTP.

Wraps `browser-use <https://github.com/browser-use/browser-harness>`_'s
LLM-driven browser automation behind a single MCP tool ``browse``.

Risk level **HIGH**: the harness drives a real Chromium browser
through arbitrary operator-untrusted pages.  A malicious site can
attempt phishing flows, JS fingerprinting, or social-engineer the
harness into clicking destructive UI.  Cat-A A-018 gates per
invocation; the persona's role.yaml must explicitly raise
``max_mcp_risk_level: HIGH`` to reach this server.

The harness's own LLM client reuses the agent's API credentials —
``ACC_ANTHROPIC_API_KEY`` / ``ACC_OPENAI_API_KEY`` are passed
through via the container env.  ``BROWSER_HARNESS_HEADLESS`` controls
whether Chromium runs visibly (``false`` for local debugging) or
headless (``true``, the default for production).

The browser-use import is lazy so unit tests can drive
``handle_jsonrpc`` against a stub without Playwright in the test
environment.  See ``run_browse_task`` for the indirection point.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger("acc.web_browser_harness_mcp")


_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "acc-mcp-web-browser-harness"
_SERVER_VERSION = "0.1.0"

# browser-use's Agent walks the page in steps; we cap to keep wall-clock
# bounded.  Personas can override per-call; the manifest's input schema
# advertises the cap.
_DEFAULT_MAX_STEPS = 25
_HARD_MAX_STEPS = 60


_TOOLS_ADVERTISEMENT: list[dict] = [
    {
        "name": "browse",
        "description": (
            "Drive a Chromium browser to perform a research task.  "
            "Returns the harness's result text plus a per-step trace.  "
            "HIGH-risk MCP — persona must opt in via "
            "max_mcp_risk_level: HIGH."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["task"],
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Natural-language description of the browse "
                        "task (e.g. 'find the 2025 GA release date for "
                        "AWS Bedrock Agents and copy the announcement "
                        "URL')."
                    ),
                },
                "max_steps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _HARD_MAX_STEPS,
                    "default": _DEFAULT_MAX_STEPS,
                },
                "start_url": {
                    "type": "string",
                    "description": (
                        "Optional starting URL.  When omitted the "
                        "harness opens about:blank and lets the "
                        "underlying LLM choose where to navigate."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------------------
# Lazy harness factory — kept as a module-level callable so tests can
# monkey-patch with a stub.
# ---------------------------------------------------------------------------


async def run_browse_task(
    task: str, *, max_steps: int, start_url: str,
) -> dict:
    """Execute one browse task and return a structured result.

    Lazy-imports :mod:`browser_use` so the JSON-RPC dispatcher can be
    unit-tested in environments without Playwright + Chromium
    installed.

    Returns::

        {"result": str, "steps": list[str], "success": bool, "error": str}

    Errors do NOT raise — the agent reads ``success`` + ``error``
    and decides whether to fall back to web_search + web_fetch.
    """
    try:
        from browser_use import Agent  # noqa: PLC0415 — heavy dep
    except ImportError as exc:
        return {
            "result": "",
            "steps": [],
            "success": False,
            "error": f"browser_use unavailable: {exc}",
        }

    headless = os.environ.get("BROWSER_HARNESS_HEADLESS", "true").lower() != "false"
    backend = os.environ.get("BROWSER_HARNESS_BACKEND", "anthropic").lower()

    try:
        llm = _build_harness_llm(backend)
    except Exception as exc:
        return {
            "result": "",
            "steps": [],
            "success": False,
            "error": f"harness LLM init failed: {exc}",
        }

    full_task = task
    if start_url:
        full_task = f"Open {start_url} as the starting point.  Then: {task}"

    try:
        agent = Agent(
            task=full_task,
            llm=llm,
            headless=headless,
            max_steps=max_steps,
        )
        history = await agent.run()
    except Exception as exc:
        logger.exception("browser_use Agent.run failed")
        return {
            "result": "",
            "steps": [],
            "success": False,
            "error": f"harness run failed: {exc}",
        }

    final_result = ""
    steps: list[str] = []
    try:
        # browser-use returns a History-like object; we read the
        # final extracted content + step trace.  Falls back gracefully
        # when the API shifts (it has between releases).
        final_result = (
            getattr(history, "final_result", lambda: "")()
            or str(getattr(history, "result", "") or "")
        )
        for step in getattr(history, "history", []) or []:
            steps.append(str(step))
    except Exception:  # pragma: no cover — defensive against API drift
        logger.exception("browser_use history parse failed")

    return {
        "result": final_result,
        "steps": steps[-50:],  # cap trace size
        "success": True,
        "error": "",
    }


def _build_harness_llm(backend: str):
    """Construct an LLM client browser-use can drive.

    Two backends supported in this PR:
    * ``anthropic`` — needs ``ACC_ANTHROPIC_API_KEY``.
    * ``openai_compat`` — needs ``ACC_OPENAI_BASE_URL`` +
      ``ACC_OPENAI_API_KEY`` (vLLM / OpenShift AI / OpenAI proper).
    """
    if backend == "anthropic":
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
        api_key = os.environ.get("ACC_ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ACC_ANTHROPIC_API_KEY not set")
        model = os.environ.get("ACC_ANTHROPIC_MODEL", "claude-sonnet-4-5")
        return ChatAnthropic(model=model, api_key=api_key)
    if backend == "openai_compat":
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        api_key = os.environ.get("ACC_OPENAI_API_KEY", "")
        base_url = os.environ.get("ACC_OPENAI_BASE_URL", "")
        if not api_key or not base_url:
            raise RuntimeError(
                "ACC_OPENAI_API_KEY + ACC_OPENAI_BASE_URL must be set"
            )
        model = os.environ.get("ACC_OPENAI_MODEL", "gpt-4")
        return ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
    raise RuntimeError(f"unknown BROWSER_HARNESS_BACKEND={backend!r}")


# ---------------------------------------------------------------------------
# JSON-RPC dispatcher
# ---------------------------------------------------------------------------


def handle_jsonrpc(envelope: dict) -> dict:
    """Dispatch one JSON-RPC 2.0 request envelope.

    ``tools/call`` runs :func:`run_browse_task` synchronously by
    spawning a fresh asyncio loop — the harness is async internally
    but we expose a sync RPC surface so the stdlib HTTPServer can
    drive us without an event-loop integration.

    Tests monkey-patch :func:`run_browse_task` to avoid the live
    Playwright dependency.
    """
    rid = envelope.get("id")
    method = envelope.get("method", "")
    params = envelope.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": _SERVER_NAME,
                    "version": _SERVER_VERSION,
                },
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"tools": list(_TOOLS_ADVERTISEMENT)},
        }

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if tool_name != "browse":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32601,
                    "message": f"unknown tool {tool_name!r}",
                },
            }

        task = arguments.get("task", "")
        if not isinstance(task, str) or not task.strip():
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32602,
                    "message": "browse: 'task' must be a non-empty string",
                },
            }

        try:
            max_steps = int(arguments.get("max_steps", _DEFAULT_MAX_STEPS))
        except (TypeError, ValueError):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32602,
                    "message": "browse: 'max_steps' must be an integer",
                },
            }
        max_steps = max(1, min(_HARD_MAX_STEPS, max_steps))

        start_url = str(arguments.get("start_url", "") or "")

        # Drive the async harness from a sync stdlib HTTP handler —
        # safe because each request gets its own coroutine + loop.
        try:
            result = asyncio.run(
                run_browse_task(
                    task, max_steps=max_steps, start_url=start_url,
                )
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("browser_use task failed")
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32603,
                    "message": f"harness error: {exc}",
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps(result),
                }],
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


# ---------------------------------------------------------------------------
# HTTP request handler — same shape as echo_mcp_server
# ---------------------------------------------------------------------------


class _MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug(
            "browser_harness_mcp: %s - %s",
            self.address_string(), fmt % args,
        )

    def do_GET(self) -> None:  # noqa: N802
        body = (
            f"{_SERVER_NAME} {_SERVER_VERSION}\n"
            f"protocolVersion {_PROTOCOL_VERSION}\n"
            f"POST a JSON-RPC 2.0 envelope to invoke.\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            envelope = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            })
            return
        if not isinstance(envelope, dict):
            self._send_json(400, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid envelope"},
            })
            return
        response = handle_jsonrpc(envelope)
        self._send_json(200, response)

    def _send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    addr = ("0.0.0.0", 8080)
    logger.info(
        "%s starting on %s:%d (protocolVersion=%s, headless=%s)",
        _SERVER_NAME, addr[0], addr[1], _PROTOCOL_VERSION,
        os.environ.get("BROWSER_HARNESS_HEADLESS", "true"),
    )
    HTTPServer(addr, _MCPHandler).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
