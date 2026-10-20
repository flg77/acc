# 20261004-public-release-channels — proposal

**ACC on COPR, a dedicated build host, and a public release path for the operator.**

## Why

Three things are true today and none of them should stay true:

1. **Every release RPM is built on lighthouse.** `packaging/rpm/release-pipeline.sh`
   defaults `BUILD_HOST=lighthouse`, the standalone signing key lives in
   `/home/flg/.acc-rpm-gpg` on the same host, and lighthouse is the inference
   box (`deploy/host-configs/lighthouse-*.yaml`). A release and a model
   evaluation compete for the same machine; a rebuild of lighthouse loses the
   build tree, the pip cache and the key in one go; and nothing on it is
   provisioned by lab-gitops, so there is no second copy of how it is set up.

2. **The COPR channel exists only as a sentence.** `acc.spec`, `INSTALL.md`
   and the install proposal (IN-00 §4b, 2026-09-09) all say "two channels:
   the internal Satellite for the spearhead build, COPR fed from the public
   mirror". No COPR project exists, the spec cannot build on a COPR builder
   as it stands (see *Constraints*), and a community reader of
   `docs/INSTALL.md` is pointed at `rpm.ic3net.internal`, which they cannot
   reach. The edge profile is the one most people would try on a spare box
   first, and today it has no install path outside the lab.

3. **The operator has no release channel at all.** The bundle and catalog
   images build locally (`operator/Makefile` `bundle-build` / `catalog-build`),
   the Makefile names `quay.io/redhat-ai-dev` while the CSV names
   `quay.io/flg77/acc_images`, and the OperatorHub roadmap in
   `docs/operator-certification.md` has not been started. Someone with an
   OpenShift cluster has no `CatalogSource` to apply and nothing to subscribe to.

The questions this proposal answers, in the operator's words: how do we get
off lighthouse; how do our releases reach COPR; how do we release the operator;
should the RPMs live in a GitHub repository; and what is the most effortless
way for the community to install ACC and keep it current.

## Decisions proposed

### D1 — The RPMs are not committed to git. GitHub Releases on the mirror carry the artefacts; COPR builds from the mirror's tag.

A git repository is the wrong store for a 2.3 GB `acc-runtime` package: every
release would add that much history for ever, a clone would pull every release
ever made, and GitHub refuses files over 100 MB without LFS. More importantly,
a repository of binaries is a second copy of the truth with nothing that ties
it to the tag it came from.

What ties an artefact to a tag is a **GitHub Release** on the public mirror
(`flg77/acc`), created on the curated release commit. It carries:

- `acc-<v>.src.rpm` — the SRPM, so anyone can rebuild;
- the `acc` and `acc-runtime` binary RPMs that COPR built, for a host that
  cannot use a repository;
- `install.yaml`, the bundle and catalog image references for the operator;
- `SHA256SUMS` and a keyless cosign signature of it, the same mechanism
  `acc-pkg` already uses for family packs.

COPR does **not** take those files; it builds **from the mirror's git tag**
(the SCM source method: repository URL, `packaging/rpm` as the subdirectory,
`acc.spec` as the spec, `.copr/Makefile` to make the SRPM). That is the whole
point: the public package is built by a public builder from public source, and
a reader can check that the SRPM COPR produced matches the one on the Release.

The spearhead build goes to the Satellite and nowhere else, as decided on
2026-09-09. A dedicated GitHub repository for spearhead RPMs would publish what
`docs/MIRROR-WITHHELD.md` exists to keep back; one for the mirror's RPMs adds
nothing a Release does not. **No new repository.**

### D2 — One dedicated build host, `acc-build`, provisioned by lab-gitops; lighthouse builds nothing after the cut-over.

A RHEL 10 VM in the lab (no GPU; 4 vCPU, 16 GB RAM, 200 GB disk for the mock
chroots and the pip cache), with:

- `mock` and the chroots the channel serves: `rhel+epel-9-x86_64`,
  `rhel+epel-10-x86_64`, and `aarch64` for both via `qemu-user-static`
  (`--forcearch aarch64`; slow but unattended);
- `podman`, `copr-cli`, `rpm-sign`, `createrepo_c`;
- the signing key — either the OpenBao path that `channels.yml` already
  implements, or the standalone key **moved** off lighthouse into the same
  `GNUPGHOME` layout on the new host. Moving the key keeps every subscribed
  host's trust intact; a new key would need every `.repo` re-imported;
- an `acc-build` service user with the sub-uid range rootless podman needs;
- ssh access for the operator's workstation only, which is how
  `release-pipeline.sh` drives it today.

The pipeline changes by one default: `BUILD_HOST=acc-build`, `BUILD_ROOT`
under that user's home. `build.sh` is unchanged. lighthouse keeps serving
models and loses the build role, the key directory and the stale
`acc-rpm-*` work trees.

