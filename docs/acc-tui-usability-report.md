# ACC TUI usability report — WYSIWYG-for-CLI landscape + cross-surface proposals

*Drafted 2026-05-14.  Operator-local; gitignored.  Not a public RFC —
this is an opinionated working document that informs the next round
of Obsidian proposals (008+).*

## Why this report exists

Operator pain on the editing surface keeps surfacing after every TUI
review.  Proposal 003 hardened the *navigation* parts of the TUI
(prompt cancel, role-detail rendering, file-watcher, Configuration
pane).  What it didn't fix is the underlying truth that **every field
where the operator types real content is either a one-line `Input` or
a plain-text `TextArea`** — and neither of those is a fit for editing
YAML role definitions, multi-paragraph role.md narrative, or a JSON
Cat-B override blob.

Proposal 007 ("in-pane role editing") was deferred at the time with
the rationale *"editing role.yaml + role.md inside Textual without a
real editor widget is a usability trap — let operators edit in their
normal editor first."*  That was the right call given the choices then
on the table.  Three things have changed:

1. Textual 0.62+ ships `TextArea` with **tree-sitter** syntax
   highlighting — turning it on is a single `language=` kwarg.
2. The `acc-tui` container already exposes `ACC_TUI_WEB_PORT` as a
   dormant WebBridge feature.  We can serve a real browser-side editor
   (Monaco) on demand without polluting the Textual canvas.
3. The Podman Desktop extension (webview-capable) and the OpenShift
   operator (kubebuilder-schema-driven console forms) handle the
   **same logical role definition** with completely different storage
   and UX.  Any TUI-only fix leaves two surfaces drifting.

The point of this report is to lay out the WYSIWYG-for-CLI landscape,
catalogue the realistic options, and propose eight cross-surface
improvements.  Not every proposal needs to land — but the operator
should pick a coherent path across TUI / PD / RHOAI rather than
patching one surface at a time.

---

## 1. Findings from the editing-surface audit

| Surface | Editable widgets | Multi-line? | Syntax-aware? |
|---|---|---|---|
| Nucleus / Infuse (`acc/tui/screens/infuse.py` L94–276) | 6× `Input`, 2× `TextArea`, 2× `Select` | Purpose + seed-context only | No |
| Prompt (`acc/tui/screens/prompt.py` L228–263) | 1× `Input`, 1× `TextArea`, 1× `Select` | Prompt body only | No |
| Ecosystem (`acc/tui/screens/ecosystem.py` L400+) | 1× `Input` (filter only) | — | — |
| Configuration (PR-4 in 003) | 0 editable fields today | — | — |

**15 widgets total.  Two are multi-line.  Zero have syntax
highlighting.**  Comma-delimited fields (`task_types`,
`allowed_actions`, `domain_receptors`) hide structure behind a
single-line `Input` — operators routinely hit the 80-col wrap point
when authoring a real role.

The repo already contains the **skeleton of an external-editor
pathway** at `acc/tui/screens/ecosystem.py` L153–196
(`_resolve_editor_command()`, `_spawn_editor()`).  Dead code today —
never called from any action handler.  This is a strong signal that
the previous design *anticipated* the limitation but stopped short of
wiring it up.

Beyond the TUI, the same logical content lives in three places at
once:

- **Files** — `roles/<id>/role.yaml`, `roles/<id>/role.md`,
  `acc-config.yaml`.  TUI source of truth.
- **CRDs** — `AgentCollective.spec.roleDefinition` at
  `operator/api/v1alpha1/agentcollective_types.go` L57–92.  OpenShift
  Web Console renders auto-generated forms from
  `+kubebuilder:validation:*` annotations.
- **NATS snapshots** — read-only observability feed consumed by the
  Podman Desktop extension's panels.  No edit path.

Zero bi-directional sync between files and CRDs.  The operator
manually re-applies whichever side they edited.

---

## 2. WYSIWYG-for-CLI landscape

I considered nine approaches.  Five are realistic; two are flat
rejections; two need framing as "yes, but not where you'd expect."

