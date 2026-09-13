# 20260912-mcp-tools-in-the-prompt — tasks

## Phase 1 (v0.17.4) — the tools reach the prompt

### 1.1 The manifest carries its tools
- [ ] `MCPToolSpec` — `name` (required), `summary` (default ""), `args` (list[str], default [])
- [ ] `MCPManifest.tools: list[MCPToolSpec]`, default empty
- [ ] Validator: a declared tool must be permitted — in `allowed_tools` when
      that list is non-empty, never in `denied_tools`
- [ ] Validator: no duplicate tool names

### 1.2 The prompt block renders them
- [ ] `advertised_tool_lines(manifest)` — declared `tools` first, else
      `allowed_tools` names, else nothing
- [ ] Cap at `MAX_ADVERTISED_TOOLS` (12) with `… and N more`
- [ ] Wire into the "Available MCP servers" block in `cognitive_core.py`,
      keeping it inside the cacheable prefix

### 1.3 The example + the docs
- [ ] `mcps/echo_server/mcp.yaml` declares its `echo` tool
- [ ] `docs/howto-mcp-sources.md` documents the field
- [ ] CHANGELOG entry under `[Unreleased]`

### Verification
- [ ] `tests/test_mcp_tool_advertisement.py` green
- [ ] Existing MCP + cognitive-core suites green (no prompt regression for a
      manifest that declares neither tools nor an allowlist)
- [ ] Full sweep, classified against origin/main
- [ ] Live check on lighthouse: the AS-04 fixture manifest **without** the
      purpose-string hack — the model must call `get_weather`, not `get_current`

## Phase 2 (deferred)
- [ ] Live `tools/list` enrichment at registry load, intersected with the
      allowlist, cached, with a stale story
- [ ] Argument types + required/optional
