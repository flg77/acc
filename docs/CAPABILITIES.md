# ACC operator surfaces — CLI, TUI and Web GUI

What each surface can do, and — just as usefully — what it cannot. ACC exposes
three ways to drive a collective, and they are **not** interchangeable. Knowing
which one owns a task saves looking for a control that was never there.

| | `acc-cli` / `acc-pkg` | TUI (`acc-tui`) | Web GUI (`acc-webgui`) |
|---|---|---|---|
| **Best for** | automation, scripts, CI, SSH | day-to-day driving | demos, multi-user review |
| **Needs a TTY** | no | yes | no (browser) |
| **Authentication** | host/shell | host/shell | oauth2-proxy / Keycloak |
| **Changes config** | yes (`config set`) | yes (Configuration, Nucleus) | read-mostly |
| **Approves oversight** | yes | yes | yes |
| **Runs a task** | via `plan submit` | Prompt screen | `POST /prompt` |

A rule of thumb: **anything that must run unattended belongs on the CLI**. The
TUI and the Web GUI are for a human who is present.

---

## 1. CLI — `acc-cli` (28 commands)

Argparse only, no runtime dependency beyond the standard library for parsing, so
`--help` is fast and the command works inside a minimal container.

### Health and configuration

| Command | What it does |
|---|---|
| `acc-cli doctor` | One read-only health report. `--json` for monitoring, `--probe` to also dial endpoints, `--check NAME` for one check. **Non-zero exit only when a check is BROKEN.** |
| `acc-cli status` | Per-agent and collective-wide state: role, running/absent, resolved backend + model, last heartbeat, bus, working memory, oversight depth. `--json`, `--role`. Non-zero exit when unhealthy. |
| `acc-cli config show/get/set/unset` | Typed access to the five configuration files. `set` validates against the schema and **preserves comments and formatting**. |
| `acc-cli config path` | Where each configuration file resolves to on this host, and which are read-only or secret-bearing. |
| `acc-cli config check` | Missing, unknown, deprecated and unresolvable configuration. `--all` includes keys left at their default. |
| `acc-cli config migrate` | Writes options a newer release added, using their defaults. Never overwrites an existing value. |
| `acc-cli secrets scope` | Which credentials each role actually needs, and which ones scoping would remove. Names only — never values. |

Three severity classes in `doctor`, because the operator's next action differs:
**BROKEN** (cannot work as configured — sets the exit code), **DEGRADED** (a
dependency is unhealthy right now), **DRIFTED** (files are correct but not in
effect). A degraded endpoint deliberately does not fail the command: it is
usually a transient upstream blip, and a monitor that pages on it teaches people
to ignore the page.

### Identity and access

| Command | What it does |
|---|---|
| **`hub_curator`** (role) | The enterprise brain's only agent: no chat surface, no skills; on a schedule it proposes qualifying shared notes from the bound instances into the hub's enterprise tier. People approve at **operator tier** — a hub promotion by a lower tier is refused. |
| `acc-cli memory notes --hub` / `memory propose --to <scope|hub:<cid>>` / `memory curate --hub <cid> [--propose]` | The enterprise brain: a hub's **enterprise tier** fills only through approved publish proposals from the bound instances (quorum of two people, probation, ceiling carried) and is the only cross-instance read path. `curate` lists the candidates; `propose` queues one for a person to approve. |
| `acc-cli instance create/list/show/archive/export/import/synth/env` | An **instance**: a collective bound to an owner, a posture and its own state roots (`instances/<id>/`). `synth` renders its compose overlay, `./acc-deploy.sh instance up <id>` runs it; `export` carries the definition, never state. |
| `acc-cli access whoami` | The identity this process acts as, and which substrate vouched for it. |
| `acc-cli access list/admit/revoke/check` | External requesters (chat, webhooks). **Default deny**; admitting is an explicit operator action, and an external identity can never be granted the operator tier. Every admission carries a **category ceiling** (`requester` MEDIUM by default; `--ceiling` narrows it, nothing widens it): work above it is refused outright, never offered to a human (D-014). |
| `acc-cli auth list/status/add/remove/reset` | Credential pools. A `429` rests a key; a `401` **faults** it and it stays out until cleared — rotating past a rejection hides a revoked credential. |
| `acc-cli egress policy/check/journal` | Destination policy and brokered credentials. ACC injects the credential so the agent never holds it; *where* traffic may go is the substrate's job. |

