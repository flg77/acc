"""Minimal JSON-RPC 2.0 helpers for the A2A inbound endpoint.

OpenSpec: ``openspec/changes/20260527-a2a-agent-interop/`` (Phase 2).
Docs: ``docs/a2a-interop.md``.

We don't pull a third-party JSON-RPC library: A2A's surface area is small
enough to handle with a few helpers, and avoiding the dep keeps the ``acc[a2a]``
extra light (just ``aiohttp`` for the HTTP layer).

Two helpers + the standard error-code table:

- :func:`parse_request` validates a JSON-RPC 2.0 request body and returns
  ``(error_message, method, params, id)``.  ``error_message is not None``
  ⇒ the body is invalid (caller emits ``error(req_id, INVALID_REQUEST, …)``).
- :func:`success`, :func:`error` produce the well-formed JSON-RPC responses
  the A2A server writes back to the wire.

The custom code ``GOVERNANCE_BLOCKED = -32001`` (within the JSON-RPC
implementation-defined server-error range) lets an A2A caller distinguish
"denied by ACC governance" from a generic "internal error" — see
:func:`acc.a2a.client.A2AClientError.is_governance_blocked`, which keys off
this code to skip the NATS reachability fallback (a denial is a denial).
"""

from __future__ import annotations

from typing import Any

# Standard JSON-RPC 2.0 error codes (https://www.jsonrpc.org/specification).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Custom server errors (-32000 to -32099 are reserved for implementation-defined
# server errors).  We use -32001 for "blocked by governance" so an A2A caller
# can distinguish a governance denial from an internal failure.
SERVER_ERROR = -32000
GOVERNANCE_BLOCKED = -32001


def parse_request(body: Any) -> tuple[str | None, str | None, dict, Any | None]:
    """Validate a JSON-RPC 2.0 request body.

    Returns ``(error_message, method, params, id)``.  If ``error_message`` is
    not None the body is invalid; otherwise the rest is populated.  ``id`` may
    legitimately be ``None`` for a notification (no response expected), but the
    A2A inbound endpoint always returns one, so callers treat ``id is None``
    as a normal id value, not "no response".
    """
    if not isinstance(body, dict):
        return ("Request body must be a JSON object", None, {}, None)
    if body.get("jsonrpc") != "2.0":
        return ("Missing or invalid 'jsonrpc' (must be '2.0')", None, {}, None)
    method = body.get("method")
    if not isinstance(method, str) or not method:
        return ("Missing or invalid 'method'", None, {}, None)
    params = body.get("params")
    if params is None:
        params = {}
    elif not isinstance(params, (dict, list)):
        return ("'params' must be an object or array", None, {}, None)
    return (None, method, params if isinstance(params, dict) else {"_": params}, body.get("id"))


def success(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def error(req_id: Any, code: int, message: str, data: Any | None = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}
