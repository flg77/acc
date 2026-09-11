# Installing ACC

*`20260909-acc-install` (proposal 055). Stamped for v0.17.2 (IN-01..IN-07).*

ACC runs as a small set of containers (NATS, Redis, one per cell, the TUI,
optionally the Web GUI) plus a few commands on the host: `acc`, `acc-cli`,
`acc-pkg`, `acc-deploy`. This page is how those commands get onto a host and
find their files. The RPM packages exactly the layout described here.

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

## From a checkout (developers)

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

## The RPM (EL9 / EL10)

Two packages (since v0.16.0), installed together:

| package | what it holds |
|---|---|
| `acc-runtime` | the vendored virtualenv (`/usr/lib/acc/venv`), the commands (`/usr/bin/acc`, `acc-cli`, `acc-pkg`, `acc-tui`, `acc-webgui`), the data trees (`/usr/share/acc`) — what a container needs too |
| `acc` | the host layer: `/etc/acc/` (home — the four `*.yaml` 0644, secrets in `acc.env` 0640 root:acc), `/var/lib/acc/` (state), `acc-deploy`, `acc-stack.service`, the `acc` system user; requires `acc-runtime` |

The stack runs as the unprivileged `acc` user with rootless Podman (its own
sub-uid range, lingering enabled). It never gains root: no sudoers entry, no
capabilities, `NoNewPrivileges`.

### From the channel (hosts on the lab network)

The internal Satellite is the distribution base; **acc1 mirrors it** so a host
needs no Satellite subscription and no CA:

```bash
sudo tee /etc/yum.repos.d/acc.repo >/dev/null <<'REPO'
[acc]
name=ACC packages
baseurl=http://rpm.ic3net.internal:8080/acc-spearhead/
enabled=1
gpgcheck=0
REPO
sudo dnf install acc                  # pulls acc-runtime, podman, podman-compose
```

Straight from the Satellite instead (`baseurl=https://sat1.ic3net.internal/pulp/content/ic3net_internal/Library/custom/ACC/acc-spearhead/`),
install its CA first — without it dnf only says *"All mirrors were tried"*:

```bash
curl -s -o /tmp/katello-ca.crt http://sat1.ic3net.internal/pub/katello-server-ca.crt
sudo install -m 0644 /tmp/katello-ca.crt /etc/pki/ca-trust/source/anchors/ && sudo update-ca-trust
```

Once the channel carries a signing key, verify every package against the key
the Satellite serves without credentials: `sudo rpm --import
https://sat1.ic3net.internal/katello/api/v2/repositories/30/gpg_key_content`,
then `gpgcheck=1`.

### By file (a host that cannot reach the lab)

The channel carries `el10` packages, which do not install on EL9. Build the
release for the host's EL in a rootless container (`packaging/rpm/README.md`,
"Building for another EL release"), copy both files over, and install them
together — dnf resolves `python3.12` and `podman-compose` (EPEL on EL9):

```bash
sudo dnf install ./acc-runtime-<version>-1.el9.x86_64.rpm ./acc-<version>-1.el9.x86_64.rpm
```

### After the install

```bash
sudo usermod -aG acc $USER            # share the service's state -- then log in again
sudo vi /etc/acc/acc-config.yaml      # the host's configuration; secrets go in /etc/acc/acc.env
sudo systemctl enable --now acc-stack # the collective, as the acc user
acc paths                             # home /etc/acc, share /usr/share/acc, state /var/lib/acc
acc                                   # the TUI, attached to the running stack
```

**The state is shared through group `acc`** (operator decision, 2026-09-11):
`/var/lib/acc` is setgid and group-writable, the service writes with umask
`0002`, and `acc` / `acc-cli` / `acc-pkg` do the same there — so a package you
install with `acc-pkg` is the one the running stack sees. Until you have joined
the group, `acc paths` says so. Joining `acc` also lets you read
`/etc/acc/acc.env`; that is what an operator of this host is for.

On a system install, start and stop the stack with **`systemctl`**, not
`acc stack up` as yourself: that would run a second collective in your own
rootless Podman store.

### Upgrading and removing

```bash
sudo dnf upgrade acc acc-runtime
rpm -q --qf '%{VERSION}\n' acc && acc --version   # the two must agree
sudo systemctl restart acc-stack
```

Check `acc --version`, not only `rpm -q`: an upgrade once left a host running
the previous version while the package query said the new one (fixed since
v0.14.4 by package-owned, hash-checked bytecode). `dnf remove acc acc-runtime`
leaves `/etc/acc` and `/var/lib/acc` in place.

### Building it

`packaging/rpm/build.sh` builds the wheel and the RPM on a RHEL-family host
(`rpm-build`, `systemd-rpm-macros`, `python3.12`); every release is built from
its tag, published and verified on a real host by
`packaging/rpm/release-pipeline.sh <tag>`. `packaging/rpm/README.md` has the
details. Two channels: the internal Satellite for the spearhead build, COPR
fed from the public mirror.
