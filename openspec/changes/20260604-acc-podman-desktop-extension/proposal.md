# ACC Podman Desktop Extension (Stage 2.5)

## Why

Stage 2 of the role-ecosystem strategy targets three discovery
surfaces for `@acc/*` packs:

1. `acc-tui` Marketplace pane (shipped 2.4).
2. `acc-webgui` Roles tab (shipped 2.4).
3. **Podman Desktop extension** (this proposal).

Podman Desktop is the canonical GUI for the container runtime ACC
relies on, and Red Hat has been pushing it as the standard
edge/desktop tool for the Hummingbird agentic-OS workflow.  An
extension there reaches operators who never open a terminal.

## What

A separate TypeScript repo at `flg77/acc-podman-desktop` shipping
two Phase-1 commands:

* **ACC: Open Role Marketplace** — fetches the canonical catalog
  `index.json` and lists available `@acc/*` packs in a webview.
* **ACC: Install Family Pack** — invokes `acc-pkg install
  <name>@<version>` against the host.  Operator must have `acc-pkg`
  on PATH; the extension never embeds package logic.

Configuration block exposes `acc.catalog.url` (default
`https://flg77.github.io/acc-ecosystem`) so operators can point at
mirrors / private hubs.

Phase 2 (not in this proposal): inline tracing pane parity with
`acc-webgui`, surfaced as a Podman Desktop view.

## Repo / bootstrap

This repo ships **`tools/bootstrap-podman-desktop.sh`** — a one-shot
script the operator runs once to scaffold the new repo:

```bash
tools/bootstrap-podman-desktop.sh
# (operator-authenticated gh CLI required)
```

Creates `flg77/acc-podman-desktop` public + Apache 2.0, scaffolds
`package.json`, `tsconfig.json`, `src/extension.ts`, opens a
bootstrap PR.

## Trust boundary

The extension is a **thin shim**.  All security-critical paths
(cosign verify, EC policy, signed registry write) stay in the
`acc-pkg` CLI inside the host.  Podman Desktop just dispatches.
This keeps the trust surface auditable.

## Non-goals

* Replacing `acc-tui` or `acc-webgui`.
* Embedded cosign / EC verification (delegated to `acc-pkg`).
* Multi-collective orchestration UI.

## Open questions (deferred)

* Publishing to the Podman Desktop marketplace requires Red Hat /
  Podman Desktop team review.  Bootstrap repo is sufficient for
  internal use; marketplace listing is Phase 1.5.
* Whether to ship a bundled `acc-pkg` binary or require host
  install.  Bootstrap = require host install; bundled distribution
  is Phase 2.
