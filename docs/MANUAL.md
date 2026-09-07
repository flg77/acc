# User Manual

The short version of how to run and drive ACC. The full per-command reference
is `CAPABILITIES.md`; the screen-by-screen walkthrough is `howto-tui.md`; the
release history is `CHANGELOG.md`.

## Getting started

**Standalone / edge (podman):**

```bash
acc-cli setup                      # guided first run; validates as it goes, ends with doctor
./acc-deploy.sh build              # images tagged by `git describe`
./acc-deploy.sh up --webgui        # NATS, Redis, one container per cell, TUI, Web GUI
acc-cli doctor --probe             # BROKEN / DEGRADED / DRIFTED; exit 1 only on BROKEN
acc-cli status                     # every cell: role, running, resolved model, heartbeat
acc-tui --profile user             # Prompt · Board · Compliance (operator = every screen)
```

Configuration lives in five files (`acc-config.yaml`, `models.yaml`,
`collective.yaml`, `catalogs.yaml`, `.env`) — all per host and gitignored;
`acc-cli config path` shows where each resolves. Credentials only ever live in
`.env`; nothing writes it.

**OpenShift / RHOAI:** install the operator, create an `AgentCorpus`; the Web GUI
sits behind oauth2-proxy/Keycloak (a pre-provisioned `<ns>-webgui` client is
required).

## Features

- **Prompting a role** — Prompt screen, `Ctrl+S` sends to the selected role; the
  reply, reasoning stream and outcomes land in the thread; a follow-up in the
  same session continues the conversation (replayed from the tracelog).
  `@path`, `@dir/`, `@diff` attach workspace content as *data*.
- **Operating modes** — `/mode AUTO|PLAN|ACCEPT_EDITS|ASK_PERMISSIONS` (or
  `Shift+Tab`). Under `AUTO` a curated infuse / spawn / route executes and is
  tracked as an `AUTO_APPROVED` row; system access and acting on your behalf
  are always asked; `ASK_PERMISSIONS` asks for everything.
- **Answering a request in the Prompt pane** — when a gate arrives the request
  region takes focus: a proposal batch offers `1 approve all · 2 reject all ·
  a/d this row`; a capability gate `1 allow once · 2 allow for this task · 3
  deny`; an escalation `1 allow for this task · 2 deny`. HIGH takes the key
  twice. `Esc` leaves it pending, `Ctrl+G` returns, `r` prefills a reason. Every
  answer is recorded in Compliance. A decision is final — a conflicting second
  one is refused.
- **Who sees what** — an operator sees the whole collective; anyone else
  (a viewer token, a Slack requester at a shared TUI) sees the tasks, plan
  steps, gates and signals they asked for, on every surface.
- **The Board** — what the runtime is doing: plan steps, cluster members,
  single tasks and the gate that blocks a step, under QUEUED · RUNNING ·
  BLOCKED · DONE · FAILED. `c` cancel, `r` retry, `a` reassign, `g` go to the
  gate. Nobody drags a card to Done. Same board in the Web GUI, attributed to
  the logged-in user.
- **Compliance** — governance layers, frameworks and gap scans, the pending
  queue, and the decision history (one row per decision, with who or which
  policy decided).
- **Roles and packs** — Ecosystem (roles, skills, MCPs), Nucleus (infuse a role;
  `Apply` publishes a signed `ROLE_UPDATE`), Marketplace / Catalogs (signed
  `.accpkg` packs; cosign-verified on install; a built-in day-0 catalog).
- **Plans** — `acc-cli plan submit plan.json --watch`; the arbiter runs the DAG;
  watch it on the Board or in Comms.
- **Memory** — per requester / channel / isolated scopes; `acc-cli memory notes
  --scope …`; publication to the shared tier is a proposal with a quorum of two
  people (`memory propose --to <scope>`); `acc-cli memory forget --person <id>`
  (dry run by default). Instances bound to a hub publish into its enterprise
  tier (`memory propose --to hub:<cid>`, `memory curate --hub <cid>`) and read
  it on every prompt; a note is never read below the ceiling of the work it
  came from. A hub with a curator (`instance create enterprise --stack-profile
  edge-min --agent hub_curator`) proposes promotions on its own; an operator
  approves them.