### Forensics

| Command | What it does |
|---|---|
| `acc-cli logs --task <id>` | One piece of work across every agent that touched it. Sources are labelled (container vs tracelog), and a missing source is reported rather than fatal. |
| `acc-cli sessions browse/resume/continue/rename/export` | Find an investigation and pick it up. Resuming appends a parent link; it never rewrites history. |
| `acc-cli sessions retention [--apply]` | Governed removal. Defaults to keep-forever; **no removal path leaves no trace**. |
| `acc-cli checkpoints list/show/restore/prune` | Snapshots before agent writes, linked to the task **and the approving decision**. |

### Memory

| Command | What it does |
|---|---|
| `acc-cli memory notes [--scope]` | The durable lessons a given context would read. Scoped: a channel's memory approximates its scrollback. |
| `acc-cli memory forget --person <id> [--apply]` | Remove a person's episodes and reconcile the notes drawn from them. **Dry run by default.** A note with no sources left is deleted; one below quorum is demoted *and* unpublished; one still above quorum is rebuilt. Erasure touches memory only — the audit record of the request survives it. |

### Work and governance

| Command | What it does |
|---|---|
| `acc-cli objective new/list/show/pause/resume/cancel` | Work that persists across turns under a **mandatory ceiling**. An objective never raises the autonomy level — a gated action inside it still waits. |
| `acc-cli hooks list/add/test/remove` | Run a command on a lifecycle event. Hooks **observe**; gating belongs to the oversight queue. |
| `acc-cli scan [--fail-on <sev>] [--propose]` | Known-vulnerable components. A finding raises a decision, not a block — but exit **2** means the scan could not run, which is not the same as clean. |
| `acc-cli backup` / `acc-cli restore` | Capture a deployment. The archive holds **no secret values**, and a restore refuses rather than returning a deployment that cannot authenticate. |
| `acc-cli setup [<section>]` | Guided first run that validates at the point of entry and finishes by running the health checks. |
| `acc-cli profile list/show/diff/apply/revert/export/import` | Whole deployment postures. Validated before applied, reversible, and posture changes are flagged separately. |
| `acc-cli mcp list/test/tools/add/remove` | MCP servers. Host overrides only ever **subtract** — `effective = manifest ∩ host ∩ role`. |

### Roles and collectives

| Command | What it does |
|---|---|
| `acc-cli role list/show/lint/audit` | Inspect and validate role definitions. |
| `acc-cli role compile/decompile` | Move between the authoring form and the runtime form. |
| `acc-cli role infuse` | Apply a role update (arbiter countersignature required). |
| `acc-cli collective validate/diff` | Check a collective preset, and see what a change would alter. |
| `acc-cli collective synthesize` | Generate the deployment from the preset. |
| `acc-cli collective pkg-install / pkg-status` | Package installation into a running collective. |
| `acc-cli overlay show/validate` | Inspect deployment overlays. |

### Governance

| Command | What it does |
|---|---|
| `acc-cli oversight pending/approve/reject` | The human decision point. An approval dispatches exactly one action — the claim is atomic, so a six-agent collective performs one install, not six. `pending` prints full ids; `approve`/`reject` take the full id or a **unique prefix** (an ambiguous one is refused). A decision is **final**: a conflicting second decision is refused, the first stands. |
| `acc-cli oversight submit` | Raise a proposal for a decision. |
| `acc-cli sessions list/show/verify` | Replay the durable, hash-linked tracelog. **`verify` re-checks each recorded step against the Category A/B/C gates** — a tamper-evident audit of what an agent actually did. |
| `acc-cli plan submit/watch` | Submit a plan and follow it. The plan carries the submitting principal, and every step the executor dispatches runs as that person: their ceiling holds per step (D-014), their memory scope receives the episode, the Board shows it to them. |
| `acc-cli trace` | Follow reasoning traces. |

### Diagnostics and operations

| Command | What it does |
|---|---|
| `acc-cli llm test` | Send a tiny prompt to the configured backend and print the result. |
| `acc-cli nats pub/sub` | Publish to and subscribe from the signalling bus. |
| `acc-cli e2e list/run/show/validate` | The golden-prompt suite. |
| `acc-cli schedule add/list/remove/run-once` | Scheduled agent work. |

### Packaging — `acc-pkg`

