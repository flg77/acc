# 20261004-public-release-channels — tasks

Legend: **[op]** operator-only (signing, publishing, pushing, credentials);
everything else can be prepared on a branch and proven locally.

## Phase 1 — the spec builds on a COPR builder

- [x] 1.1 `.copr/Makefile` with an `srpm` target: builds the wheel
      (`packaging/rpm/build.sh --wheel-only`), lays out `SOURCES/`, calls
      `rpmbuild -bs` with `acc_version` / `acc_release` from
      `packaging/rpm/version.py` and `acc_python` / `acc_python_pkg` from
      `%{?rhel}`; writes into `$(outdir)`.
- [x] 1.2 `packaging/rpm/build.sh`: factor the SRPM-only path out so the
      Makefile and the release pipeline share it (`--srpm-only`).
- [x] 1.3 `acc.spec`: `%if 0%{?rhel} == 9` default `acc_python python3.12`;
      `BuildRequires: gcc` only where a wheel compiles; confirm nothing in
      `%install` assumes the build host's `/app` prefix.
- [~] 1.4 (script written; the matrix itself still has to run on a RHEL host with mock) `packaging/rpm/mock-matrix.sh`: run `mock --enable-network -r <chroot>`
      for `rhel+epel-9`, `rhel+epel-10`, `fedora-42` × `x86_64`, `aarch64`
      (`--forcearch`), then the layout + no-CUDA checks from
      `release-pipeline.sh` step 3 against every result.
- [x] 1.5 `tests/test_rpm_spec.py`: assert the `.copr/Makefile` exists, names
      the spec, and that the README documents both channels with the COPR
      commands (extend the existing channel test).
- [x] 1.6 `packaging/rpm/README.md`: a *COPR* section — source method,
      chroots, network flag, the vendored-wheels alternative and why it was
      not taken.

## Phase 2 — the public channel

- [ ] 2.1 **[op]** (declared: `packaging/rpm/copr-project.sh create`; the token is yours) create COPR project `flg77/acc`: chroots per D3,
      `--enable-net on`, description, homepage = the mirror, delete-after
      unset, keep last 5 builds.
- [ ] 2.2 **[op]** (the same script adds it; `update` brings an existing project to the file) add package `acc`, SCM source: mirror URL,
      subdirectory `.`, spec `packaging/rpm/acc.spec`, method `make_srpm`.
