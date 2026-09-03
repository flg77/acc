# Tasks — surface parity

Ordered so that users get something early and the architecture does not get
retrofitted at the end. Phase 0 decides the sequence; do not start Phase 2
without it.

## Phase 0 — decide, before building

- [ ] **Sequence** (open question 1). Recommended: hybrid — extract a service
      function only for the capability currently being exposed, so the layer
      grows with the work instead of as a big-bang refactor nobody can review.
- [ ] **Authorisation for authoring on the web** (open question 2). The TUI's
      presence on the host *was* the authorisation; the web has no equivalent.
      Decide the role check for `models.yaml` CRUD, package publish, and role
      edit before shipping any of them. The answer is a permission, never an
      omission.
- [ ] **Declare Slack's status** (open question 3): parity surface, or explicitly
      a subset with a stated reason.
- [ ] Write `docs/surface-parity.md` — the contract itself: what a surface must
      implement to be called a surface. A desktop app will be read against this.

## Phase 1 — the two structural gaps

Neither is a screen; both are things the web surface silently cannot do.

- [x] ~~`acc/channels/webgui.py`: add `session_id`~~ — **not needed.**
      `WebPromptChannel` subclasses `TUIPromptChannel` and does not override
      `send()`, so it has accepted `session_id` since `6853fb8`. The earlier
      claim that the channel lacked it was wrong: the grep found nothing
      because the file is a 42-line subclass. The gap was one layer up — the
      route never passed one.
- [x] Degrade path tested explicitly: two sessionless prompts must not share a
      thread. The failure guarded against is a *shared default* — echoing the
      collective id, or a constant — which would silently join two users'
      conversations.
- [x] `POST /prompt` accepts `session_id` and forwards it; the reply echoes
      `session_id or task_id` so a client that named no thread can adopt the one
      it was given. The Prompt screen holds it, shows which thread is live, and
      has an explicit **New thread** control.
- [x] Tests: `tests/test_webgui_session_continuity.py` (8), including a guard
      that fails if anyone gives `WebPromptChannel` its own `send()` that drops
      the parameter, and a channel-vs-base signature parity assertion.
- [ ] `routes_compat.py` session — **blocked on a design decision, not built.**
      An OpenAI client resends the entire message array every turn, and
      `parse_request` joins those into one prompt. Passing a `session_id` as
      well would have the agent replay prior turns from the tracelog *and*
      receive them again in the prompt — the same history twice, charged twice
      against the context budget. The fix is to send only the latest user
      message and rely on server-side replay, which is more auditable and
      trusts the client less, but it changes what a compat request means.
- [ ] Attachments — **much larger than "wire up the UI"; not started.**
      `content_blocks()` is called from **tests only**. No channel, no
      `cognitive_core`, no backend consumes an attachment. `POST
      /api/attachments` accepts an image, stores the bytes and returns a
      reference that nothing downstream can use. The delivery path has to be
      built end to end — prompt carries refs → TASK_ASSIGN → `cognitive_core`
      → backend content blocks → the durable record. Needs its own proposal.
      This strengthens the category-B finding: the route was merged with
      neither a caller above it nor a consumer below.

## Phase 2 — the 23 capabilities, by user-visible grouping

Each item means: a service function, a route, and a web UI that reaches it. Not
a route alone — `routes_attachments` is the cautionary example.

**Golden prompts** (`acc.golden_prompts`, `acc.pkg.golden_pack`) — the TUI's
single heaviest capability, 23 imports.

- [ ] Author and edit (form: title, description, body).
- [ ] Version history and version picker.
- [ ] Run and promote — routes already exist under `/diagnostics/golden/*`;
      confirm the UI reaches all of them.
- [ ] CSV/JSON import and export.
- [ ] Export as an `@scope/*` pack.

**Models and role→model** (`acc.models`, `acc.role_model_map`, `acc.config`)

- [ ] `models.yaml` CRUD, behind the Phase-0 authorisation decision.
- [ ] Role→model map: visible *and* editable.
- [ ] Reload without a restart, as the Config pane does.

**Marketplace, catalogs, packages** (`acc.marketplace`, `acc.pkg.*` — 6 modules)

- [ ] Browse the built-in day-0 catalog (`20e5e02` shipped the default; the web
      marketplace still shows empty on a stock host).
- [ ] Knowledge-pack and bundle counts.
- [ ] Install, fetch, resolve.
- [ ] Publish and rate — publishing from a browser needs the Phase-0 decision.

**Roles and skills** (`acc.role_loader`, `acc.skills*`, `acc.mcp*`,
`acc.capability_*`)

- [ ] Role load/author beyond today's RoleEditor.
- [ ] Skill and MCP registry browsing.
- [ ] Capability index and validation surfaced.

**Operating state** (`acc.operating_modes`, `acc.operator_identity`,
`acc.collective`, `acc.self_challenge`)

- [ ] Operating mode visible and switchable.
- [ ] Operator identity shown.
- [ ] Compliance gate card inline in the web Prompt screen — 136 TUI references,
      zero in the web frontend, and the archetypal web user is a reviewer.

## Phase 3 — make parity mechanical

Without this, Phase 2 is a snapshot the next 73 commits undo.

- [ ] `acc/services/` established, with the extracted capabilities from Phase 2.
- [ ] **Parity test**: every service function has a route. A new capability that
      forgets the web fails CI rather than shipping.
- [ ] **Channel parity test**: a channel gaining a parameter its siblings lack
      fails. This would have caught the `session_id` gap at commit time.
- [ ] **Backend-only check**: a mounted route with no frontend caller must be
      declared API-only or marked unfinished.

## Phase 4 — desktop readiness

- [ ] Confirm against `docs/surface-parity.md` that a desktop app can be built
      as a renderer over `acc/services/` or the HTTP API, with no capability it
      would have to reimplement.
- [ ] Decide embed-vs-HTTP for desktop. Not needed now; the point of Phases 1–3
      is that it stops being an expensive question.

## Not in scope

Presentation only — a command palette and which-key bindings are terminal
idiom, and their web equivalents are a search field and a menu. **The
capabilities they reach are in scope and must exist on both surfaces.**


## APPLIED — Phase 1 (partial), 2026-08-30

**Done:** conversational continuity reaches the web surface. 8 new tests; 422
passed across the webgui/prompt/channel suites; `tsc --noEmit` clean.

**Two corrections to this plan, both found by doing it:**

1. The channel never lacked `session_id` — it inherits it. The defect was one
   layer up, in the route. Cheaper than billed.
2. Attachments is not a UI-wiring task. There is no delivery path at all, so it
   is a feature to build, not a gap to close. More expensive than billed.

**`operating_mode` and `workspace` closed too** — the same defect class as
`session_id`: the channel accepted all three, the route passed none. The web
surface was pinned to AUTO in a fixed workspace while the TUI could choose both.

Neither is validated at the route, deliberately:

- An unknown `operating_mode` is coerced to AUTO by
  `acc.operating_modes.normalise` agent-side, failing toward the *stricter*
  gate. A second check at the route could only disagree with that one.
- `workspace` is client-supplied and this surface is remote, unlike the TUI —
  but the guard belongs where the mount is known.
  `agent._resolve_task_workspace_dir` rejects absolute paths, `..` and `/..`,
  and `workspace.resolve_in_workspace` enforces symlink-collapsed containment.
  The route already requires an operator principal. Tests assert the escapes
  are refused, so a change to that check cannot silently widen what the web can
  reach.

13 tests; 469 passed across webgui/prompt/channel/operating; `tsc` clean.
