# Tasks: Managing MCP servers at runtime

**Change ID:** 20260817-mcp-server-management
**Branch:** `feat/mcp-server-management`

---

## Phase 1 — Inspect and test

- [x] `[1]` `mcp list` showing source (bundled / package / operator-added)
- [x] `[2]` `mcp test` distinguishing unreachable / auth / listing failures
- [x] `[3]` Effective-permission view: host ∩ role, showing which side denied

## Phase 2 — Manage

- [x] `[4]` `mcp add` / `mcp remove` for operator-added servers
- [x] `[5]` Per-tool enable/disable composing with role declarations
- [x] `[6]` Decide temporary-vs-permanent override default (open question 2)
      *(PERMANENT, and written to a file the operator can read. A temporary
      override that expires silently means a tool comes back without anyone
      deciding it should -- the opposite of what a gate is for. `mcp enable`
      is the explicit reversal.)*

## Phase 3 — Guarantees

- [x] `[7]` Test: an operator override cannot grant a role more than it declared
- [x] `[8]` Test: package-installed servers keep their trust rules

