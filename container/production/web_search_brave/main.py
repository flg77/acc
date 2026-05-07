"""Brave Search MCP server — JSON-RPC 2.0 over HTTP.

Wraps the Brave Search API (https://api.search.brave.com/res/v1/web/search)
behind a single MCP tool ``search`` that ACC research personas invoke
via ``[SKILL: web_search ...]`` markers.

Free tier: 2k queries / month (as of 2026).  Operator supplies
``BRAVE_API_KEY`` via the .env / podman-compose environment.

Mirrors :mod:`container.production.echo_mcp_server.main` so the test
pattern (load module via ``importlib`` + drive ``handle_jsonrpc``)
applies unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger("acc.web_search_brave_mcp")


_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "acc-mcp-web-search-brave"
_SERVER_VERSION = "0.1.0"
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# Hard ceiling — Brave's API rejects values above 20.  Operators can
# override the default per-call via the count argument.
_MAX_RESULT_COUNT = 20


_TOOLS_ADVERTISEMENT: list[dict] = [
    {
        "name": "search",
        "description": (
            "Web search via Brave Search.  Returns a list of results "
            "(title, url, description, age) for the given query.  "
            "Personas should fetch the URLs they want to read in "
            "depth via the web_fetch MCP tool."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (operator-supplied; passed verbatim).",
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_RESULT_COUNT,
                    "default": 10,
                    "description": "Number of results to return.",
                },
            },
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC dispatcher
# ---------------------------------------------------------------------------


def handle_jsonrpc(envelope: dict) -> dict:
    """Dispatch one JSON-RPC 2.0 request envelope.

    See :mod:`container.production.echo_mcp_server.main` for the
    canonical contract — same shape, same error codes, same id-echo
    invariant the ACC client checks.
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
        if tool_name != "search":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32601,
                    "message": f"unknown tool {tool_name!r}",
                },
            }

        query = arguments.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32602,
                    "message": "search: 'query' must be a non-empty string",
                },
            }

        count_raw = arguments.get("count", 10)
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32602,
                    "message": "search: 'count' must be an integer",
                },
            }
        count = max(1, min(_MAX_RESULT_COUNT, count))

        results = _brave_search(query, count)
        if isinstance(results, dict) and results.get("__error__"):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32603,
                    "message": results["__error__"],
                },
            }

        # Canonical MCP content envelope: a single text content
        # carrying a JSON-encoded list of results.  Personas parse it
        # back via json.loads on the agent side.  Embedding the
        # structured payload as text keeps us compatible with MCP
        # clients that don't speak structured content yet.
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"query": query, "results": results}),
                }],
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


# ---------------------------------------------------------------------------
# Brave API client (stdlib urllib — no extra deps)
# ---------------------------------------------------------------------------


def _brave_search(query: str, count: int) -> Any:
    """Call the Brave Search API and return a normalised result list.

    Returns a list of ``{title, url, description, age}`` dicts on
    success, or a dict ``{"__error__": <message>}`` on failure (the
    JSON-RPC dispatcher converts that to a -32603 error).

    Errors are intentionally compact + operator-readable; the
    persona's prompt only needs to know "search worked / didn't
    work", not the underlying HTTP details.
    """
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return {"__error__": "BRAVE_API_KEY not set in container env"}

    url = (
        _BRAVE_ENDPOINT
        + "?"
        + urllib.parse.urlencode({"q": query, "count": str(count)})
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
            "User-Agent": f"{_SERVER_NAME}/{_SERVER_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"__error__": f"Brave HTTP {exc.code}: {exc.reason}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"__error__": f"Brave network error: {exc}"}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"__error__": f"Brave parse error: {exc}"}

    return _normalise_brave_payload(payload)


def _normalise_brave_payload(payload: dict) -> list[dict]:
    """Reduce Brave's verbose response to a small, agent-friendly shape.

    Brave returns ~30 fields per result; we keep only the four every
    research persona uses.  Lossy by design — if a persona needs
    deeper details (e.g. sitelinks, schema-org metadata), it should
    follow up via web_fetch on the URL.
    """
    web = (payload.get("web") or {}).get("results") or []
    out: list[dict] = []
    for item in web:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "description": str(item.get("description", "")),
            "age": str(item.get("age", "")),
        })
    return out


# ---------------------------------------------------------------------------
# HTTP request handler — same shape as echo_mcp_server
# ---------------------------------------------------------------------------


class _MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("brave_mcp: %s - %s", self.address_string(), fmt % args)

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


def main() -> None:  # pragma: no cover — exercised by the live container
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    addr = ("0.0.0.0", 8080)
    logger.info(
        "%s starting on %s:%d (protocolVersion=%s)",
        _SERVER_NAME, addr[0], addr[1], _PROTOCOL_VERSION,
    )
    HTTPServer(addr, _MCPHandler).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
