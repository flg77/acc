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

## As a bootable image (the edge profile)

An edge node boots an image carrying the released RPM instead of installing a
package: `container/Containerfile.bootc`, published as
`quay.io/flg77/acc_images:acc-bootc-<version>`. `bootc switch` once, then
`bootc upgrade` per release, with a rollback. See `docs/howto-edge.md`,
Option D.

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

### From the public channel (COPR)

The public channel is a [COPR](https://copr.fedorainfracloud.org/coprs/flg77/acc/)
project built from the public mirror's release tags, for EL9, EL10 and the
current Fedora, on **x86_64 and aarch64**. It is signed with COPR's own key,
which `dnf copr enable` imports, so a host needs nothing else:

```bash
sudo dnf install -y 'dnf-command(copr)'   # EL: the copr plugin (dnf-plugins-core)
sudo dnf copr enable flg77/acc
sudo dnf install acc                      # pulls acc-runtime, podman, podman-compose
sudo dnf upgrade acc                      # every later release
```

EL9 takes `python3.12` from AppStream and `podman-compose` from EPEL; enable
EPEL first (`dnf install epel-release` on CentOS Stream / Alma / Rocky, the
EPEL repository on RHEL). `acc-cli doctor` has a `channel` row that says
whether the host is current with the channel.

### By file (a host with no repository access)

Every release on the mirror carries the packages COPR built, with checksums:
<https://github.com/flg77/acc/releases>. Pick the two for the host's release
and architecture, verify, install together — dnf resolves `python3.12` and
`podman-compose` (EPEL on EL9):

```bash
v=0.27.0; d=el9; a=x86_64
base=https://github.com/flg77/acc/releases/download/v$v
curl -fsSLO $base/SHA256SUMS -O $base/acc-runtime-$v-1.$d.$a.rpm -O $base/acc-$v-1.$d.$a.rpm
sha256sum --check --ignore-missing SHA256SUMS
sudo dnf install ./acc-runtime-$v-1.$d.$a.rpm ./acc-$v-1.$d.$a.rpm
```

`SHA256SUMS.sigstore.json` beside it is a keyless cosign signature of the
checksums: `cosign verify-blob --bundle SHA256SUMS.sigstore.json
--certificate-identity <the release signer> --certificate-oidc-issuer <issuer>
SHA256SUMS`. The SRPM is there too, for a rebuild on another release.

### Inside the lab (the Satellite channel)

The spearhead build goes to the internal Satellite, which is the distribution
base for lab hosts; **acc1 mirrors it** so a host needs no Satellite
subscription and no CA:

```bash
sudo tee /etc/yum.repos.d/acc.repo >/dev/null <<'REPO'
[acc]
name=ACC packages
baseurl=http://rpm.ic3net.internal:8080/acc-spearhead/
enabled=1
gpgcheck=0
REPO
sudo dnf install acc
```

Straight from the Satellite instead (`baseurl=https://sat1.ic3net.internal/pulp/content/ic3net_internal/Library/custom/ACC/acc-spearhead/`),
install its CA first — without it dnf only says *"All mirrors were tried"*:

```bash
curl -s -o /tmp/katello-ca.crt http://sat1.ic3net.internal/pub/katello-server-ca.crt
sudo install -m 0644 /tmp/katello-ca.crt /etc/pki/ca-trust/source/anchors/ && sudo update-ca-trust
```

The channel carries a signing key; verify every package against the key the
Satellite serves without credentials: `sudo rpm --import
https://sat1.ic3net.internal/katello/api/v2/repositories/30/gpg_key_content`,
then `gpgcheck=1`. A lab host that cannot reach either (bb3, saturate3) takes
the Release's files, above.

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
the group, `acc paths` says so. A group applies only to logins made **after**
`usermod` — an older shell still reports the state as missing. What ACC's own
commands write there stays group-writable; a file you copy in by hand keeps
your shell's umask, so `chmod g+w` it if the service must change it later. Joining `acc` also lets you read
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
details. Two channels: the internal Satellite for the spearhead build, built on
the dedicated host `acc-build`; COPR, built by COPR from the public mirror's tag
(`packaging/rpm/copr-project.sh` declares the project, `release-copr.sh` builds
a release in it, `release-github.sh` puts what it built on the Release).