- **Operations** — `profile list/apply/revert` (whole posture, reversible),
  `backup`/`restore` (no secret values), `sessions browse/resume/retention`,
  `checkpoints`, `logs --task`, `objective`, `hooks`, `scan`, `mcp`, `auth`
  (credential pools), `egress`, `access` (who may ask, and how far — a
  requester's work stops at MEDIUM unless the role is narrower), `secrets scope`.
- **Instances** — `acc-cli instance create alice-dev --owner system:alice
  --profile edge-lean --pack @acc/workspace-roles`, then `./acc-deploy.sh
  instance up alice-dev` and `eval "$(acc-cli instance env alice-dev)" &&
  acc-tui`: the same runtime, its own memory / sessions / trace / overlays,
  owned by one person. `export` moves the definition, not the state.
- **OpenAI-compatible endpoint** — set `ACC_COMPAT_API_KEYS`; `model` names a
  role; gated work returns 202 with a pollable handle; `X-ACC-Session` names a
  thread.

## Configuration

Key environment variables: `ACC_NATS_URL`, `ACC_REDIS_URL`, `ACC_COLLECTIVE_ID`,
`ACC_LLM_BACKEND` / `ACC_LLM_BASE_URL` / `ACC_LLM_MODEL`, `ACC_OPERATOR_MODE`
(`prod` locks authoring; `dev` is refused on rhoai/edge), `ACC_TUI_PROFILE`,
`ACC_THREAD_CONTINUITY=0` (kill switch), `ACC_PROMPT_PERMISSION_REGION=0`
(plain gate card), `ACC_WORKSPACE_CHECKPOINTS`, `ACC_COMPAT_API_KEYS`,
`ACC_WEBGUI_AUTH_MODE`. The schema is derived from the config models:
`acc-cli config check --all` lists every key.

## Keyboard shortcuts (TUI)

`Ctrl+A` + digit / `Ctrl+P` — jump to a screen · `?` help · `Ctrl+S` send ·
`Shift+Tab` mode · `Ctrl+G` gates · `Ctrl+O` reasoning · `Ctrl+L` clear · Board:
`c` `r` `a` `g` `Enter` · Compliance: `a` approve, `r` reject.

## Troubleshooting

- **Nothing happens after an approval** — check the arbiter log for the claim;
  a row already decided the other way is refused (the first decision stands).
- **Proposal card shows no rationale / progress line does not move** — an agent
  older than v0.11.2 is packing dicts on the wire; rebuild it (the TUI tolerates
  it but shows less).
- **`acc-webgui` restarts in a loop** — an image older than v0.11.1 lacks
  `python-multipart`; rebuild.
- **`oversight reject` says "item not found"** — pass the full id or a unique
  prefix (`pending` prints full ids since v0.11.3).
- **LLM calls fail with `BackendConnectionError`** — `.env` may be an empty
  *directory* (podman artefact) so `ACC_LLM_*` fell back to the template;
  replace it with a file.
- **A reply comes back blocked with `task_error: <type>: <message>`** — the
  agent's LLM call failed (a gateway disconnect, an out-of-credits 400); since
  v0.12.1 the task ends instead of stranding a plan step. Check the agent log
  and the LLM endpoint; retry the step from the Board.
- **`refused: skill 'x' is HIGH -- above the requester's ceiling MEDIUM`** —
  by design (D-014): work asked for by an admitted requester or an API key
  stops at MEDIUM. If the work is legitimate, an operator runs the prompt;
  admissions can only narrow a ceiling, never widen it.
- **Instance cells crash-loop on `Permission denied` under their roots** — an
  image older than v0.13.1 mounted the instance directory without `U`;
  rebuild. Reading a cell-owned root from the host is `podman unshare`.
- **A hub promotion was approved but nothing landed** — the agent log says
  `publish … into hub … refused — approver … operator tier required`: a hub
  promotion needs an operator-tier approver (D-016); a requester's approval,
  or a surface older than v0.14.1 that sends no tier, is refused and
  journalled as `note_publish_refused`.
- **First reply after a restart is slow** — the edge 3B model's first call can
  take minutes; the second is fast.

_Last updated: 2026-09-07 (v0.14.1)_
