# ACC as an RPM (IN-06)

`packaging/rpm/build.sh` builds the wheel (with the data trees under
`acc/_share`, see `packaging/build_share.py`) and then the RPM with
`rpmbuild -ba`; `MOCK_ROOT=<config>` rebuilds the SRPM in mock for another
target (RHEL 9 / 10, Fedora). The wheel is the RPM's `Source0`: the RPM
packages a release artefact, it never re-runs the Python build.

## What it installs

| Path | What | Owner |
|---|---|---|
| `/usr/lib/acc/venv` | the vendored virtualenv (Textual, nats-py, LanceDB, pydantic, FastAPI … are not in EPEL at the pinned versions; this is an internal / COPR package, not a Fedora-review one); **CPU torch**, pinned from PyTorch's CPU index before the wheel resolves its own — the PyPI default drags in ~5 GB of CUDA that a host running the collective in containers never executes, and `%install` fails if any `nvidia/` package slips in; `pip` removed — the package is the only writer | root |
| `/usr/bin/{acc,acc-cli,acc-pkg,acc-tui,acc-webgui,acc-agent,acc-catalog}` | symlinks into the venv | root |
| `/usr/bin/acc-deploy` → `/usr/share/acc/acc-deploy.sh` | the deploy script from the share | root |
| `/usr/share/acc` → the wheel's `acc/_share` | roles, skills, mcps, collectives, `container/production`, `regulatory_layer`, the templates | root |
| `/etc/acc/` | the operator's home: `acc-config.yaml`, `models.yaml`, `collective.yaml`, `catalogs.yaml` (from the templates, `%config(noreplace)`), `acc.env` 0640 root:acc (empty of secrets) | root:acc |
| `/var/lib/acc/{packages,instances,workspaces,logs}`, `/var/log/acc` | state | acc:acc |
| `acc-stack.service` | `acc-deploy up --webgui` / `down` as the `acc` user | — |

The `acc` system user (`sysusers`) **never gains root**: no sudoers entry,
no capabilities, `NoNewPrivileges` in the unit, rootless podman under a
sub-uid range added in `%pre`, lingering enabled in `%post` so a session-less
service can run rootless podman. Images come from `ACC_IMAGE_PREFIX`
(`quay.io/flg77/acc_images` in the unit); the compose file interpolates it.

## Channels (operator decision 2026-09-09)

- the **spearhead** build → the internal Satellite, which is **the distribution
  base** for ACC packages;
- **COPR** ← the public **mirror** only (the same withheld-files rule as the mirror).

A spearhead build never reaches a public repository.

**After every release tag, run the whole pipeline** — the channel must never lag
the tag, because a host upgrading from it believes it is current:

```bash
packaging/rpm/release-pipeline.sh v0.15.0        # tag -> build -> verify -> publish -> prove
RELEASE=2 packaging/rpm/release-pipeline.sh v0.15.0   # same source, new package
packaging/rpm/release-pipeline.sh v0.15.0 --dry-run
```

It builds from `git archive <tag>` (never the working tree), refuses a package
carrying CUDA or missing the layout, publishes, and then upgrades a real client
from the channel and requires `rpm -q` and `acc --version` to **agree** — the
check that catches an upgrade which installs new files and keeps running the old
code. The `acc-package-release` skill drives it.

To publish an already-built package on its own:

```bash
packaging/rpm/publish-satellite.sh dist/rpm/RPMS/x86_64/acc-<version>-<release>.<dist>.x86_64.rpm
```

It copies the file to the Satellite and uploads it with `hammer` **there**, so no
API credentials sit on the build host, and it refuses a snapshot build unless
`ALLOW_SNAPSHOT=1` says so on purpose. Published at:

```
https://sat1.ic3net.internal/pulp/content/ic3net_internal/Library/custom/ACC/acc-spearhead/
```

Clients consume it through a `.repo` file pointing at that URL; the script prints
one.

## The mirror on acc1 — installing without a Satellite subscription

`rpm.ic3net.internal` (acc1) serves a copy of the same channel, so a host can
install ACC without being registered to the Satellite and without its CA:

```bash
sudo tee /etc/yum.repos.d/acc-mirror.repo >/dev/null <<'REPO'
[acc-mirror]
name=ACC packages (mirror of the sat1 channel, on acc1)
baseurl=http://rpm.ic3net.internal:8080/acc-spearhead/
enabled=1
gpgcheck=0
REPO
sudo dnf install acc
```

Port **8080** on purpose: `:80` and `:443` on that address are the LAN frontend,
an nginx L4 proxy to the cluster ingress, rendered by lab-gitops
`ansible/host-frontend-proxy` — the mirror is a separate httpd server beside it
and must never be folded into that config. The DNS record lives in lab-gitops
`ansible/dns/group_vars/bind_servers/zones.yml`, not in the zone file.

`acc-repo-sync.timer` on acc1 pulls from the Satellite hourly
(`/usr/local/sbin/acc-repo-sync`). It syncs **every** package the upstream
metadata advertises, not only the newest: a mirror that downloads the newest
package alone still publishes metadata promising the others, and those 404.

