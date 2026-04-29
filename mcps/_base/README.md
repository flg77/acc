# mcps/ — Convention reference

An **MCP server** is an external process that speaks the
[Model Context Protocol](https://modelcontextprotocol.io/) — a
JSON-RPC 2.0 spec from Anthropic that exposes tools, resources, and
prompts to LLM clients.

Each entry under `mcps/` is **one local manifest pointing at one
remote server**.  The directory does NOT contain server code — only
the configuration ACC needs to consume the server (URL, auth, tool
allow/deny lists, governance hooks).

## Layout

| File | Required | Purpose |
|------|----------|---------|
| `mcp.yaml` | yes | Validated by `acc.mcp.MCPManifest`. |

`mcps/_base/mcp.yaml` provides field defaults that every per-server
manifest deep-merges over — same rule as roles/_base and skills/_base.

## Minimum manifest

```yaml
# mcps/echo_server/mcp.yaml
purpose:    "Local MCP echo server for smoke tests."
transport:  "http"
url:        "http://acc-mcp-echo:8080/rpc"
allowed_tools:
  - "echo"
  - "ping"
risk_level: "LOW"
```

## Loading

```python
from acc.mcp import MCPRegistry

reg = MCPRegistry()
reg.load_from()                          # reads $ACC_MCPS_ROOT or ./mcps
print(reg.list_server_ids())             # → ['echo_server', ...]

client = await reg.client("echo_server")
tools  = await client.list_tools()
result = await client.call_tool("echo", {"text": "hello"})
```

## Transports

| Transport | Status (Phase 4.2) | Notes |
|-----------|--------------------|-------|
| `http`   | implemented | JSON-RPC 2.0 over HTTP POST.  Production path. |
| `stdio`  | reserved    | Manifest validator accepts the value; the client raises `NotImplementedError` until the stdio transport lands in a follow-up PR. |

## Tool allow/deny lists

The manifest is the operator-side sandbox:

* `allowed_tools: []` (default) ⇒ allow every tool the server advertises.
* `allowed_tools: [echo, ping]` ⇒ only these two are reachable, even
  if the server offers more.
* `denied_tools: [shell.exec]` ⇒ blacklist applied **after**
  `allowed_tools`.  Useful when you want "everything except X".

Tools blocked by the manifest are filtered out of `list_tools()` AND
raise `MCPToolNotFoundError` from `call_tool()` — even if the LLM
hallucinates a name.

## Risk levels

Identical semantics to skills (LOW / MEDIUM / HIGH / CRITICAL).
Cat-A rule A-018 (Phase 4.3) blocks invocations whose risk level
exceeds the calling role's tolerance; CRITICAL also enqueues an
`OVERSIGHT_SUBMIT` request to the human-in-the-loop queue.

## Governance hooks

* `requires_actions` — labels the calling role's `allowed_actions`
  list must contain.  Example: `["call_external_api"]` for any
  outbound HTTP MCP server.
* `domain_id` — biological tag.  Pair with the role's
  `domain_receptors` so a `governance` arbiter can trace which cell
  populations have access to which symbionts.

## Excluded directories during discovery

`_base`, `TEMPLATE`, `__pycache__`.

See [`acc/mcp/manifest.py`](../../acc/mcp/manifest.py) for the
authoritative field list and validators.
