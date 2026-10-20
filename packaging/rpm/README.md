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

## COPR — the public channel, built from the mirror's tag

`20261004-public-release-channels`, Phase 1. COPR is the builder for the public
channel: it clones the public mirror at a release tag, makes the SRPM with
`.copr/Makefile`, and rebuilds that SRPM in every chroot the project serves —
`epel-9`, `epel-10`, `fedora-42`, `fedora-43`, each for `x86_64` **and
`aarch64`** (the edge profile's ARM boards, which lighthouse cannot build for).
COPR signs with its own per-project key, so a client needs nothing but:

```bash
sudo dnf copr enable flg77/acc
sudo dnf install acc
sudo dnf upgrade acc            # every later release
```

### What makes the SRPM rebuildable anywhere

An SRPM is rebuilt with **no `--define`** on the command line, by COPR and by
mock alike, so nothing the build host knew may be needed:

- **The version is in the spec.** `build.sh` rewrites the
  `## acc-version-begin … end` block of the spec copy it puts in the SRPM,
  pinning `acc_version`, `acc_wheel_version` and `acc_release` from
  `version.py`. The defaults in the committed spec are `0.0.0-0.unpinned`,
  which sorts below everything: an unpinned spec cannot impersonate a release.
- **The interpreter is chosen where the package is built.** `%if 0%{?rhel} == 9`
  selects `python3.12`; RHEL 10 and Fedora use `python3`. The venv's
  `pythonX.Y` is read inside `%install`, never by a parse-time `%(...)` — the
  spec is parsed before its BuildRequires are installed, and on a fresh chroot
  the interpreter is not there yet.
- **A tree that is not the tagged commit builds a snapshot** (`0.<stamp>.g<sha>`),
  as on the build host. A COPR clone that has lost its tags therefore produces
  a package that sorts below the release, not one that pretends to be it.

```bash
packaging/rpm/build.sh --srpm-only --outdir dist/srpm   # the SRPM, pinned
make -f .copr/Makefile srpm outdir=dist/srpm             # the same, as COPR calls it
packaging/rpm/mock-matrix.sh                             # rebuild it in every chroot
CHROOTS="rhel+epel-9-x86_64" packaging/rpm/mock-matrix.sh dist/srpm/acc-*.src.rpm
```

`mock-matrix.sh` ends every row with the checks `release-pipeline.sh` runs on
the build host's output (no CUDA, the two-package layout, the runtime
requirement). The aarch64 rows run under `qemu-user-static` on an x86_64 host:
slow, but unattended.

### Network during the build — the trade-off taken

`%install` builds the venv with `pip` from PyPI and the PyTorch CPU index, and a
COPR builder has no network by default. Two ways out:

| | network-enabled project | vendored wheels in the SRPM |
|---|---|---|
| how | `copr-cli modify flg77/acc --enable-net on`; `mock --enable-network` | `pip download` into a `Source1` tarball; `pip install --no-index` |
| reproducible from the SRPM alone | no — the same property the Satellite package has today | yes |
| cost | none | ~1 GB of source per release, one wheel set per architecture |

**Phase 1 takes the first.** The second is the recorded follow-up if COPR ever
restricts network builds or reproducibility becomes a requirement; nothing in
the spec's layout changes for it, only where the wheels come from.

### Creating the project (operator, Phase 2)

The project is declared in `packaging/rpm/copr-project.sh` — the chroots, the
network setting, the package's SCM source, how many builds are kept — and
created or updated from that declaration, so a rebuilt project comes out the
same and a change is a diff. The API token is the operator's
(`~/.config/copr`); `status` needs none.

```bash
packaging/rpm/copr-project.sh status             # what COPR has, from its public API
packaging/rpm/copr-project.sh create --dry-run    # the copr-cli calls it would make
packaging/rpm/copr-project.sh create             # the project + the acc package
packaging/rpm/copr-project.sh update             # an existing project, brought to the file
packaging/rpm/release-copr.sh v0.27.0            # the first build: a mirror tag carrying .copr/Makefile
```

