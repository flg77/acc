# Proposal: Managing MCP servers at runtime

**Change ID:** 20260817-mcp-server-management
**Date:** 2026-08-17
**Status:** Implemented
**Author:** flg

---

## Problem Statement

MCP servers are deployed but not managed. They arrive as bundled definitions
or inside signed packages — which is a genuinely strong distribution story — and
from that point an operator has almost no control. There is no way to test whether
a server is reachable, no way to disable a single misbehaving tool without
removing the whole server, and no way to add one without editing files.

The consequence appears during diagnosis: when a tool call fails, isolating
whether the fault is the server, the network, the credentials or the agent's use
of it requires reproducing the call by hand. The product knows how to reach the
server and does not expose that knowledge.

## Current Behavior

Bundled server definitions plus whatever packages install; the registry
records what is present. There is no management command, no connection test, no
per-tool control.

## Desired Behavior

    acc-cli mcp list
    acc-cli mcp test <name>
    acc-cli mcp add <name> ...
    acc-cli mcp remove <name>
    acc-cli mcp tools <name> [--enable/--disable <tool>]

`test` is the highest-value half and could ship alone: connect, list tools,
report the failure precisely when it fails.

Per-tool enable/disable needs care because it overlaps an existing concept. Roles
already declare which capabilities they may use, and that declaration is governed.
A host-level per-tool toggle must **compose with** that rather than shadow it —
the effective permission being the intersection, with both visible, so an operator
can tell whether a tool is unavailable because the host disabled it or because the
role never had it.

## Success Criteria

- `test` distinguishes unreachable, authentication failure, and tool-listing
  failure with a message naming which.
- Disabling a tool at host level prevents its use without editing a role.
- The effective permission (host ∩ role) is inspectable, and it is clear which
  side denied.
- Adding a server does not require editing files by hand.
- Signed-package-installed servers remain governed by the package trust rules.

## Scope

**In scope**

- List, test, add, remove, and per-tool enable/disable.
- Composition with role capability declarations, with visibility of both.
- Precise diagnosis in `test`.

**Out of scope**

- Bypassing package signing to install a server.
- Replacing package-based distribution with ad-hoc installs.
- A server marketplace or catalogue browser.

## Implementation options

**A. Host-level configuration file** listing servers and per-tool state, merged
with role declarations at resolution. Simple, inspectable, one more config
surface — which the configuration-schema work should absorb.

**B. Registry-backed state** alongside installed packages. Keeps everything about
a server in one place; muddies the line between what a package declared and what
an operator overrode.

**C. Role-only control** (no host layer) — force every change through a role
update. Governed and honest, and far too heavy for "this server is flapping,
disable it for an hour".

A is the recommendation, with the override clearly marked as an operator layer
distinct from what packages and roles declare.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Does a per-tool toggle live at the host (operator convenience) or in the role
   (governed, signed)? They imply different trust levels and the answer decides
   the design.
2. Should a host-level disable be temporary by default, so a forgotten override
   does not silently persist?
3. Does `test` execute a tool, or only connect and list? Executing is a better
   test and has side effects.

## Assumptions

- Package-installed servers keep their signing and trust guarantees.
- Role capability declarations remain authoritative for what a role may use.
