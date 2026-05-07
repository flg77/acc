"""Web Fetch MCP server — JSON-RPC 2.0 over HTTP with paywall detection.

Exposes one tool ``fetch`` that retrieves a URL, converts the HTML
body to plain markdown-ish text, and surfaces a ``paywalled: bool``
flag the citation_tracker uses to mark unreliable sources.

Paywall detection is best-effort:

* HTTP 401 / 402 → unconditionally ``paywalled: true``.
* 200 OK + content-pattern match against ``_PAYWALL_PATTERNS``
  (well-known publisher snippets) → ``paywalled: true`` (markdown
  body still returned so the persona can quote what is visible).

The list is operator-extensible — patch ``_PAYWALL_PATTERNS`` and
rebuild the container.  No bus knob in this PR (deferred to E5+).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger("acc.web_fetch_mcp")


_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "acc-mcp-web-fetch"
_SERVER_VERSION = "0.1.0"

# Hard cap on the markdown body returned to the agent — keeps the
# bus payload bounded.  Personas that need the full body should use
# the harness MCP for navigation + selective extraction instead.
_MAX_BODY_CHARS = 50_000


# Known paywall content patterns — case-insensitive substring match
# against the *plain-text* body (after HTML strip).  Operator-extensible
# by editing this list; PR notes flag a follow-up to expose via Cat-B
# config.
_PAYWALL_PATTERNS: tuple[str, ...] = (
    "subscribe to read",
    "subscribe to continue",
    "this article is for subscribers",
    "you have reached your article limit",
    "subscribe now to continue reading",
    "register to continue reading",
    "create a free account to continue",
    "to continue reading, please subscribe",
    "this content is for members",
    "members only — log in",
)


_TOOLS_ADVERTISEMENT: list[dict] = [
    {
        "name": "fetch",
        "description": (
            "Retrieve a URL and return its body as markdown-ish text "
            "plus a paywalled flag.  Used by research personas to "
            "ground citations in primary sources.  Output is capped "
            "at 50000 chars; truncation is signalled via the "
            "'truncated' field."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute http(s) URL to retrieve.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": _MAX_BODY_CHARS,
                    "default": _MAX_BODY_CHARS,
                    "description": "Trim the markdown body at this length.",
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
    canonical error-code conventions.
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
        if tool_name != "fetch":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32601,
                    "message": f"unknown tool {tool_name!r}",
                },
            }

        url = arguments.get("url", "")
        if not isinstance(url, str) or not (
            url.startswith("http://") or url.startswith("https://")
        ):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32602,
                    "message": "fetch: 'url' must be an http(s) URL",
                },
            }

        max_chars_raw = arguments.get("max_chars", _MAX_BODY_CHARS)
        try:
            max_chars = int(max_chars_raw)
        except (TypeError, ValueError):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32602,
                    "message": "fetch: 'max_chars' must be an integer",
                },
            }
        max_chars = max(100, min(_MAX_BODY_CHARS, max_chars))

        result = _fetch_url(url, max_chars)
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
# HTTP fetch + HTML→text conversion + paywall detection
# ---------------------------------------------------------------------------


def _fetch_url(url: str, max_chars: int) -> dict:
    """Retrieve *url*, return a structured result dict.

    Result shape::

        {
            "url": str,
            "status_code": int,
            "paywalled": bool,
            "markdown": str,         # plain-text body, truncated
            "truncated": bool,
            "content_type": str,
            "error": str,            # populated on failure
        }

    Errors do NOT raise — the agent reads `status_code` + `error`
    and decides whether to retry / fall back.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html, text/plain;q=0.9, */*;q=0.5",
            "User-Agent": f"{_SERVER_NAME}/{_SERVER_VERSION}",
        },
    )
    status_code = 0
    body_bytes = b""
    content_type = ""
    error_text = ""
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status_code = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body_bytes = resp.read(max_chars * 4)  # over-read; HTML→text shrinks
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            body_bytes = exc.read(max_chars * 4)
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        except Exception:
            body_bytes = b""
        error_text = f"HTTP {exc.code}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return {
            "url": url, "status_code": 0, "paywalled": False,
            "markdown": "", "truncated": False,
            "content_type": "", "error": f"network error: {exc}",
        }

    body_text = _decode_body(body_bytes, content_type)
    truncated = len(body_text) > max_chars
    body_text = body_text[:max_chars]
    paywalled = _is_paywalled(status_code, body_text)

    return {
        "url": url,
        "status_code": status_code,
        "paywalled": paywalled,
        "markdown": body_text,
        "truncated": truncated,
        "content_type": content_type,
        "error": error_text,
    }


def _decode_body(raw: bytes, content_type: str) -> str:
    """Decode bytes → string + strip HTML when content_type indicates HTML.

    No external deps; the stripper is good-enough for plain-prose
    extraction.  Personas should reach for browser-harness when the
    page actually renders content via JS.
    """
    encoding = "utf-8"
    if content_type:
        for fragment in content_type.lower().split(";"):
            fragment = fragment.strip()
            if fragment.startswith("charset="):
                encoding = fragment.split("=", 1)[1].strip() or "utf-8"
                break
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")

    if "html" in content_type.lower() or text.lstrip().startswith("<"):
        return _strip_html(text)
    return text


def _strip_html(html: str) -> str:
    """Naive HTML → plain-text via :class:`HTMLParser`.

    Drops <script> / <style>; collapses whitespace.  Suitable for
    citation-grounding LLM prompts; not a faithful markdown rendering.
    """
    out: list[str] = []
    skip_depth = 0
    skip_tags = {"script", "style", "noscript", "iframe"}

    class _Stripper(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list) -> None:
            nonlocal skip_depth
            if tag in skip_tags:
                skip_depth += 1
            elif tag in {"p", "br", "div", "h1", "h2", "h3", "h4", "li"}:
                out.append("\n")

        def handle_endtag(self, tag: str) -> None:
            nonlocal skip_depth
            if tag in skip_tags and skip_depth > 0:
                skip_depth -= 1

        def handle_data(self, data: str) -> None:
            if skip_depth == 0:
                out.append(data)

    parser = _Stripper()
    try:
        parser.feed(html)
    except Exception:
        # Malformed HTML — fall back to raw text.
        return re.sub(r"\s+", " ", html).strip()
    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_paywalled(status_code: int, body_text: str) -> bool:
    """Cheap paywall heuristic — the doc string at module top
    explains the trade-offs."""
    if status_code in (401, 402):
        return True
    haystack = body_text.lower()
    return any(p in haystack for p in _PAYWALL_PATTERNS)


# ---------------------------------------------------------------------------
# HTTP request handler — same shape as echo_mcp_server
# ---------------------------------------------------------------------------


class _MCPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("fetch_mcp: %s - %s", self.address_string(), fmt % args)

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
        "%s starting on %s:%d (protocolVersion=%s)",
        _SERVER_NAME, addr[0], addr[1], _PROTOCOL_VERSION,
    )
    HTTPServer(addr, _MCPHandler).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
