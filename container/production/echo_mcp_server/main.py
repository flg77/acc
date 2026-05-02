"""Minimal MCP echo server — JSON-RPC 2.0 over HTTP.

Trivial diagnostic server that satisfies the three MCP methods
:class:`acc.mcp.client.MCPClient` exercises:

* ``initialize``   — capability handshake, returns a valid
  ``protocolVersion`` + ``serverInfo`` block.
* ``tools/list``   — advertises one tool named ``echo``.
* ``tools/call``   — when ``name == "echo"`` returns the input
  ``arguments.text`` wrapped in MCP's canonical
  ``{"content": [{"type": "text", "text": <input>}]}`` envelope.

Design choices:

* **stdlib only** — no Flask, no aiohttp.  Keeps the image footprint
  to whatever the UBI10 python-312-minimal base provides.  The
  agent client makes one request per session in the typical smoke
  flow; ``http.server.HTTPServer`` (single-threaded) is more than
  enough.
* **No external dependencies** — the JSON-RPC envelope is dead
  simple to handle inline; pulling in ``mcp`` or ``json-rpc`` would
  be over-engineering for a diagnostic.
* **Listens on 0.0.0.0:8080** — matches the URL in
  ``mcps/echo_server/mcp.yaml`` (``http://acc-mcp-echo:8080/rpc``).
  The agent's HTTPTransport posts to the path it was configured
  with; we accept any path so future manifest edits don't require a
  server change.

Lifecycle:

* ``main()`` blocks forever serving requests; the container's
  default CMD invokes it.
* SIGTERM / SIGINT triggers a clean shutdown via Python's default
  KeyboardInterrupt handling — no extra signal wiring needed.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger("acc.echo_mcp")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Match the protocol version the ACC client advertises.  Newer MCP
# clients are lenient about minor revs; we just echo the version we
# support.
_PROTOCOL_VERSION = "2024-11-05"

_SERVER_NAME = "acc-mcp-echo"
_SERVER_VERSION = "0.1.0"

# Single-tool advertisement.  Schema mirrors what well-behaved MCP
# servers ship — name, description, inputSchema (JSON Schema fragment).
_TOOLS_ADVERTISEMENT: list[dict] = [
    {
        "name": "echo",
        "description": "Round-trip the input text — diagnostic for ACC clients.",
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to echo back.",
                },
            },
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC dispatcher (pure function — easy to unit-test)
# ---------------------------------------------------------------------------


def handle_jsonrpc(envelope: dict) -> dict:
    """Dispatch one JSON-RPC 2.0 request envelope.

    Args:
        envelope: Decoded request body — expected shape::

            {"jsonrpc": "2.0", "id": <int|str>, "method": str, "params": dict}

    Returns:
        Decoded response envelope — either ``result`` or ``error``,
        always carrying the same ``id`` as the request.

    Implementation note: this is a plain function (no class, no
    server reference) so unit tests can drive it directly without
    spinning up an HTTPServer.
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
        if tool_name != "echo":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32601,
                    "message": f"unknown tool {tool_name!r}",
                },
            }
        text = arguments.get("text", "")
        if not isinstance(text, str):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32602,
                    "message": "echo: 'text' must be a string",
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{"type": "text", "text": text}],
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------


class _EchoMCPHandler(BaseHTTPRequestHandler):
    """One-method HTTP handler — POST anywhere returns a JSON-RPC reply.

    GET requests get a plain-text 200 with a tiny self-describing
    string so a curl /health-style probe doesn't 404.
    """

    # Suppress the default access-log spam; we log via the module
    # logger instead so log lines match the rest of the ACC stack.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("echo_mcp: %s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        body = (
            f"acc-mcp-echo {_SERVER_VERSION}\n"
            f"protocolVersion {_PROTOCOL_VERSION}\n"
            f"POST a JSON-RPC 2.0 envelope to invoke.\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (stdlib API)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""

        try:
            envelope = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"parse error: {exc}",
                    },
                },
            )
            return

        if not isinstance(envelope, dict):
            self._send_json(
                400,
                {
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600, "message": "expected JSON object"},
                },
            )
            return

        try:
            response = handle_jsonrpc(envelope)
        except Exception as exc:
            logger.exception("echo_mcp: dispatch crashed for %r", envelope)
            response = {
                "jsonrpc": "2.0",
                "id": envelope.get("id"),
                "error": {
                    "code": -32603,
                    "message": f"internal error: {exc}",
                },
            }

        self._send_json(200, response)

    def _send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Block on the HTTP server until SIGINT / SIGTERM."""
    logging.basicConfig(
        level=os.environ.get("ACC_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    host = os.environ.get("ACC_MCP_ECHO_HOST", "0.0.0.0")
    port = int(os.environ.get("ACC_MCP_ECHO_PORT", "8080"))

    server = HTTPServer((host, port), _EchoMCPHandler)
    logger.info(
        "echo_mcp: listening on %s:%d (protocolVersion=%s)",
        host, port, _PROTOCOL_VERSION,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("echo_mcp: shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