`init`, `build`, `publish`, `install`, `uninstall`, `remove`, `list`, `info`,
`inspect`, `contents`, `rdeps`, `owner`, `login`, `eval`, `golden-pack`,
`new-role`, plus the `q*` query family (`qf`, `qi`, `ql`, `qv`).

Packages are **cosign-verified on install** — keyless verification uses the
bundle (signature + certificate + Rekor entry), keypair verification uses
`--key`. An unverifiable package does not install.

---

## 2. TUI — `acc-tui`

Textual. Thirteen screens; the navigation bar moves between them. `acc-tui
--profile user|operator` (`ACC_TUI_PROFILE`) chooses the strip: `operator` is
every screen, `user` is Prompt · Board · Compliance with the rest one `Ctrl+A`
chord away. A profile is a view choice — nothing about what may be done differs.

| Screen | What it is for | Keys |
|---|---|---|
| **Dashboard** | Collective at a glance | — |
| **Prompt** | Send work to a role and watch it reason | `ctrl+s` send, `shift+tab` cycle mode, `ctrl+l` clear |
| **Ecosystem** | Roles, skills, MCPs; infuse from here | — |
| **Marketplace** | Browse and install packages | `/` filter, `enter` install, `r` refresh, `+`/`-` rate |
| **Catalogs** | Catalog sources and priority | `n` new, `d` delete, `r` refresh, `+`/`-` priority |
| **Compliance** | Governance, oversight queue, proposals, **decision history** (one row per decision, `By` = approver or `policy:<mode>`) | `a` approve, `r` reject, `g`/`o`/`p` focus |
| **Board** | Work in flight — PLAN steps (with their fanned-out members folded under them), direct cluster members, single tasks, the gate that blocks a step — under QUEUED · RUNNING · BLOCKED · DONE · FAILED; cold-starts from the arbiter heartbeat. A non-operator sees the items they asked for (same on Compliance, Comms and the Web GUI) | `c` cancel, `r` retry, `a` reassign, `g` go to the gate, `Enter` detail |
| **Diagnostics** | Golden-prompt suite; **`h` runs the same health checks as `acc-cli doctor`** | `r` run, `a` run all, `e` edit, `h` health |
| **Configuration** | Role→model mapping, live backends | — |
| **Performance** | Throughput and latency | — |
| **Comms** | Inter-agent traffic | — |
| **Infuse** | Guided role infusion | — |
| **Help** | Key reference | `?`, `escape`, `q` |

The Diagnostics health view is not a second implementation — it calls the same
check registry the CLI renders. Two surfaces reporting different answers about
whether a deployment is healthy is a failure mode in itself.

**Headless caveat.** The data-driven screens (Dashboard, Comms, Compliance,
Performance, Diagnostics) render their *empty* state without a live NATS/Redis/LLM
stack. Filesystem and static screens (Ecosystem, Prompt, Configuration) render
meaningfully offline.

---

## 3. Web GUI — `acc-webgui`

FastAPI. 36 endpoints. Authentication is external — oauth2-proxy in front,
Keycloak behind it — so the deployment needs a pre-provisioned `<ns>-webgui`
client in the realm. A missing client presents as `Client not found`, not as a
GUI error.

### Read

| Endpoint | Returns |
|---|---|
| `GET /health` | Liveness. |
| `GET /api/collectives` | Known collectives. |
| `GET /api/snapshot/{collective_id}` | Full current state. |
| `GET /signals/{collective_id}` | Recent bus traffic. |
| `GET /plan/{collective_id}` | The active plan. |
| `GET /models` | The model registry. |
| `GET /episodes/search` | Search durable episodes. |
| `GET /audit` | Audit records. |
| `WS /ws/{collective_id}` | Live updates. |

### Governance

| Endpoint | Does |
|---|---|
| `GET /governance/layers`, `/frameworks` | The governance tiers in force. |
| `GET /governance/proposals` | Open proposals. |
| `POST /governance/proposals/{id}/decision` | Approve or reject. |
| `POST /governance/gap-scan` | Scan for compliance gaps. |
| `POST /oversight` | Act on the oversight queue. |

### Action

| Endpoint | Does |
|---|---|
| `POST /prompt` | Send work to a role. |
| `POST /infuse` | Install a package / apply a role update. |
| `POST /test-llm` | Smoke-test the configured backend. |
| `GET/POST /diagnostics/golden…` | Run, inspect and promote golden prompts. |

