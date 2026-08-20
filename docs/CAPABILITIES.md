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

## 1. CLI — `acc-cli`

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
| `acc-cli oversight pending/approve/reject` | The human decision point. An approval dispatches exactly one action — the claim is atomic, so a six-agent collective performs one install, not six. |
| `acc-cli oversight submit` | Raise a proposal for a decision. |
| `acc-cli sessions list/show/verify` | Replay the durable, hash-linked tracelog. **`verify` re-checks each recorded step against the Category A/B/C gates** — a tamper-evident audit of what an agent actually did. |
| `acc-cli plan submit/watch` | Submit a plan and follow it. |
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

Textual. Twelve screens; the navigation bar moves between them.

| Screen | What it is for | Keys |
|---|---|---|
| **Dashboard** | Collective at a glance | — |
| **Prompt** | Send work to a role and watch it reason | `ctrl+s` send, `shift+tab` cycle mode, `ctrl+l` clear |
| **Ecosystem** | Roles, skills, MCPs; infuse from here | — |
| **Marketplace** | Browse and install packages | `/` filter, `enter` install, `r` refresh, `+`/`-` rate |
| **Catalogs** | Catalog sources and priority | `n` new, `d` delete, `r` refresh, `+`/`-` priority |
| **Compliance** | Governance, oversight queue, proposals | `a` approve, `r` reject, `g`/`o`/`p` focus |
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
| Edit a configuration **file** | ✅ `config set` | partly (Configuration/Nucleus) | ❌ |
| Report deployment health | ✅ `doctor` | ✅ (`h` on Diagnostics) | ❌ |
| Per-agent status | ✅ `status` | ✅ Dashboard | ✅ snapshot |
| Preview credential scoping | ✅ `secrets scope` | ❌ | ❌ |
| Package build / publish | ✅ `acc-pkg` | install only | install only |
| Verify a session tracelog | ✅ `sessions verify` | ❌ | ❌ (audit view only) |
| Run unattended | ✅ | ❌ needs a TTY | ✅ (API) |

The Web GUI is deliberately read-mostly for configuration. Configuration through
the web interface is specified (`20260817-web-configuration-surface`) and depends
on the configuration schema, which now exists.

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

## See also

- `docs/howto-tui.md` — TUI walkthroughs
- `docs/TESTING.md` — the test suite
- `openspec/changes/` — specifications, including those not yet built