- [ ] 2.3 **[op]** (needs a mirror tag that carries Phase 1's `.copr/Makefile` and spec — the first promote after this change) first build from the current mirror tag; fix what the
      builder reports; record the build id in the Release notes.
- [x] 2.4 `docs/INSTALL.md`: COPR first (`dnf copr enable flg77/acc`,
      `dnf install acc`, `dnf upgrade`), then *inside the lab* for the
      Satellite / acc1 mirror; the *two must agree* check stays.
- [ ] 2.5 **[op]** Proof on two VMs with no lab access (Fedora 42, RHEL 9 or Alma 9):
      install, `acc --version` = `rpm -q`, `acc-cli doctor`, `acc stack up`
      with the edge profile.
- [x] 2.6 CHANGELOG: *Operators* entry for the public channel.

## Phase 3 — the dedicated build host

- [x] 3.1 (role authored; the **[op]** step is copying it into lab-gitops and running it) lab-gitops: `ansible/acc-build/` role — RHEL 10 VM,
      `mock`, `qemu-user-static`, `podman`, `copr-cli`, `rpm-sign`,
      `createrepo_c`, the `acc-build` user + sub-uid range, the chroot configs,
      a pip cache directory, ssh key for the operator's workstation.
- [ ] 3.2 **[op]** (runbook: `packaging/build-host/HOWTO.md` §3, one ssh pipe, fingerprint checked against the channel's served key, lighthouse cleaned only after the proof) move the standalone key: `gpg --export-secret-keys` on
      lighthouse → `GNUPGHOME` on `acc-build` with the same UID; verify a
      test signature against the Satellite's served public key; **then**
      shred the lighthouse copy.
- [x] 3.3 `release-pipeline.sh`, `sign-rpms*.sh`, `build-images.sh`,
      `build-agent-rpm.sh`: `BUILD_HOST` default `acc-build`; `BUILD_ROOT`
      default under its home; README and the `acc-package-release` skill
      updated; `tests/test_image_pipeline.py` if it names the host.
- [ ] 3.4 **[op]** (HOWTO §4) one full release from `acc-build` proved on acc1
      (`rpm -q` = `acc --version`; `rpm -K` verifies).
- [ ] 3.5 **[op]** (HOWTO §5; the host-configs README already says lighthouse builds nothing) lighthouse: remove `/git/ml/agentic/acc-rpm-*`, the pip
      cache and the key directory; the host-config README notes lighthouse
      is inference only.

## Phase 4 — one release routine

- [x] 4.1 `packaging/rpm/release-copr.sh <tag>`: refuses until the mirror carries
      the tag (the promote's job, which stays operator-local and outside this
      repo), then `copr-cli buildscm --method make_srpm` from the mirror's tag
      and `watch-build`; the build id lands in `dist/copr/<v>.build-id`.
- [x] 4.2 `packaging/rpm/release-github.sh <tag>`: fetches the COPR build's SRPM
      and RPMs per chroot and checks each carries the version, renders the
      operator manifests from the tag's tree, writes `SHA256SUMS` and signs it
      keyless (`--no-sign` is explicit), creates the Release with `--verify-tag`
      on the mirror (or replaces assets with `--clobber`), notes from the
      CHANGELOG section at the tag (`changelog_notes.py`, refuses without one).
      Dry-run narrates.
- [x] 4.3 `release-pipeline.sh --public`: legs 3 and 4 after the Satellite proof;
      `--dry-run` narrates all three legs; without `--public` it says how.
- [ ] 4.4 **[op]** (needs the COPR project, Phase 2, and a public client) Proof: public client upgrades from COPR to the new version within
      the same run; the Release's SRPM `rpm -qp --qf '%{VERSION}-%{RELEASE}'`
      equals the COPR build's.
- [ ] 4.5 **[op]** Later: COPR GitHub webhook on the mirror for tag pushes, once 4.1
      has run by hand for two releases.

## Phase 5 — the operator release — SUPERSEDED 2026-10-05

The `operator/` and `console-plugin/` trees left this repository with
`20261004-operator-release-split` OR-11 (`flg77/acc-operator`, public, with
its own CI, registry, release tooling and docs: OR-03..OR-10 there). The
Phase 5 work below was written against the trees that left and is not in
this change any more; what of it is not already covered by the operator
repository's own plan is re-targeted there as findings:

- the ConsolePlugin CR must be created by the manager, not shipped as a
  bundle object (`operator-sdk bundle validate` rejects the kind; it blocks
  the community-operators submission);
- the RBAC marker blocks must be package-level (a blank line after the
  block), or controller-gen drops them and the generated role is empty;
- the CSV's `clusterPermissions` must be generated from the role (they had
  drifted: no NetworkPolicy / EgressFirewall / CiliumNetworkPolicy / SPIRE,
  no `create` on Secrets for the NKey set);
- a `verify-manifests` target that regenerates into a temp dir and diffs;
- the public CatalogSource + Subscription as committed manifests.

## Phase 6 — lifecycle for the edge and the host

- [x] 6.1 `container/Containerfile.bootc`: `FROM quay.io/fedora/fedora-bootc:42`
      (and a `centos-bootc:stream10` variant), COPR repo enabled, `dnf -y
      install acc`, the `acc-stack` unit enabled, the edge profile as the
      default `acc-config.yaml`; built by `build-images.sh --with-bootc`.
      Done as: the base is an `ARG` (Fedora default, CentOS bootc + `ACC_REPO_FAMILY=epel`
      for the EL line), COPR by a written repo file (no dnf plugin needed) or
      `ACC_REPO_URL` for the lab mirror, `deploy_mode: edge` via `acc-cli config set`,
      the feature profile opt-in (`ACC_PROFILE`), bound images opt-in
      (`ACC_BOUND_IMAGES=1`), `bootc container lint` last.
- [x] 6.2 `docs/howto-edge.md`: *Option D — image mode*: `bootc switch`,
      `bootc upgrade`, `bootc rollback`; the first-boot steps for `/etc/acc`.
- [x] 6.3 `acc-cli doctor`: a `channel` row from `dnf check-update acc`
      (read-only; *no channel* when `acc` is not an installed RPM).
- [ ] 6.4 **[op, other repo]** `acc-web-project`: the three install paths on the front page.
- [ ] 6.5 **[op]** (needs podman + a bootc VM; the image is not buildable in the authoring container) Proof: edge VM switches to the bootc image, runs the edge profile
      offline, upgrades to the next release, rolls back.

## Follow-ups filed, not done here

- `acc-runtime-ml` subpackage (torch only where embeddings run).
- Vendored wheels in the SRPM for reproducible COPR builds.
- OpenBao-held signing key (`channels.yml`) instead of the moved standalone key.
- Certified-operators submission.
