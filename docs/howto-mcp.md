# How to integrate an MCP server with ACC

The [Model Context Protocol](https://modelcontextprotocol.io/) is
Anthropic's open spec for connecting LLMs to external tool servers.
An **MCP server** is an out-of-process service that advertises tools,
resources, and prompts to clients via JSON-RPC 2.0.

In ACC's biological metaphor, MCP servers are **symbiotic bacteria**:
external organisms the cell hosts but does not synthesise.  Each one
has its own life-cycle, its own governance contract, and is admitted
through the cell wall (`role.allowed_mcps`) one at a time.

This guide walks through registering an MCP server with ACC, opting
a role into using it, and driving tool calls from the LLM.

## Prerequisites

You need an MCP server reachable at a known URL.  For the rest of
this doc we assume:

* Server is running at `http://acc-mcp-echo:8080/rpc`.
* It speaks JSON-RPC 2.0 with at minimum the three methods ACC
  exercises: `initialize`, `tools/list`, `tools/call`.
* It advertises one tool named `echo` that returns its `text`
  argument back.

If you don't have a server yet, write one with the
[`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk)
or any HTTP framework that can serialise the spec.  ACC ships only
the **client** half.

## 1. Directory layout

ACC discovers MCP server *manifests* under `mcps/`.  A manifest is a
local declaration of how to reach one server, NOT the server code:

```
mcps/
├── _base/
│   ├── mcp.yaml            # defaults — already exists
│   └── README.md
└── my_echo/                # ← your new manifest
    └── mcp.yaml
```

The directory name must equal the `server_id` in the manifest
(`lowercase_snake_case`).

## 2. Manifest

`mcps/my_echo/mcp.yaml`:

```yaml
purpose:        "Local echo MCP server for smoke tests."
version:        "0.1.0"

transport:      "http"
url:            "http://acc-mcp-echo:8080/rpc"
timeout_s:      10
api_key_env:    ""              # set to e.g. "MY_ECHO_TOKEN" for bearer auth

allowed_tools:
  - "echo"
denied_tools:   []

requires_actions: []
risk_level:     "LOW"
domain_id:      "diagnostic"
tags:           ["test"]

description: |
  Round-trips a single 'echo' tool.  Used as a smoke-test target.
```

### Field cheatsheet

| Field | Role |
|-------|------|
| `transport`        | `http` (implemented) or `stdio` (reserved for a follow-up PR). |
| `url`              | Required for `http`.  Full base URL of the JSON-RPC endpoint. |
| `api_key_env`      | Name of the env var carrying a bearer token; sent as `Authorization: Bearer <value>`.  Empty = unauthenticated. |
| `allowed_tools`    | Operator-side sandbox.  Empty = "allow everything the server advertises"; non-empty = strict whitelist.  Tools blocked here are filtered out of `list_tools()` and raise `MCPToolNotFoundError` from `call_tool`. |
| `denied_tools`     | Applied AFTER `allowed_tools`.  Useful when you want "everything except shell.exec". |
| `requires_actions` | Composes with `role.allowed_actions` — Cat-A A-018 denies if any are missing. |
| `risk_level`       | `LOW | MEDIUM | HIGH | CRITICAL`.  A-018 enforces the calling role's `max_mcp_risk_level` ceiling. |

## 3. Wire it into a role

Edit `roles/<role_name>/role.yaml`:

```yaml
role_definition:
  # ... existing fields ...

  allowed_mcps:
    - echo_server
    - my_echo               # ← add here
  default_mcps:
    - my_echo               # ← advertised in the LLM system prompt
  max_mcp_risk_level: "MEDIUM"
```

Empty `allowed_mcps` denies every MCP server (fail-closed default).

## 4. Verify in the TUI

Boot the TUI, navigate to **Ecosystem**, confirm the MCP SERVERS
table shows your new row:

| Server      | Transport | Risk  | Tools |
|-------------|-----------|-------|-------|
| echo_server | http      | LOW   | echo  |
| my_echo     | http      | LOW   | echo  |

Headless verification:

```python
from acc.mcp import MCPRegistry
reg = MCPRegistry()
reg.load_from("mcps")
print(reg.list_server_ids())          # should include 'my_echo'

import asyncio
async def main():
    client = await reg.client("my_echo")
    print(await client.list_tools())  # → [{'name': 'echo', ...}]
    print(await client.call_tool("echo", {"text": "ping"}))
    await client.close()
asyncio.run(main())
```

## 5. Drive it from the LLM

In the agent task loop, the LLM emits an `[MCP:...]` marker:

```
I need to verify connectivity:

[MCP: my_echo.echo {"text": "round trip"}]

The result above confirms the server is reachable.
```

`acc.capability_dispatch.parse_invocations` extracts the marker;
`dispatch_invocations` runs it through
`CognitiveCore.invoke_mcp_tool` (which fires Cat-A A-018) and folds
the result into `TASK_COMPLETE.invocations`.

### Marker grammar

```
[MCP: <server_id>.<tool_name> {<json args>}]
[MCP: <server_id>.<tool_name>]               # args default to {}
```

* `<server_id>` matches `[a-z][a-z0-9_]*`.
* `<tool_name>` allows dots so nested namespacing (`fs.read`) is
  preserved end-to-end.
* JSON payload must be a single-line object literal.

## Cat-A A-018 — what gets enforced

`acc/governance_capabilities.py::CapabilityGuard.check_mcp_invocation`
runs four checks in order on every invocation:

1. **Server whitelist** — `server_id` must be in `role.allowed_mcps`.
2. **Required actions** — every entry in `manifest.requires_actions`
   must be in `role.allowed_actions`.
3. **Risk ceiling** — `manifest.risk_level` must rank at or below
   `role.max_mcp_risk_level`.
4. **Manifest tool gate** — `manifest.is_tool_allowed(tool_name)`
   must return True (re-checks `allowed_tools` / `denied_tools`).

Any failure raises `MCPToolNotFoundError` in enforce mode (when
`compliance.cat_a_enforce=True`), or logs a `would block` warning
and proceeds in observe mode.

## CRITICAL invocations and human oversight

A manifest with `risk_level: CRITICAL` always sets
`CapabilityDecision.needs_oversight=True`.  The agent task loop is
expected to enqueue an `OVERSIGHT_SUBMIT` request to the arbiter's
`HumanOversightQueue` before the call proceeds.

The current dispatcher logs the request at INFO and proceeds; a
future PR will add blocking mode for CRITICAL invocations.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Server not in `Ecosystem` table | `mcps/<id>/mcp.yaml` missing or fails validation. |
| `MCPConnectionError` on first call | URL unreachable, or server's `initialize` handshake failed.  Check `acc-agent` logs at INFO. |
| `MCPToolNotFoundError: A-018 blocked` | `server_id` not in `role.allowed_mcps`, OR the tool is not in `manifest.allowed_tools`, OR the risk ceiling failed, OR a required action is missing. |
| `MCPProtocolError: id mismatch` | Server returned a JSON-RPC response with the wrong `id` field — usually a multiplexing bug on the server side. |
| `MCPTransportError: HTTP 5xx` | Server is up but errored.  ACC retries are NOT automatic — the LLM is expected to re-emit the marker if it wants to retry. |
| Bearer auth missing | `api_key_env` is set but the env var is empty at agent-startup time.  ACC logs a warning and sends the request unauthenticated. |

## When NOT to use an MCP server

If the capability is:

* **Stateless and pure-Python** — write a Skill instead
  (see [`docs/howto-skills.md`](howto-skills.md)).  In-process
  invocation is microseconds; an HTTP round-trip is milliseconds.
* **Specific to one role and not reusable** — embed the logic in
  the role's prompt or a dedicated agent rather than spinning up an
  external service.

MCP servers are the right call when the capability is owned by
another team, runs in another language, holds long-lived state
(database connections, credentials), or already exists as a
standalone product.

## See also

* [`acc/mcp/__init__.py`](../acc/mcp/__init__.py) — public client API.
* [`acc/mcp/manifest.py`](../acc/mcp/manifest.py) — full manifest
  field reference.
* [`acc/mcp/client.py`](../acc/mcp/client.py) — JSON-RPC envelope
  and error mapping.
* [Model Context Protocol spec](https://spec.modelcontextprotocol.io)
  — the underlying wire protocol.
* [`docs/howto-skills.md`](howto-skills.md) — sister doc for the
  in-house Skills surface.