The Satellite stays the source of truth. Only hosts on the lab network can reach
either; **bb3 and saturate3 can reach neither**, so they are staged by file
until an online mirror exists.

## The container half — `acc-runtime`, and the agent image

The package splits. **`acc-runtime`** is what runs — the virtualenv, the commands
and `/usr/share/acc` — and nothing that belongs to a host. **`acc`** adds the host
layer on top (the configuration under `/etc/acc`, the state root, `acc-deploy`,
the unit and the `acc` user) and requires it; it is 21 KB.

In a pod the host layer is dead weight or worse: the unit drives podman-compose
inside a container, the `acc` user is not the UID OpenShift assigns, and a
`0750 acc:acc` state root is unwritable by it.

**One image is built from the RPM**, `acc-agent-core`:

```bash
packaging/images/build-agent-rpm.sh v0.15.0
```

Not the others, and this is a measurement, not a preference: the RPM vendors one
dependency set while the images each carry a hand-picked one. The web GUI
installs ten packages and no ML, which is why it is 487 MB; from the RPM it would
be ~2.5 GB. The agent is the one component whose weight already matches, because
it does embeddings — measured at **2.25 GB from the RPM against 1.98 GB from
source**. A test asserts that no other Containerfile installs the package, so the
decision cannot be reversed silently. Vault `IN-11` has the numbers.

What it buys is one provenance chain: the version a host installs and the version
a pod runs are the same NEVRA from the same channel.

Configuration is not baked. A ConfigMap or a volume at `/etc/acc` supplies it —
the same place the operator already mounts it, and `acc paths` finds it there:

```
$ podman run --rm --user 12345:0 -v ./cm:/etc/acc:ro acc-agent-core-rpm:0.15.0 acc paths
home         home      /etc/acc
share        env       …/acc/_share
state        env       /app/data
config       home      /etc/acc/acc-config.yaml
```

## Building for another EL release

The build produces a package for the release it runs on: an `el10` package does
not install on AlmaLinux 9. Build for another release in a container, which needs
no privileged change on any host:

```bash
podman run --rm -v <tree>:/src:z -w /src almalinux:9 bash -lc '
  dnf -y install rpm-build systemd-rpm-macros python3.12 python3.12-pip python3.12-devel gcc
  PYTHON=python3.12 PYTHON_PKG=python3.12 bash packaging/rpm/build.sh'
```

## Versions in the channel

`packaging/rpm/version.py` maps the project's semantic version to an RPM
`Version` and `Release`, because RPM cannot hold a semantic version directly
(`-` is illegal in both fields, and ordering is per field):

| project version | Version | Release | why |
|---|---|---|---|
| `0.14.4` | `0.14.4` | `1` | the release |
| `0.15.0-rc.1` | `0.15.0` | `0.rc.1` | `0.…` sorts **below** the `1` of the release |
| `0.15.0rc1` | `0.15.0` | `0.rc1` | the PEP 440 spelling, same result |
| `0.14.4` from an untagged or dirty tree | `0.14.4` | `0.<commit stamp>.g<sha>[.dirty]` | a snapshot can never impersonate the release |
| a packaging-only rebuild | unchanged | `RELEASE=2` | same source, new package |

`rpm.labelCompare` is asserted on every one of those orderings in
`tests/test_rpm_semver.py`.

## Building on a RHEL host

```bash
sudo dnf install rpm-build systemd-rpm-macros python3.12 python3.12-pip podman podman-compose
git clone <acc> && cd acc
packaging/rpm/build.sh                       # dist/rpm/RPMS/x86_64/acc-<version>-1.<dist>.x86_64.rpm
rpm -qpl dist/rpm/RPMS/*/acc-*.rpm | head
sudo dnf install dist/rpm/RPMS/*/acc-*.rpm
acc --version && acc paths                   # home /etc/acc, share /usr/share/acc, state /var/lib/acc
sudo -u acc acc-cli doctor --paths
sudo systemctl enable --now acc-stack
```

Proof order (operator): acc1 → bb3 → saturate3. `dnf remove acc` leaves
`/etc/acc` and `/var/lib/acc` in place.

## Known limits of the first package

- RHEL 10 and Fedora ≥ 40 ship 3.12 as `python3` (the default); RHEL 9 needs the `python3.12` module: `PYTHON=python3.12 PYTHON_PKG=python3.12 packaging/rpm/build.sh`.
- The local embedding fallback (`sentence-transformers`) makes `torch` a core
  dependency, so the venv is large even on CPU. The host commands do not run
  it; the cells do. Dropping it from the host package is a dependency-split
  decision, not a packaging one.
- The package is arch-specific (the venv carries compiled wheels: pydantic-core,
  LanceDB, ...); the venv is built on the build host for the target's Python --
  build in mock for a target that differs from the host.
- The unit runs the compose as `acc`; the operator's own TUI still runs as
  the operator (`acc` on the PATH, home `~/.config/acc` unless `ACC_HOME=/etc/acc`).