---

## 4. What each surface cannot do

Being explicit saves a search for a control that does not exist.

| | CLI | TUI | Web GUI |
|---|---|---|---|
| Edit a configuration **file** | ✅ `config set` | partly (Configuration/Nucleus) | ✅ ordinary keys via `/api/config/set`; **posture keys refused** — `/api/config/propose` raises an oversight proposal instead |
| Report deployment health | ✅ `doctor` | ✅ (`h` on Diagnostics) | ❌ |
| Per-agent status | ✅ `status` | ✅ Dashboard | ✅ snapshot |
| Preview credential scoping | ✅ `secrets scope` | ❌ | ❌ |
| Package build / publish | ✅ `acc-pkg` | install only | install only |
| Verify a session tracelog | ✅ `sessions verify` | ❌ | ❌ (audit view only) |
| Run unattended | ✅ | ❌ needs a TTY | ✅ (API) |

The Web GUI edits ordinary configuration through the same schema the CLI
validates against (`20260817-web-configuration-surface`, v0.8.0); a governance
control that could be edited from a browser session is not a control, so
posture keys (security floor, deploy mode, compliance enforcement) go through
an oversight proposal. The Web GUI also carries the **Board** (five columns
of cards, `GET /api/board/{cid}`, `POST /api/board/control` attributed to the
logged-in principal) and, when `ACC_COMPAT_API_KEYS` is set, the
OpenAI-compatible endpoint (`/v1/chat/completions`, `/v1/models`,
`/v1/tasks/{id}`).

---

## 5. Exit codes

Anything used by a monitor needs a defined exit code.

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `doctor` | no BROKEN check | a BROKEN check | unknown `--check` name |
| `status` | bus reachable **and** every mapped role running | otherwise | unknown `--role` |
| `config check` | no errors | an error | — |
| `config set` | written | refused (schema, secret, unresolvable reference) | — |
| `secrets scope` | reported | — | unknown role |
| `scan` | no findings, or below `--fail-on` | a finding at/above the floor | **the scan could not run** |
| `auth status` | all healthy | a faulted credential | — |
| `profile apply` | applied | validation failed (nothing written) | — |
| `restore` | restored | refused (missing secrets, or would overwrite) | not a readable archive |
| `access check` | admitted | denied | — |
| `memory forget` | reported (dry run or applied) | — | **the backend cannot erase** — nothing was removed |

`status` counts a role that configuration declares but nothing runs as
**unhealthy** — while still reporting it as *not deployed* rather than *failed*,
because those need different responses from a human.

---

## 6. Configuration surfaces

Five files. All are gitignored as of v0.7.0, so a fresh clone has only templates.

| File | Owns | Writable by `config set` |
|---|---|---|
| `acc-config.yaml` | deploy mode, agent, signalling, vector, LLM, security, compliance | ✅ |
| `models.yaml` | the model registry and `role_models` (including failover chains) | ✅ |
| `collective.yaml` | collective id and per-agent overrides | ✅ |
| `catalogs.yaml` | package catalog sources | ✅ |
| `.env` | credentials | ❌ **never** — described by the schema, never written |

`acc-cli config path` prints where each resolves to, and flags the ones that are
read-only, secret-bearing or currently falling back to a template.

---

## 7. What ACC enforces, and what it consumes

Being explicit here matters more than anywhere else in this document: a security
feature that overstates its reach is worse than a smaller one that is honest.

| Concern | Enforced by | ACC's part |
|---|---|---|
| **Who a user is** | the substrate — cluster RBAC, system auth, the web session | resolves a principal from it; never invents a fourth identity model |
| **What a principal may ask an agent** | **ACC** | the tier model; no cluster can express this |
| **Where traffic may go** | the substrate — NetworkPolicy, egress proxy, sandbox | a destination check as defence in depth, so an honest mistake is diagnosable |
| **That an agent never holds a brokered credential** | **ACC** | injected at the boundary; it cannot be exfiltrated from a process that never had it |
| **That code runs in a cage** | the OpenShell gateway | delegates, and **fails closed** when the gateway is unreachable |
| **Which credentials an agent holds** | **ACC** | scoping at the receiving end, so no delivery mechanism has to change |

## See also

- `docs/howto-tui.md` — TUI walkthroughs
- `docs/TESTING.md` — the test suite
- `openspec/changes/` — specifications, including those not yet built