| Approach | Fit | Notes |
|---|---|---|
| **Textual `TextArea` + tree-sitter** | ✅ XS effort | Already shipped in the Textual version we use.  YAML / Markdown / JSON / Python grammars available.  Bracket matching, paren-balance, theme-aware colours.  Wins on price/perf. |
| **External `$EDITOR` / `$VISUAL` invocation** | ✅ S effort | The "operator already has nvim/code/helix configured — let them use it" play.  Skeleton exists; needs an action handler.  File-watcher (PR-3, landed) covers the post-save reload. |
| **Field-level reactive validators** | ✅ S effort | `acc/config.py` already defines Pydantic models that *would* catch bad input.  We just don't surface the errors to the UI.  Textual ships a `Validator` protocol; one adapter bridges the two. |
| **Browser-side Monaco via WebBridge** | ⚠️ M effort | The TUI container already opens `ACC_TUI_WEB_PORT` (currently `0` = disabled).  Serve Monaco from a `/edit/role/<id>` route; operator opens in their normal browser when serious authoring is needed.  Same files the TUI watches, so changes round-trip automatically. |
| **OpenVSCode Server companion container** | ✅ M effort | Add `gitpod/openvscode-server:latest` to `container/production/podman-compose.yml` under an opt-in `--profile editor`.  Mount the repo root.  The operator gets **real VS Code** in the browser — extensions, themes, IntelliSense for YAML/Markdown — without ACC having to maintain a Monaco bundle.  *This is the most honest answer to "VS Code plugin within our TUI."* |
| **PD-extension Monaco panel** | ✅ M effort | Podman Desktop extensions get a full webview.  Drop a Monaco-based role editor into the PD UI; talks to the local filesystem via the extension's `fs` API.  Closes the PD-extension editing gap.  Lives in `flg77/acc-podman-desktop`. |
| **OpenShift Web Console form-driven CRD edit** | ✅ XS effort (docs only) | Already works — `+kubebuilder:validation:*` annotations on `AgentCollective` / `AgentCorpus` produce console forms.  Operator just doesn't know.  Document with a screenshot. |
| **Embedded terminal-side editor (`micro` / `helix` inside Textual)** | ❌ reject | Textual can't host another TUI inside its canvas without breaking input routing.  Tried and abandoned upstream multiple times. |
| **Literal "VS Code plugin within TUI"** | ❌ reject | VS Code's renderer is Electron / Chromium.  Cannot embed inside a terminal.  The OpenVSCode Server option (above) is the practical equivalent. |

**Key insight:** the cheapest wins (P1–P3 below) are all already
*possible* with code that exists in the repo today.  The bigger bets
(P4–P6) all converge on Monaco — once via WebBridge, once via a
sibling container, once via PD's webview.  Worth picking *one* Monaco
delivery vehicle rather than building two.

---

## 3. Proposals

### P1 — Wire `TextArea` syntax highlighting *(XS, no deps)*

Set `language="markdown"` on the seed-context TextArea
(`acc/tui/screens/infuse.py` L151), `language="markdown"` on the
prompt body (`acc/tui/screens/prompt.py` L258).  If we extend Nucleus
to expose a raw role.yaml override (proposal 007 territory), use
`language="yaml"` there.  Total diff: ~4 lines.

Visible win: instant syntax colouring on every multi-line field.
Operators stop typing into a featureless grey block.

### P2 — Wire the dormant `_spawn_editor()` *(S, depends on PR-3 — landed)*

