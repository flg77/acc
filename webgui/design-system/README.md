# ACC WebGUI design system — PatternFly 6 + the ACC patterns

Decision of 2026-09-21: the WebGUI is rebuilt on **PatternFly**, Red Hat's
default, so it reads as part of OpenShift / RHOAI — and looks the same on an
edge host. PatternFly supplies the mechanics. This folder holds only what
PatternFly has no word for, and the preview cards that specify it.

| File | What |
|---|---|
| `acc.css` | eight ACC tokens (all mapped onto PatternFly tokens — no colour of its own) and five patterns |
| `foundations/tokens.html` | the state vocabulary, environment colours, which PatternFly labels carry tier / risk / trust |
| `shell/environment-bar.html` | **where this surface runs** — cluster · standalone · viewer · "could not find out" |
| `shell/app-shell.html` | masthead + horizontal nav + bar at 900 px (a Showroom tab); the eight-question navigation |
| `patterns/declared-observed.html` | **a declared value beside the observed one** — every state of the Agentset table |
| `patterns/proposal-lifecycle.html` | **a change with a life** — asked → approved → applied → converged, refused, above the ceiling, changed elsewhere |
| `patterns/gated-action.html` | **an action that is not available says why** — enabled · disabled + reason · caveat · hidden |
| `components/table-states.html` | empty (and true) · refused read · stale · loading |

## Look at it

```bash
cd webgui
npm install
npm run ds:vendor        # copies PatternFly's CSS + fonts into design-system/vendor/ (generated, not committed)
```

Then open any `.html` in a browser. Dark theme: add `class="pf-v6-theme-dark"`
to `<html>` — nothing in `acc.css` needs to change.

## Sync to claude.ai/design

Each preview starts with `<!-- @dsCard group="…" -->`; that line is what the
Design System pane builds its cards from.

`/design-sync` is a **Claude Code slash command** — type it into a Claude Code
prompt, not into PowerShell, bash, or a shell on acc1. It runs on the machine
that holds these files, and it needs **your claude.ai login**: the cards are
uploaded into a design-system project under your account, and nobody else's
session can do that for you.

1. On the workstation, from `webgui/`: `npm install`, then `npm run ds:vendor`.
   The cards link `./vendor/patternfly.min.css`; without `vendor/` they upload
   unstyled.
2. Start Claude Code **in `webgui/design-system/`** (`cd webgui\design-system`,
   then `claude`) — the directory it is started in is the directory it may read
   uploads from. Or use an existing Claude Code session (the desktop app's Code
   tab works) and let it change into that directory.
3. Type `/design-sync`. If it says design access is missing, run `/design-login`
   once (it opens a browser for your claude.ai login), then repeat.
4. First time: choose **create new** and name the project (*ACC WebGUI*). It
   shows the exact file list it will write and where it reads them from — read
   it, approve it. Nothing is written before you approve.
5. Open claude.ai/design → the project → *Design System*: one card per preview,
   grouped by the `group="…"` of each file.

Later syncs are incremental — one component at a time, never a wholesale
replace. If your Claude Code does not know `/design-sync`, update it (it ships
with newer builds); nothing else in this folder depends on the sync.

## Rules

* **A page that needs a new pattern → the pattern lands here first**, as a
  preview card, then as a React component in `webgui/src/components/`. The
  card is the component's spec; nothing is drawn twice.
* **PatternFly first.** If PatternFly has the component, use it unchanged. A new
  class in `acc.css` needs a sentence in its header saying what PatternFly lacks.
* **No colour values.** Only PatternFly tokens, so every theme follows.
* **Reasons are text.** A disabled control shows why beside it, from
  `/api/environment` — never only a tooltip, never a host command in a cluster.
* **900 px and framed.** Every card must hold at the width of a Showroom tab,
  keyboard-reachable, with PatternFly's visible focus.
* **Synthetic data only** in the cards: the workshop's namespaces and roles.
  Never keys, tokens, customer names.

Some cards show the *target*, not today's runtime: `spec.ui.control` and
proposals that patch a CR are KW-15 (vault `20-backlog/k8s-webgui/KW-00`).
