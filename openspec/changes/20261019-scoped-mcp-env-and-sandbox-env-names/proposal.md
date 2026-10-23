# 20261019-scoped-mcp-env-and-sandbox-env-names — proposal

Two findings from the assistant-autonomy review (vault proposal 060, §1), fixed
on top of the 058 stack (`feat/058-p5-standing-grants`) so the mandatory
sandbox ships with correct settings.

## Why

### 1. stdio MCP servers inherit the agent's whole environment

`acc/mcp/transports.py` `StdioTransport.start()`:

```python
env = dict(os.environ)
env.update(self._manifest.env or {})
```

**What it exposes.** Every stdio server gets every variable the agent has:
- LLM keys;
- NATS and Redis settings;
- MLflow and OAuth client settings;
- tokens meant for other servers.

Seven bundled MCPs use stdio, mostly third-party `npx` / `uvx` packages. The
reference MCP Python SDK passes only a short safe list plus what you declare.

**A related bug.** The manifests declare their credential with an empty value:
- `mcps/github_api/mcp.yaml`: `GITHUB_PERSONAL_ACCESS_TOKEN: ""`, commented
  "Set on the host before bringing the agent up";
- `mcps/semantic_scholar/mcp.yaml`: `S2_API_KEY: ""`.

`env.update` applies these after the inherited environment, so the empty string
**overwrites** the host's token. The server starts without it.

### 2. The sandbox settings are named differently in different places

- `acc/sandbox/runner.py` reads `ACC_OPENSHELL_BIN`; `acc/configschema.py`
  lists `OPENSHELL_BIN`, which nothing reads.
- `ACC_SANDBOX_REQUIRED` (058 Phase 3) and `ACC_OPENSHELL_BIN` are not in the
  schema, so `acc config check` reports the correct names as unknown keys.
- `configschema.REQUIRES` keys the gateway check on `ACC_SANDBOX_ENABLED`, but
  the runner turns the sandbox on when `ACC_SANDBOX_NAME` is set.
- `acc/preflight.py` counts `ACC_SANDBOX_ENABLED` as "on" when the key merely
  appears in the env file. The runner treats `ACC_SANDBOX_ENABLED=0` as off,
  so `acc doctor` reports a false BROKEN.

## What changes

1. **Scoped stdio environment.** A stdio server's environment is built from
   three sources:
   - a base of non-secret process variables: `PATH`, `HOME`, user and locale,
     temp dirs, the proxy and CA-bundle variables, and the Windows
     process essentials;
   - plus the manifest's `env:`;
   - nothing else from the agent.

   In `env:`, an **empty value means "pass this name through from the agent's
   environment"** (the manifests' documented intent), and a non-empty value is
   a literal. A declared name that the agent does not have is left out rather
   than set to "".
2. **One source for the sandbox names.**
   - `acc/configschema.py` and `acc/preflight.py` use the runner's `ENV_*`
     constants.
   - The schema lists `ACC_SANDBOX_REQUIRED` and `ACC_OPENSHELL_BIN`, and drops
     `OPENSHELL_BIN`, which never worked.
   - `REQUIRES` keys on `ACC_SANDBOX_NAME`.
   - Preflight decides "on" with `SandboxConfig.from_env()` over the process
     environment merged with the env file.

## Impact
- **Affected code:**
  - `acc/mcp/transports.py`, `acc/mcp/manifest.py` (docstring);
  - `acc/configschema.py`, `acc/preflight.py`;
  - `mcps/github_api/mcp.yaml`, `mcps/semantic_scholar/mcp.yaml` (comments).
- **New env knobs:** none. `OPENSHELL_BIN` leaves the schema; it was never
  read.
- **Tests:**
  - a stdio server cannot see an undeclared secret;
  - an empty declared value passes the agent's value through;
  - a declared name the agent lacks is absent, not "";
  - a literal value is passed;
  - every runner `ENV_*` name is in the schema;
  - `REQUIRES` names the gateway for `ACC_SANDBOX_NAME`;
  - preflight treats `ACC_SANDBOX_ENABLED=0` in the env file as off.
- **Backward compatibility:** a stdio server that relied on an undeclared
  inherited variable stops seeing it. The fix is to declare the name in its
  manifest (empty value). The bundled manifests already declare their
  credentials.