Why a VM and not GitHub Actions for the spearhead build: the spearhead
tree never leaves the lab, the Satellite is only reachable from the lab, and
the convention that *pushes are the operator's* already holds for images. A
hosted runner would need a self-hosted agent inside the lab, which is a VM
with extra moving parts.

### D3 — The public channel is COPR, project `flg77/acc`, built from the mirror tag, signed by COPR.

| | spearhead channel (today) | public channel (new) |
|---|---|---|
| source | `git archive <tag>` of spearhead | the curated commit on `flg77/acc`, tagged `v<version>` |
| builder | `acc-build` (mock) | COPR builders |
| chroots | el9, el10 × x86_64, aarch64 | `epel-9`, `epel-10`, `fedora-42`, `fedora-43` × `x86_64`, `aarch64` |
| signing | channel key (standalone or OpenBao) | COPR's per-project key, automatic |
| served at | sat1 / acc1 mirror | `copr.fedorainfracloud.org/coprs/flg77/acc` |
| enabled by | a `.repo` file + CA | `dnf copr enable flg77/acc` |
| artefacts kept | Satellite | the Release on the mirror |

The two channels carry the **same NEVR** (`acc-0.27.0-1`) with a different
`%dist`; a host subscribes to one or the other, never both. `aarch64` is new
and is the reason to want COPR at all: the edge profile targets ARM boards and
lighthouse cannot build for them.

The order of a release becomes: tag → spearhead pipeline (Satellite, proved on
acc1) → `acc-promote` (curated commit, tag on the mirror) → COPR build of that
tag → GitHub Release with the SRPM, RPMs and checksums → proof on a **public
client** that has no lab access. The promote step already exists; the COPR
build is one `copr-cli build-scm` the promote routine runs at its end, and a
COPR webhook on the mirror makes it automatic later. A build that fails on
COPR does not block the Satellite channel and vice versa.

### D4 — The operator ships through OLM: a public catalog image first, then community-operators, then certification.

The operator is already at OLM Level 3 with a `replaces` chain, which is the
lifecycle mechanism; what is missing is somewhere to subscribe to.

1. **Now:** the operator, bundle and index images are built from the release
   tag by `packaging/images/build-images.sh` (new `--with-operator`) and pushed
   to `quay.io/flg77/acc_images` as `acc-operator-<v>`, `acc-operator-bundle-<v>`
   and `acc-operator-index-<v>`; the Makefile's `IMG`/`BUNDLE_IMG`/`CATALOG_IMG`
   defaults move to that prefix so the Makefile and the CSV agree. A
   `CatalogSource` manifest naming the index image ships on the GitHub
   Release and in `docs/operator-install-local.md` as method C. A cluster
   installs with one `oc apply` and one `Subscription`
   (`installPlanApproval: Automatic`, channel `stable`), and every later
   release is an OLM upgrade through the `replaces` chain. For plain
   Kubernetes without OLM, the Release carries `install.yaml`
   (`make build-installer`).
2. **Next:** the community-operators submission (`docs/operator-certification.md`
   §8) — the operator appears on operatorhub.io and in the OpenShift console
   without a `CatalogSource`. Prerequisite: the WS-A runbook (the two catalog
   CRDs in the bundle) and a green `operator-sdk scorecard` + `preflight` run
   on the bundle.
3. **Later:** the certified path, which needs the partner account and is
   independent of 2.

The operator keeps its own version line (0.2.x) and consumes runtime images by
tag; a runtime release does not force an operator release. What is coupled is
the **cadence**: the release routine builds the operator images whenever
`operator/` changed since the last operator tag, so the catalog never lags a
CRD change.

> **2026-10-05:** the operator left this repository (`20261004-operator-release-split`,
> OR-11; `flg77/acc-operator`). D4 stands as the shape of the operator's release;
> its implementation is that repository's, and Phase 5 here is superseded (tasks.md).

### D5 — Effortless for the community means: one command to enable, `dnf` to stay current, and an image for the edge.

- **Host install:** `sudo dnf copr enable flg77/acc && sudo dnf install acc`.
  The COPR `.repo` carries `gpgcheck=1` and the key; `dnf upgrade` picks up
  every release; `dnf-automatic` gives unattended updates for those who want
  them. Nothing ACC-specific to learn.
- **Edge (image mode):** a `container/Containerfile.bootc` layering the COPR
  repository and `acc` onto `quay.io/fedora/fedora-bootc` (and `centos-bootc`
  for the EL line), published as `acc_images:acc-bootc-<v>`. An edge node does
  `bootc switch quay.io/flg77/acc_images:acc-bootc` once and `bootc upgrade`
  from then on — atomic, rollback-able, and the lifecycle the edge profile was
  written for (IN-08 named bootc; this is where it lands).
- **Cluster:** the OLM `Subscription` from D4; OLM does the upgrade.
- **Self-check:** `acc-cli doctor` grows a `channel` row that runs
  `dnf check-update acc` and reports *current* / *behind by N* / *no
  channel configured*, so "am I on the latest?" is answered where operators
  already look. It stays read-only.
