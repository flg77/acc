# 20260913-mcp-streamable-http — tasks

## Phase 1 (v0.17.5)

### 1.1 The manifest
- [ ] `MCPTransport` gains `streamable-http`
- [ ] Transport consistency: it requires `url`, like `http`

### 1.2 The transport
- [ ] `StreamableHTTPTransport` implementing `Transport`
- [ ] Capture `Mcp-Session-Id`, echo it on later requests
- [ ] Send `notifications/initialized` after a successful initialize
- [ ] Decode a JSON body **and** an SSE `data:` frame
- [ ] `MCP-Protocol-Version` header; best-effort `DELETE` on close
- [ ] Dispatch in `build_transport`

### 1.3 Docs
- [ ] `docs/howto-mcp-sources.md`: which transport to choose
- [ ] CHANGELOG entry

### Verification
- [ ] `tests/test_mcp_streamable_http.py` green
- [ ] Existing MCP suites green (`http` unchanged)
- [ ] Full sweep, classified against origin/main
- [ ] **lighthouse: the AS-04 cell reaches midojo's fake MCP with the shim
      stopped** — the fixture retires

## Phase 2 (deferred)
- [ ] The GET listening stream
- [ ] Resumability / session recovery
