# Tasks: Managing MCP servers at runtime

**Change ID:** 20260817-mcp-server-management
**Branch:** `feat/mcp-server-management`

---

## Phase 1 — Inspect and test

- [ ] `[1]` `mcp list` showing source (bundled / package / operator-added)
- [ ] `[2]` `mcp test` distinguishing unreachable / auth / listing failures
- [ ] `[3]` Effective-permission view: host ∩ role, showing which side denied

## Phase 2 — Manage

- [ ] `[4]` `mcp add` / `mcp remove` for operator-added servers
- [ ] `[5]` Per-tool enable/disable composing with role declarations
- [ ] `[6]` Decide temporary-vs-permanent override default (open question 2)

## Phase 3 — Guarantees

- [ ] `[7]` Test: an operator override cannot grant a role more than it declared
- [ ] `[8]` Test: package-installed servers keep their trust rules

