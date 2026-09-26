# User Manual

The short version of how to run and drive ACC. The full per-command reference
is `CAPABILITIES.md`; the screen-by-screen walkthrough is `howto-tui.md`; the
release history is `CHANGELOG.md`.

## Getting started

The short form, once ACC is installed on the host (`uv tool install
agentic-cell-corpus[tui]`, or the RPM):

```bash
# From the channel, on a host on the lab network:
sudo tee /etc/yum.repos.d/acc.repo >/dev/null <<'REPO'
[acc]
name=ACC packages
baseurl=http://rpm.ic3net.internal:8080/acc-spearhead/
enabled=1
gpgcheck=0
REPO
sudo dnf install acc
sudo usermod -aG acc $USER             # share the service's state (/var/lib/acc) -- log in again
sudo systemctl enable --now acc-stack  # the collective runs as the acc user
```

A host that cannot reach the lab installs both packages by file, built for
its EL release — `docs/INSTALL.md` has the whole procedure, including upgrades.

Once the channel is keyed, verify every package against the key the Satellite
serves, which it publishes without credentials:

```bash
sudo rpm --import https://sat1.ic3net.internal/katello/api/v2/repositories/30/gpg_key_content
sudo sed -i 's/^gpgcheck=0/gpgcheck=1/' /etc/yum.repos.d/acc.repo
```

```bash
acc setup                          # guided first run: posture, model, storage
acc stack up --webgui              # a wheel install; on the RPM, systemctl runs the stack
acc                                # the TUI, attached — exits 3 with a hint if nothing answers
acc paths                          # where this host's ACC lives, and why each file was picked
acc tour                           # the first-run tour, again (it runs by itself the first time)
```

