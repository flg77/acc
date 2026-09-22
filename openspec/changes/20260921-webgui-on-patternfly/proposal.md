# 20260921-webgui-on-patternfly — proposal

## Why

The operator answered the open design questions on 2026-09-21 (vault
`20-backlog/k8s-webgui/KW-00` §0, `design-rewrite` §5):

> *"Yes, use PatternFly as it is Red Hat's default. Same everywhere — full
> control over the environments, edge but also OCP/RHOAI — no lies!
> Everything that lives in a CR is detected and updateable while also visible
> from within RHOAI → ACC deployment creates the RHOAI project, enables MLflow
> and allows role edit from artefacts that are available on RHOAI; this
> includes models, skills, MCPs, permission management etc."*

and: the WebGUI **is** the primary control surface on a cluster; the SPA is
**rewritten**, not evolved.

The SPA today is a working prototype — 2 508 lines, 1 259 of them in one
`webgui/src/screens.tsx`, one 149-line hand-written stylesheet, no component
library, `react-router-dom` declared and unused, seventeen nav buttons. It has
no design language to preserve. The design changes also mandate functional
ones (a write path, RHOAI bootstrap, models / MCPs / permissions from the
platform) — those are separate changes; this one lays the ground they are
built on, so the first new screen (the Agentset page) is born in the new
design instead of being retrofitted.

## What changes

### Phase 1 (this ship) — the design system, as code

* `webgui/design-system/` — **PatternFly 6 for the mechanics, five ACC
  patterns on top** (`acc.css`): the ACC state vocabulary as tokens, the
  environment bar, declared/observed, the proposal lifecycle, the
  capability-gated action. Every colour is a PatternFly token — light, dark
  and high-contrast follow the theme class with nothing to maintain.
* Seven preview cards, each starting with `<!-- @dsCard group="…" -->` so
  `/design-sync` can publish them to a claude.ai/design design-system project:
  tokens · environment bar · app shell at 900 px with an eight-item
  navigation · declared/observed (every state of the Agentset table) ·
  proposal lifecycle (waiting, converging, refused, above the ceiling,
  changed elsewhere) · gated action · table states.
* `@patternfly/patternfly` 6.6.1 as a pinned dev dependency;
  `npm run ds:vendor` copies its stylesheet and fonts next to the cards
  (`design-system/vendor/`, generated, not committed) so they render offline
  and travel with a sync — no CDN.
* Nothing in `webgui/src/` changes. The running SPA is untouched.

### Phase 2 (stacked, this branch) — the shell and the first screen

* **Shell** on `@patternfly/react-core`: masthead with the eight-item
  navigation, the environment bar, hash routes, tabs per section. Every one of
  the seventeen old screens is still reachable — they run inside a `.legacy`
  wrapper whose stylesheet is scoped and mapped onto PatternFly tokens, and
  each moves out as it is rebuilt.
* **Agentset page** (vault KW-03): declared beside running, from the design
  system's patterns, over one comparison rule in Python
  (`acc.deployment.compare`) that the TUI will share; `GET /api/agentset`,
  `GET /api/whoami`.

### Phases 3–N (deferred — each its own change)

* Rebuild the remaining screens on PatternFly, one per change, starting with
  Overview, Work and Packages; `screens.tsx` dissolves.
* **The write path** (KW-15): `<corpus>-ui` Role gains write verbs behind a
  corpus switch; a change is a person's proposal → approval → patch →
  converged. Needs the operator's answer on two writers (KT-00 Q1).
* **RHOAI**: operator-side MLflow enablement incl. read access for the UI
  ServiceAccount (KW-17), models from the platform incl. MaaS and an
  OpenAI-compatible backend in the CRD (KW-18), MCP/skill sources (KW-19,
  research first — RHOAI 3.5 on bb3 has no `MCPServer` kind), permissions
  from project RBAC (KW-20), links in and out of the RHOAI dashboard (KW-21).

## Impact

* **Affected code:** `webgui/design-system/**` (new), `webgui/scripts/ds-vendor.mjs`
  (new), `webgui/package.json` + lock (one dev dependency, one script),
  `webgui/.gitignore`.
* **New env knobs:** none.
* **Tests:** none — no runtime code. The cards were rendered in a browser;
  every PatternFly class they use exists in 6.6.1 (checked against the loaded
  stylesheet).
* **Backward compatibility:** the shipped SPA and its build are unchanged
  (`tsc --noEmit` clean; the image build runs `npm ci` and gains one unused
  dev package).

## What stays open after Phase 1

* The cards show the **target**: `spec.ui.control` and CR-patching proposals do
  not exist yet (KW-15). The README says so.
* The TUI shares the concepts (state vocabulary, declared/observed, reasons as
  text), not the tokens.
* `/design-sync` is run by the operator — it needs their claude.ai login.