- **Docs:** `docs/INSTALL.md` leads with the COPR path; the Satellite and the
  acc1 mirror move under an *inside the lab* heading. The website
  (`acc-web-project`) gets the same three commands.

## Constraints that shape the work

**The spec builds the venv with `pip` from PyPI and the PyTorch CPU index during
`%install`.** COPR builders have no network by default. Two ways out:

- *enable network for the project* (`copr-cli modify --enable-net on`) — one
  flag, the build stays as it is, and the SRPM step already has network. The
  build is then not reproducible from the SRPM alone, which is the same
  property the Satellite package has today;
- *vendor the wheels into the SRPM* (`pip download` into a `Source1` tarball,
  `pip install --no-index` in `%install`) — fully offline and reproducible, at
  the cost of a ~1 GB source tarball per release and an aarch64 wheel set that
  must be downloaded for the other architecture.

**Phase 1 takes the first; the second is recorded as the follow-up** if COPR
ever restricts network builds or reproducibility becomes a requirement.
Either way `tests/test_rpm_spec.py` keeps asserting *no CUDA in the venv*.

**Size.** `acc-runtime` is ~2.3 GB installed because `sentence-transformers`
makes `torch` a core dependency (noted in `packaging/rpm/README.md`). COPR
sets no hard limit, but every chroot × architecture keeps every build, so the
project **prunes old builds** (keep the last five) and the dependency-split
question (an `acc-runtime-ml` subpackage carrying torch, required only by the
agent image) is the natural next packaging change. It is not in this
proposal.

**Python.** EPEL 9 needs `python3.12` from AppStream; EPEL 10 and Fedora ship
3.12+ as `python3`. The spec already takes `acc_python` / `acc_python_pkg`
macros; `.copr/Makefile` sets them from `%{?rhel}` so a single spec builds in
every chroot.

**The mirror rule.** Nothing promoted may reference a withheld path. The
`.copr/` directory, the bootc Containerfile and the Release workflow reference
only `packaging/` and `container/`, which are promoted.

## Not in this proposal

- Metadata signing on the Satellite (`repo_gpgcheck`) — `hammer` cannot.
- The `acc-runtime-ml` split — a dependency decision, filed separately.
- The certified-operators submission — needs the partner account first.
- Windows (`uv tool`) and the Podman Desktop extension — unchanged.
- Replacing the standalone key with OpenBao — D2 moves the key as is; the
  switch stays available, as `packaging/rpm/README.md` says.

## Sequence and what proves each step

| Phase | Deliverable | Proof |
|---|---|---|
| 1 | spec + `.copr/Makefile` build in local mock for `epel-9`, `epel-10`, `fedora-42`, x86_64 and aarch64 | `mock --enable-network` green in each; `rpm -qpl` layout check from `release-pipeline.sh` step 3 passes on the mock output |
| 2 | COPR project `flg77/acc`; first build from mirror tag; `INSTALL.md` rewritten | a Fedora VM and a RHEL 9 VM with **no lab access** do `dnf copr enable` + `dnf install acc`; `rpm -q` and `acc --version` agree; `acc-cli doctor` green |
| 3 | `acc-build` host from lab-gitops; key moved; pipeline default switched; lighthouse cleaned | `release-pipeline.sh v<next>` end to end from `acc-build`; acc1 upgrades from the Satellite; the signature verifies against the channel's served key |
| 4 | release routine: promote → COPR → GitHub Release; `acc-package-release` skill updated | one tag produces the Satellite package, the COPR package and the Release in one run, with a dry-run that narrates all three |
| 5 | operator images under `acc_images`; `CatalogSource` + `install.yaml` on the Release; Makefile defaults fixed | `operator-sdk run bundle` and the `CatalogSource` path both install on CRC; an upgrade from the previous CSV walks the `replaces` chain |
| 6 | bootc image; `doctor channel` row; website | an edge VM `bootc switch`es to the image, runs the edge profile, and `bootc upgrade` moves it to the next release; `doctor` says *behind* before and *current* after |
| 7 | community-operators PR | the operator is listed on operatorhub.io |

Phases 1–2 and 5 run in parallel (packaging vs operator). Phase 3 can start
as soon as lab-gitops has the VM; it does not block 2, because COPR is the
builder for the public channel regardless of where the spearhead build runs.

## Operator decisions needed before Phase 2

1. COPR project name `flg77/acc` (matches the mirror) and the chroot set above.
2. Network-enabled COPR builds (Phase 1 default) vs vendored wheels.
3. aarch64 on the Satellite channel too (needs the qemu chroots on `acc-build`)
   or COPR-only for ARM.
4. Move the standalone key to `acc-build`, or take the occasion to go to OpenBao.
5. Operator catalog channel name (`stable`) and whether `alpha` tracks main.
