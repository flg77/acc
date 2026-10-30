"""A mock MCP server (streamable-http) that answers from a suite's mock world.

    python -m acc.eval_harness.mock_mcp examples/eval_scenarios/sre.yaml --port 8765

``POST /mcp`` answers from the suite's ``mocks``; ``POST /mcp/<scenario_id>``
also applies that scenario's ``mock_overrides``. The scenario sits in the URL
so a plain MCP manifest can select it -- no custom header needed.

It advertises every tool in the suite's role universe plus every mocked tool,
each with an open input schema. A call no mock answers comes back as a tool
error (``isError``) whose text starts with ``no mock for``, so the agent sees a
failing tool rather than a broken server.

Stdlib only (``http.server``): answers are plain JSON bodies, which the
streamable-http binding allows, and ``GET`` (the optional server-to-client
stream) is 405. That keeps it runnable in CI without an MCP SDK.
"""

from __future__ import annotations

import argparse
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from acc.eval_harness.mocks import NoMock, answer, load_suite

SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_PATH = "/mcp"


class MockMCPServer:
    """Serve one suite. ``port=0`` picks a free port; read it from :attr:`url`."""

    def __init__(self, suite: dict, host: str = "127.0.0.1", port: int = 0) -> None:
        self.suite = suite
        self.scenario_ids = {sc["id"] for sc in suite["scenarios"]}
        tools = {t for role in suite["roles"].values() for t in role["tools"]}
        self.tools = sorted(tools | set(suite.get("mocks", {})))
        self.httpd = ThreadingHTTPServer((host, port), _handler(self))
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}{_PATH}"

    def start(self) -> "MockMCPServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "MockMCPServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def rpc(self, msg: dict, scenario_id: Optional[str]) -> Optional[dict]:
        """The JSON-RPC response to *msg*, or ``None`` for a notification."""
        if "id" not in msg:
            return None
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            result: Any = {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"acc-eval-mock-{self.suite['suite']}", "version": "1"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [
                {"name": t, "description": f"Mock of {t} ({self.suite['suite']} suite).",
                 "inputSchema": {"type": "object", "additionalProperties": True}}
                for t in self.tools]}
        elif method == "tools/call":
            result = self._call(params.get("name", ""), params.get("arguments") or {}, scenario_id)
        else:
            return {"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}

    def _call(self, tool: str, args: dict, scenario_id: Optional[str]) -> dict:
        try:
            value = answer(self.suite, tool, args, scenario_id)
        except NoMock as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
        if isinstance(value, dict):
            result["structuredContent"] = value
        return result


def _handler(server: MockMCPServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep test output quiet
            pass

        def _scenario(self) -> tuple[bool, Optional[str]]:
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == _PATH:
                return True, None
            sid = path[len(_PATH) + 1:] if path.startswith(_PATH + "/") else ""
            return sid in server.scenario_ids, sid or None

        def _send(self, status: int, body: Optional[dict] = None, session: str = "") -> None:
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            if session:
                self.send_header(SESSION_HEADER, session)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            # Read the body before any answer: replying to an unread request
            # makes the client's socket reset on Windows.
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            known, scenario_id = self._scenario()
            if not known:
                self._send(404, {"error": f"unknown path {self.path}"})
                return
            try:
                msg = json.loads(body)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                self._send(400, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "parse error"}})
                return
            response = server.rpc(msg, scenario_id)
            if response is None:
                self._send(202)
                return
            session = uuid.uuid4().hex if msg.get("method") == "initialize" else ""
            self._send(200, response, session)

        def do_GET(self) -> None:
            self._send(405)

        def do_DELETE(self) -> None:
            self._send(200)

    return Handler


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("suite", help="path to a suite YAML")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = MockMCPServer(load_suite(args.suite), args.host, args.port)
    print(f"mock MCP for suite {server.suite['suite']!r} at {server.url}"
          f" (append /<scenario_id> for its overrides)", flush=True)
    try:
        server.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.httpd.server_close()


if __name__ == "__main__":
    main()
