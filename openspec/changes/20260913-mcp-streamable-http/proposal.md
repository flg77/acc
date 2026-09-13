# 20260913-mcp-streamable-http — proposal

Backlog: `20-backlog/mcp/` item **MC-01** ([[MC-01 — MCP streamable-HTTP
transport — ACC cannot reach a modern MCP server]]). The last of the MC family
after MC-02 (#401), MC-03 (#402) and MC-04 (#403).

## Why

`acc/mcp/manifest.py` offers two transports: `stdio`, and an `http` that POSTs a
bare JSON-RPC envelope and expects a JSON body back. That is a reasonable
reading of JSON-RPC-over-HTTP, and it is **not** the MCP HTTP transport.

MCP moved to **streamable HTTP**: the server issues a session id on
`initialize`, every later request carries it, the client confirms with a
`notifications/initialized`, and a response may come back as JSON *or* as an SSE
stream. Measured on 2026-09-12 against midojo's fake MCP — a `fastmcp`
`http_app(path="/mcp")`, i.e. the standard thing — ACC's POST is answered:

```
-32600 Missing session ID
```

`fastmcp` is what a large share of today's MCP servers are written with, so the
practical reach of ACC's MCP support is: stdio servers it launches itself, and
almost nothing else remote. AS-04 only proceeded behind a ~90-line shim on
lighthouse that speaks ACC's dialect on one side and a real MCP client on the
other — a fixture that has to go away before a midojo run is evidence about ACC
rather than about the shim.

## What changes

### Phase 1 (this ship)

1. **`transport: streamable-http`** — a third value, alongside `http` and
   `stdio`. The existing `http` stays exactly as it is: plain JSON-RPC endpoints
   exist and ACC already talks to them.
2. **`StreamableHTTPTransport`** implementing the same `Transport` protocol, so
   `MCPClient` and the dispatch layer are untouched beyond one line in
   `build_transport`:
   * captures `Mcp-Session-Id` from the `initialize` response and sends it on
     every later request;
   * sends the `notifications/initialized` the spec requires after a successful
     initialize — at the transport, because it is a protocol obligation of this
     transport and not something `MCPClient` should learn;
   * decodes **both** response shapes: a JSON body, or `text/event-stream` where
     the JSON-RPC response arrives in an SSE `data:` frame;
   * sends `MCP-Protocol-Version` on post-initialize requests, and terminates
     the session with a best-effort `DELETE` on close.
3. Errors keep the existing shapes (`MCPTransportError` / `MCPProtocolError`)
   so the dispatch layer, the audit line and the operator's mental model do not
   change.

### Phases 2–N (deferred)

* The **GET** listening stream (server-initiated requests and notifications).
  Nothing in ACC consumes server-initiated messages today, and adding a
  long-lived stream per server is a lifecycle question of its own.
* Resumability (`Last-Event-ID`) and session recovery after a server restart.
* OAuth as MCP specifies it for HTTP servers; the existing `api_key_env` /
  `auth: oauth` bearer path carries over unchanged.

## Impact

* **Affected code:** `acc/mcp/manifest.py` (the literal + validation),
  `acc/mcp/transports.py` (the class + dispatch), `docs/howto-mcp-sources.md`,
  `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** `tests/test_mcp_streamable_http.py` — the handshake and session-id
  echo, the initialized notification, JSON and SSE decoding, an error response,
  a missing session id, close/DELETE, and that `http` is untouched.
* **Backward compatibility:** additive. No existing manifest changes behaviour;
  `transport: http` keeps its exact wire format.

## What stays open after Phase 1

* Without the GET stream, a server that *only* pushes results (rather than
  answering the POST) will not work — no such server is in use here, and the
  failure is a clean timeout rather than a wrong answer.
* The AS-04 shim can be retired only once a real run has gone through this
  transport; until that is verified on lighthouse the shim stays in the
  scratch tree as the fallback.