Add an `E` keybinding on the Ecosystem role-detail pane.  When pressed,
call `_spawn_editor()` (already implemented at
`acc/tui/screens/ecosystem.py` L153–196) with the selected role's
`role.yaml` or `role.md`.  PR-3's file-watcher picks up the change
when the editor exits.  Suspend the Textual render loop while the
editor runs (Textual's `app.suspend()` context manager).

Visible win: pressing `E` drops the operator into nvim/code/helix on
the file they're looking at — no copy-paste, no context switch.

### P3 — Field-level validators *(S, depends on nothing)*

New `acc/tui/util/pydantic_validator.py`: adapter that takes a
Pydantic model + field name and returns a `textual.validation.Validator`
subclass calling the model's `validate_assignment` machinery.  Bind
the result to each `Input` in `infuse.py`'s field definitions.  Render
the first error returned by `ValidationError.errors()` underneath the
field in red.

Visible win: typing an invalid `domain_id` (e.g. with spaces) gets
immediate feedback rather than failing at infusion time.

### P4 — Optional WebBridge editor mode *(M, depends on P1–P3)*

Extend the TUI's existing WebBridge plumbing (gated by
`ACC_TUI_WEB_PORT > 0`).  Add routes:

- `GET /edit/role/<id>` → HTML shell loading Monaco, prefilled with
  the role's yaml + markdown.
- `POST /edit/role/<id>` → writes back atomically.
- `GET /edit/config` → same for `acc-config.yaml`.

Reuse P3's Pydantic validators server-side (they're the same models
the Textual UI binds against — single source of validation truth).
Monaco config: YAML + Markdown language services, vendored or
CDN-fetched.

Operator workflow: TUI Configuration screen shows a "Edit in browser"
button.  Click → opens `http://localhost:8765/edit/role/<id>`.  Save
in browser; TUI's file-watcher refreshes the detail pane within
~500ms.