ACC finds its files through one rule — `$ACC_HOME`, else `~/.config/acc`,
else `/etc/acc`, else the checkout you are in — so the commands work from
any directory (`docs/INSTALL.md`). From a checkout, the long form still works:


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
  are always asked; `ASK_PERMISSIONS` asks for everything. A call that
  **deletes or overwrites data** (`rm`, `find -delete`, `git push --force`,
  `kubectl delete`, `DROP TABLE`, a manifest's `destructive: true`, …) is asked
  as a question in every mode but `PLAN`, and refused where nobody can be asked.
- **Answering a request in the Prompt pane** — when a gate arrives the request
  region takes focus: a proposal batch offers `1 approve all · 2 reject all ·
  a/d this row`; a capability gate `1 allow once · 2 allow for this task · 3
  deny`; an escalation `1 allow for this task · 2 deny`. HIGH takes the key
  twice. `Esc` leaves it pending, `Ctrl+G` returns, `r` prefills a reason. Every
  answer is recorded in Compliance. A decision is final — a conflicting second
  one is refused. Once answered, a request stays out of the pane even while
  the arbiter's heartbeat still lists it for a few seconds (v0.17.1); one whose
  decision failed to send stays open.
- **Asking about a decision** — `c` in the panel asks a question about the
  request without resolving it: the request stays PENDING, and the agent's
  answer renders **in the panel, under the question you asked**, not only in
  the thread. The panel keeps the last couple of exchanges and trims a long
  answer (the thread keeps all of it).
- **How long you have, and who has signed** — a decision that expires shows
  its countdown (`expires in 4m00s`, then `expired`), because a gate that
  times out is rejected and a deadline you cannot see is a decision you can
  lose by reading slowly. Only a capability gate or a question the agent is
  waiting on shows one: nothing expires a proposal, so it gets no countdown
  rather than a false one. A decision needing two approvers shows `PENDING
  1/2` and names who has approved so far, read from the row itself so it
  moves when the second approver signs.
- **Whose work this is** — the panel names the person the task was admitted
  for and the ceiling in force (`for slack:alice · ceiling MEDIUM`), so you
  can see whether you are approving your own request or signing for someone
  else's. A task that never passed admission says `unattributed` rather than
  leaving it blank. Nothing here changes enforcement — the ceiling was always
  checked at dispatch; this makes the check visible.
- **What this runs** — every gated capability call shows the call itself
  above the options: `runs: shell_exec {"cmd": "rm -rf build/"}`, what makes
  it destructive when it is, and for an MCP call the transport and URL it
  reaches. Argument values whose key looks like a key / token / secret /
  password are shown as `***`. Nothing is executed to produce these lines —
  they are the parsed call and the manifest, so the decision stays pending
  and untouched while you read them.
- **A preview, when the capability declares one** — a skill or MCP server may
  declare `preview_args` (e.g. `{"dry_run": true}`); a gated call then runs
  that dry run once before you are asked, and the panel shows what it would
  do under `preview:`. **Declared, never inferred** — ACC does not rewrite a
  shell command to add `--dry-run`, because guessing a third-party CLI's
  flags is not a dry run, it is a hope. A preview is an execution and is
  journalled as one; a failed preview shows as `preview failed: …` and
  changes nothing — it never approves, never rejects, never blocks the gate.
- **A destructive question** — the decision panel names what will be destroyed
  (`shell_exec will delete or overwrite data: rm -rf build/. Run it?`) and
  offers `1 run it · 2 don't run it`. It is answered on its own, always with
  the key twice; "allow for this task", a bare "yes" and `/allow` never answer
  it (`/disallow` still refuses it). A call it lets through is marked
  `⚠ critical` in the transcript and the session trace, with the answer and who
  gave it.
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
  role; work still running after 120 s (usually a tool call held for approval)
  returns 202 with a pollable handle, and the poll names the oversight row that
  holds it; `usage` is the task's own count, `null` when not reported;
  `X-ACC-Session` names a thread; `acc-cli doctor --check compat`. See
  `docs/howto-openai-compat.md`.
- **Images in a prompt** (web GUI) — *Attach image* on the Prompt page. The image
  reaches a role bound to `anthropic` or `openai_compat`; any other backend
  **refuses the turn** rather than answering without it. The web GUI and the
  agents must share `/logs` (the compose file mounts it). Stored images follow
  the session retention policy; `acc-cli doctor --check attachments` shows the
  store.

## Configuration

Key environment variables: `ACC_NATS_URL`, `ACC_REDIS_URL`, `ACC_COLLECTIVE_ID`,
`ACC_LLM_BACKEND` / `ACC_LLM_BASE_URL` / `ACC_LLM_MODEL`, `ACC_OPERATOR_MODE`
(`prod` locks authoring; `dev` is refused on rhoai/edge), `ACC_TUI_PROFILE`,
`ACC_THREAD_CONTINUITY=0` (kill switch), `ACC_PROMPT_PERMISSION_REGION=0`
(plain gate card), `ACC_WORKSPACE_CHECKPOINTS`, `ACC_COMPAT_API_KEYS`,
`ACC_WEBGUI_AUTH_MODE`. The schema is derived from the config models:
`acc-cli config check --all` lists every key.

## Answering a decision without leaving the Prompt pane

When something needs your yes it is asked **in the Prompt pane**, not in
Compliance (which keeps the record). One request of one step opens the decision
panel: the question, the numbered options, and **beside them what the
highlighted option actually does** — what it will run, whether it is remembered
for the task or asked again, where it lands, its risk, and how many approvals it
still needs. A two-approver row shows `PENDING 1/2` and who has signed.

| Key | In the panel |
|---|---|
| `↑` / `↓` then `Enter`, or the option's digit | choose (HIGH / CRITICAL takes the key twice) |
| `n` | type a note onto the decision — kept on **your** approval record |
| `c` | ask about this decision; the request **stays pending** while you do |
| `r` | reject with a reason |
| `d` | **defer** — back in 5 min, 15 min or 1 hour, or when the agent answers the question you asked about it; it always comes back a minute before it could expire, and the panel says when it had to pull the time in |
| `h` | **hand off** to a person (`webgui:alice`, `slack:U1`) or a tier (`operator`) — it stays pending, anyone who could decide it still can, and it stops being raised here |
| `y` / `Y` | copy the decision id / the command it runs |
| `Esc` | later — still pending; `Ctrl+G` brings it back |

A category gate at LOW or MEDIUM also offers **`4` allow this class for 30 min**:
it approves this one and, for 30 minutes of this session, further gates of the
same category, risk and requester — never a destructive call, never HIGH, never
someone else's work, and never written anywhere that outlives the TUI. `3` is
still **deny**. `/snooze` lists what is running and `/snooze off` ends it;
`/deferred` lists deferrals (`/deferred now` brings them back) and `/delegated`
what you handed on. `acc-cli oversight delegate <id> --to <person|tier>` does the
same hand-off from a shell.

`ACC_PROMPT_LINEAR=1` renders the panel in reading order — the options, then the
highlighted option's details under a label — for a screen reader, instead of the
details boxed beside the options.

A reply proposing several steps keeps the compact list instead, where `1`/`2`
take all of them and `a`/`d` take the highlighted row. `ACC_PROMPT_PANEL=0`
sends everything to that list.

## Keyboard shortcuts (TUI)

`Ctrl+A` + digit / `Ctrl+P` — jump to a screen · `?` help · `Ctrl+S` send ·
`Shift+Tab` mode · `Ctrl+G` the pending decision · `Ctrl+O` reasoning · `Ctrl+L` clear · Board:
`c` `r` `a` `g` `Enter` · Compliance: `a` approve, `r` reject.

## Troubleshooting

- **Nothing happens after an approval** — check the arbiter log for the claim;
  a row already decided the other way is refused (the first decision stands).
- **A release stops at "signing needs BAO_TOKEN"** — the channel's key lives in
  OpenBao; export a token. Only a channel with no key yet may take `--unsigned`.
- **`publish-satellite.sh` says REFUSING … does not verify against the signing
  key** — the package is unsigned, or signed with another key. Sign it with
  `packaging/rpm/sign-rpms.sh <org>/<product>/<repo> <host>:<dir>`.
- **`dnf` upgraded ACC but the version did not change** — compare `rpm -q acc`
  with `acc --version`. If they disagree the host is running code the package
  did not install; that was possible before v0.16.0, where bytecode written at
  runtime could survive an upgrade. Reinstall, and check both numbers rather
  than the package alone.
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
- **A hub promotion shows `PENDING 1/2` after an approval** — by design: a
  HIGH or CRITICAL note entering a hub needs two *different* operators; the
  same person approving again (from any surface) is still one. A second
  operator approves it in Compliance, the Prompt pane or `acc-cli oversight
  approve`; a reject by anyone ends it. A requester-tier approval on such a
  row is refused outright.
- **A viewer's Board or Web GUI board is empty while the operator's is full**
  — by design (D-017): a non-operator sees only the items they asked for,
  matched by person *per surface*, and never unattributed work. Check the
  task's `requested_by` (`webgui:<user>`, `slack:<id>@<channel>`); a prompt
  from another surface does not show (cross-surface identity is on the
  HG-40.1 list). A plan's steps carry the plan's requester, ceiling included.
- **First reply after a restart is slow** — the edge 3B model's first call can
  take minutes; the second is fast.

_Last updated: 2026-09-11 (v0.17.1)_