The source stays the **mirror**: a spearhead build never reaches a public
repository, and the mirror's curated commit is what the tag names there.

## The build host — `acc-build`

`20261004-public-release-channels`, Phase 3 (D2). Every script here that
builds or signs does it over ssh on **`acc-build`** (`BUILD_HOST`), under
`/srv/acc-build` (`BUILD_ROOT`), as the unprivileged `acc-build` user: a VM
provisioned by the `acc-build-host` role (`packaging/build-host/ansible/`,
written for lab-gitops' role standard) with mock chroots for EL9, EL10 and
Fedora on x86_64 **and aarch64** (under `qemu-user-static`), rootless podman,
the pip cache, and the keyring the standalone key was **moved** into.
`packaging/build-host/HOWTO.md` is the runbook: the VM, the role, the key move,
the proof, and retiring lighthouse — which serves models and builds nothing.

```bash
ssh acc-build bash -s < packaging/build-host/ansible/acc-build-host/files/verify-build-host.sh
BUILD_HOST=somewhere-else packaging/rpm/release-pipeline.sh v0.27.0     # another host, same contract
```

## The release routine — one tag, both channels

`20261004-public-release-channels`, Phase 4. One tag produces the Satellite
package, the COPR package and the GitHub Release, in this order and for these
reasons:

| leg | command | what it needs | what it proves |
|---|---|---|---|
| 1. spearhead → Satellite | `release-pipeline.sh v<v>` | the tag here, the build host, the channel key | acc1 upgrades from the channel; `rpm -q` and `acc --version` agree |
| 2. promote | `acc-promote` (operator, outside this repo) | tests green | the curated commit and `v<v>` on the mirror `flg77/acc`, minus the withheld paths |
| 3. mirror tag → COPR | `release-copr.sh v<v>` | the tag **on the mirror**, `copr-cli` | COPR builds every chroot from the public tree; the build id lands in `dist/copr/<v>.build-id` |
| 4. COPR build → GitHub Release | `release-github.sh v<v>` | the build id, `gh`, `cosign` | the Release on the mirror carries COPR's SRPM and RPMs, `SHA256SUMS` + a keyless signature, and the CHANGELOG section as its notes |

`release-pipeline.sh v<v> --public` runs legs 1, 3 and 4 in one go; the
promote stays a deliberate step in between, which is why the script says so
and stops rather than pushing anything public itself. `--dry-run` narrates all
three legs.

```bash
packaging/rpm/release-pipeline.sh v0.27.0 --standalone-key         # leg 1
acc-promote                                                          # leg 2 (operator)
packaging/rpm/release-pipeline.sh v0.27.0 --standalone-key --public # legs 1 (again, idempotent), 3, 4
#   or, after leg 1 has run once:
packaging/rpm/release-copr.sh v0.27.0 && packaging/rpm/release-github.sh v0.27.0
```

What ties the public artefacts to the tag:

- **COPR builds the mirror's tag**, not anything from here: `release-copr.sh`
  refuses to start when the mirror has no such tag.
- **The Release attaches what COPR built**, fetched from the build's result
  directories and checked (`rpm -qp` must say the release's version) — never
  rebuilt locally. A reader can compare the Release's SRPM with COPR's.
- **The notes are the CHANGELOG section at the tag** (`changelog_notes.py`);
  a version without a section is refused.
- **The operator is not here.** It left for `flg77/acc-operator` (OR-11) and
  releases its own bundle, catalog and install manifests from there.
- **`SHA256SUMS` is signed keyless** (`cosign sign-blob --bundle`), the
  mechanism family packs already use; `--no-sign` exists so an unsigned
  Release is a choice, never an accident. Verify with
  `cosign verify-blob --bundle SHA256SUMS.sigstore.json --certificate-identity <signer> --certificate-oidc-issuer <issuer> SHA256SUMS`.

Later (4.5): a COPR webhook on the mirror for tag pushes makes leg 3
automatic; it is deliberately not wired until the routine has run by hand for
two releases.


Each channel has **one** GPG signing key, made once by lab-gitops
`ansible/satellite-content/playbooks/channels.yml`, which keeps the private half
**only in OpenBao** (`lab/satellite/channels/<org>/<product>/<repo>`) and
registers the public half as the channel's content credential. A rebuilt lab gets
the same key back, so hosts that trust a channel keep trusting it.

The release pipeline signs both packages before it publishes them:

```bash
export BAO_TOKEN=…                                  # never committed, never echoed
packaging/rpm/release-pipeline.sh v0.16.0           # … verify → SIGN → fetch → publish → prove
packaging/rpm/sign-rpms.sh ic3net_internal/ACC/acc-spearhead acc-build:<dir>    # on its own
```

`sign-rpms.sh` reads the key from the vault into memory and pipes it over ssh into
a signer container on the build host that has **no network** and keeps its keyring
on a **tmpfs**: the key never touches a disk and cannot leave. Every signature is
then checked against the channel's **public** key alone.

`publish-satellite.sh` refuses an unsigned package into any channel that carries
a key: a *subscribed* host is handed `gpgcheck=1` for such a channel, so an
unsigned package there is an install failure on every one of them. It checks on
the Satellite, against the key the channel itself serves. A channel with no key
yet still takes packages, which is the only thing `--unsigned` is for.

A client verifies with nothing but what the Satellite serves:

```bash
sudo rpm --import https://sat1.ic3net.internal/katello/api/v2/repositories/<id>/gpg_key_content
# then gpgcheck=1 in the .repo
```

Signed repository metadata (`repo_gpgcheck=1`) is not available: `hammer` exposes
no metadata-signing option for custom yum repositories on this Satellite.

### The standalone key — what actually signs `acc-spearhead` today (2026-09-22; moved to `acc-build` in Phase 3)

The OpenBao path above is what `channels.yml` is *built* to do; it was never
run, because it needs a `BAO_TOKEN` under the `ops` policy and setting that up
was more than the operator wanted right now. Instead, `ic3net_internal/ACC/acc-spearhead`
is signed with a **plain key**: generated once with `gpg --gen-key` directly on
the build host of the time (lighthouse, `GNUPGHOME=/home/flg/.acc-rpm-gpg`, RSA 4096, no
expiry, UID `ACC Release Signer <hostmaster@ic3net.internal>`) and since Phase 3
**moved, not regenerated**, to `acc-build:/srv/acc-build/.acc-rpm-gpg`
(`packaging/build-host/HOWTO.md` §3), its public half
registered as the Satellite content credential `acc-spearhead-signing` (id 7)
and wired onto the `ACC` product and the `acc-spearhead` repository.

```bash
packaging/rpm/release-pipeline.sh v0.22.0 --standalone-key
packaging/rpm/sign-rpms-standalone.sh acc-build:<dir>             # on its own
```

**The trade-off, plainly**: `sign-rpms.sh` never lets the private key touch a
disk and signs inside a network-less container with a tmpfs keyring;
`sign-rpms-standalone.sh` signs with a key that sits in an ordinary directory
on the build host, protected by filesystem permissions and nothing else. If that
stops being enough, `channels.yml` already exists and does the harder version
— switching needs no code change here, only pointing the Satellite's content
credential at the OpenBao-held key instead of this one.

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

Proof order (operator): acc1 → saturate3. bb3 is the RHOAI host and consumes
the agent image, not the RPM (operator, 2026-09-10). `dnf remove acc` leaves
`/etc/acc` and `/var/lib/acc` in place.

The state is shared with the operator through group `acc` (IN-07, operator
2026-09-11): `/var/lib/acc` is `2770 acc:acc`, the unit runs with `UMask=0002`,
and `acc` / `acc-cli` / `acc-pkg` keep what they write there group-writable.
An operator joins with `sudo usermod -aG acc $USER`; `acc paths` says so until
they have.

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
