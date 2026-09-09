# Installing ACC

*`20260909-acc-install` (proposal 055). Stamped for the tree that carries
`acc` (Phase 1: IN-01..IN-05).*

ACC runs as a small set of containers (NATS, Redis, one per cell, the TUI,
optionally the Web GUI) plus a few commands on the host: `acc`, `acc-cli`,
`acc-pkg`, `acc-deploy`. This page is how those commands get onto a host and
find their files. The RPM (IN-06) will package exactly the layout described
here; nothing below is provisional.

## Where ACC lives on a host

One rule, used by every command (`acc paths` prints the result and the
source of each path):

| | Found at | What is there |
|---|---|---|
| **home** — the operator's configuration | `$ACC_HOME`; else `~/.config/acc` when it holds `acc-config.yaml`; else `/etc/acc`; else the checkout you are in | `acc-config.yaml`, `models.yaml`, `collective.yaml`, `catalogs.yaml`, `.env` (secrets, 0600), `trust.yaml`, `tour.done` |
| **share** — the read-only data trees | `$ACC_SHARE`; else a home carrying `roles/`; else the tree shipped inside the package (`acc/_share`); else `<prefix>/share/acc`; else `/usr/share/acc`; else the checkout | `roles/`, `skills/`, `mcps/`, `collectives/`, `container/production/`, `regulatory_layer/`, the `*.example` configs, `acc-deploy.sh` |
| **state** — what the runtime writes | `$ACC_STATE`; else the checkout; else `/var/lib/acc` for a system home; else `~/.local/state/acc` | `instances/`, `packages/`, `sessions/`, `trace/`, `workspaces/` |

Environment variables (`ACC_CONFIG_PATH`, `ACC_ROLES_ROOT`, `ACC_MODELS_PATH`, …)
keep winning over the rule; a container's `/app/...` is just a home the
image sets; a developer inside a checkout sees no change.

## From a wheel (workstation, macOS, a container)

```bash
uv tool install "agentic-cell-corpus[tui] @ <wheel or index>"   # or: pipx install ...
acc --version
acc paths                       # home: none yet — the first `acc` runs the guided setup
acc                             # setup (posture, model, storage) → the tour → the TUI
```

`acc setup` writes the operator's configuration into `~/.config/acc/`; the
data trees come from the package. The stack itself needs Podman and
`podman-compose` on the host:

```bash
acc stack up --webgui           # pulls the release images (ACC_IMAGE_PREFIX, default localhost = built here)
acc                             # attaches; exits 3 with a hint while nothing answers
```

On Windows the commands install the same way (`uv tool install`); the stack
runs in Podman Desktop through the ACC extension or in WSL — `acc stack`
says so when it finds no `bash`.

## From a checkout (developers, and every host until the RPM ships)

```bash
git clone <acc> && cd acc
uv sync                         # or: pip install -e ".[tui]"
./acc-deploy.sh setup           # scaffolds .env and the four *.yaml from their .example
./acc-deploy.sh build && ./acc-deploy.sh up --webgui
acc                             # inside the checkout: the checkout is home, share and state
```

## Trusting a directory

`acc` started in a project directory asks once — `[y = this session / N /
always / below = and everything under it]` — and records the answer in
`~/.config/acc/trust.yaml`. A trusted directory is what the cells mount at
`/workspace` (`acc stack up` from inside it, or `--workspace <dir>`); a
directory never trusted is never mounted, never read by a filesystem skill,
never selected. `acc-cli workspace list|check|trust|deny|revoke` manages the
record; a directory holding many repositories is confirmed a second time.

## The RPM (IN-06)

`packaging/rpm/build.sh` builds the wheel and the RPM on a RHEL host (`rpm-build`, `systemd-rpm-macros`, `python3.12`); `packaging/rpm/README.md` has the details. What it installs:

`/usr/bin/{acc,acc-cli,acc-pkg,acc-deploy}` → a vendored virtualenv under
`/usr/lib/acc/venv`; `/etc/acc/` (home; `acc.env` 0640 root:acc);
`/usr/share/acc/` (share); `/var/lib/acc/` (state, owned by the `acc`
system user, which never gains root); `acc-stack.service` wrapping
`acc-deploy up` / `down`. Two channels: the internal Satellite for the
spearhead build, COPR fed from the public mirror.