Caveat: this adds ~2 MB of Monaco to the container image (or a
network dep at first paint if CDN'd).  P5 below is the simpler
escape hatch.

### P5 — OpenVSCode Server companion container *(M, independent)*

Add to `container/production/podman-compose.yml`:

```yaml
  acc-editor:
    profiles: [editor]
    image: gitpod/openvscode-server:latest
    container_name: acc-editor
    ports:
      - "3000:3000"
    volumes:
      - ../..:/home/workspace:U,z
    command: ["--host", "0.0.0.0", "--without-connection-token"]
    networks: [acc-net]
```

Operator runs `./acc-deploy.sh up --profile editor`; opens
`http://localhost:3000`; gets a fully-featured VS Code in the browser
with the entire repo mounted.  YAML extension, Markdown preview,
GitLens — all the operator's normal extensions work.

The TUI's file-watcher sees every change.  No code in ACC.  This is
**P4's competitor and probably its better-priced replacement**, unless
we specifically want to embed validation + role-aware UX (P4) rather
than just give the operator a great generic editor (P5).

Recommendation: start with **P5**, only build **P4** if operators
report wanting role-aware editing affordances beyond what stock VS
Code provides.

### P6 — PD extension: Monaco role editor panel *(M, separate repo)*

In `flg77/acc-podman-desktop`, add a new panel "Roles" with:

- Tree view of `roles/<id>/` on the host filesystem.
- Monaco editor instance in the right-hand pane.
- Same Pydantic validators as P3/P4 (vendor the `acc.config` schema
  as TypeScript types, or call out to a local validation endpoint).
- "Apply infusion" button that publishes the same NATS message the
  TUI's Nucleus screen does.

Scope lives in the PD repo; this report flags it but doesn't carry
the implementation.

### P7 — Bi-directional file ↔ CRD sync *(L, proposal 008 candidate)*

The convergence story.  Today:

```
roles/<id>/role.yaml  ──▶ TUI (Textual loads, edits, writes)
                      ──▶ acc-agent-* (loads via role_loader)

AgentCollective CR    ──▶ operator reconciler ──▶ ConfigMap ──▶ pod mount
```

No arrows go *up*.  Edit in OpenShift Web Console → file on disk
unchanged.  Edit `role.yaml` → CRD unchanged.

Add a "file-mirror" controller in
`operator/internal/controller/agentcollective_controller.go`: watch
`roles/<id>/role.yaml` (when running in modes that have filesystem
access — i.e., not vanilla OpenShift), patch the CRD when files
change.  Conversely, give `acc/role_loader.py` a CRD-write path
(behind a new `role_source: files | crd | mirror` config flag).

Mode semantics:

- `role_source: files` — TUI-style.  Files are SOT.  CRDs are projected
  *from* files (for parity with K8s tooling) but treated as derived.
- `role_source: crd` — OpenShift production.  CRDs are SOT.  Files are
  read-only projections.
- `role_source: mirror` — laptop dev mode.  Whichever side moves last
  wins; conflicts logged.  Useful for the operator's own test
  environment.

This is **proposal 008** in the operator's Obsidian vault.  Touches
schema, role_loader, the operator reconciler, and arguably the
existing Spiffe alignment question from proposal 004.

### P8 — Schema-driven console-form docs *(XS, docs)*

Already works.  Just document.  Add a section to `docs/howto-rhoai.md`
showing:

- Screenshot of the OpenShift Web Console form for `AgentCollective`
  generated from the kubebuilder annotations.
- Table of which `+kubebuilder:validation:*` lines drive which form
  affordances (Required field, Min/Max length, Enum dropdown).
- Note that adding `+kubebuilder:default=…` populates the form with
  sensible defaults.

Visible win: operators discover that 30% of "editing role definitions
on OpenShift" is already a no-code surface.

---

## 4. Cross-surface recommendation

A single coherent picture:

- **TUI gets P1 + P2 + P3** — these turn the existing widgets from
  "lowest-common-denominator text boxes" into real form fields with
  syntax colour, validation, and a one-keypress escape hatch to the
  operator's normal editor.  Total effort ~1 week, all small PRs.
- **For "I want VS Code", pick P5 over P4** — same operator outcome,
  zero Python, doesn't lock ACC into maintaining a Monaco bundle.
  P4 only justifies its weight if we layer role-aware UX on top.
- **PD extension gets P6** — keeps it from being read-only forever.
  Work happens in `flg77/acc-podman-desktop`; this repo just
  references the schema.
- **OpenShift gets P8 today, P7 later** — the docs are free; the
  bi-directional sync is the proposal-008-sized lever that finally
  unifies the three storage backends.  Wait for operator sign-off on
  the source-of-truth model before coding.

This sequence respects the existing proposal-007 deferral ("don't
build a half-editor inside Textual") while still giving the operator
real editing wins: P1 + P2 + P3 are *exactly* the small,
file-backed, externally-editable design 007 advocates for; P5
delivers the "real editor" without inventing one.

---

## 5. Suggested execution order

1. **P1** — 1-line edits to existing TextAreas.  Ship in a day.
2. **P2** — wire the skeleton.  Couple of hours plus the
   `app.suspend()` plumbing.
3. **P3** — adapter + bind-to-form.  Half-day each for the model
   coverage.
4. **P5** — compose-only change + small docs section.  Zero Python.
5. **P4** — only if operators say P5 isn't enough.
6. **P6** — parallel work in the PD repo.  Coordinate the validator
   schema (P3) so PD doesn't reinvent it.
7. **P8** — write the docs section once the screenshots are easy
   (you've got an OpenShift cluster handy).
8. **P7** — open as proposal 008 in the Obsidian vault, scope with
   the operator, then size.  Largest of the eight; deserves its own
   plan.

---

## 6. What this report deliberately leaves alone

- The deferred proposals 004 (subrole hierarchy / Spiffe), 005 (plan
  infusion), 006 (surface-ownership linter), 007 (in-pane edit) from
  the previous plan all remain valid.  P7 may absorb 004; the others
  are orthogonal.
- The Podman Desktop extension's *non-editing* panels (Performance,
  Compliance, Cluster topology) are out of scope here — they work
  fine and were called out in 003 as already adequate.
- The autoresearcher / coding-agent / role-template content
  itself.  This report is about how operators *edit* role
  definitions, not what those definitions should say.

---

*If this report's framing lands, the next concrete action is one of:*

1. *Open proposal 008 in Obsidian for P7 (the convergence story).*
2. *Open three small PRs for P1 + P2 + P3 against `flg77/acc`.*
3. *Open one compose-only PR for P5.*

*P4 / P6 / P8 wait on operator interest.*
