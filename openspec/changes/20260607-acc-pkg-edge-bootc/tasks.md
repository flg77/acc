# 20260607-acc-pkg-edge-bootc — tasks

Three sub-slices.  Recommended order: **3.2 → 3.1 → 3.3**.

3.2 first because the Jinja template + per-base configs land
without any bundler code — they're operator-reviewable artefacts.
3.1 builds on 3.2's templates.  3.3 adds the air-gap layer once
3.1's bundler exists.

## Sub-slice 3.2 — Per-base configs + Jinja template

### 3.2.1 `gitops/bootc/Containerfile.bootc.j2`
- [ ] Jinja template with sections:
  * `FROM {{ base.from }}`
  * `# ACC layer` — `COPY ./acc /opt/acc/`
  * Loop `{% for pkg in packages %}` — install per pkg
  * Final `RUN bootc image upgrade` hook (no-op build-time; flips
    on first boot)

### 3.2.2 Per-base YAML configs
- [ ] `gitops/bootc/hummingbird.yaml`:
  * `from: quay.io/hummingbird-community/bootc-os:latest`
  * Minimal RPM additions (curl, jq for the systemd unit
    that nudges hibernate signals)
- [ ] `gitops/bootc/rhel-bootc.yaml`:
  * `from: registry.redhat.io/rhel9/rhel-bootc:9.5`
  * Subscription-manager dance documented for the operator
- [ ] `gitops/bootc/microshift.yaml`:
  * `from: registry.redhat.io/openshift4/microshift-bootc`
  * KubeConfig auto-creation hook

### 3.2.3 Operator runbook
- [ ] `gitops/bootc/README.md`:
  * Prereqs (podman + cosign + acc-pkg)
  * Build → push → `bootc switch` on edge host
  * Troubleshooting: PVC sizing, network mode, TPM2 attestation

## Sub-slice 3.1 — `acc/pkg/bundle.py` bundler core

### 3.1.1 Pydantic models
- [ ] `BaseConfig` — base, from, rpm_additions[], extra_files[]
- [ ] `BundleSpec` — base ref, packages[], collective_yaml_ref,
      output_path
- [ ] `BundleResult` — output_path, content_sha256, manifest

### 3.1.2 Resolution + materialisation
- [ ] Walks `BundleSpec.packages` via `acc.pkg.fetch.resolve_constraint`
- [ ] Downloads tarballs + sigs + certs into a build dir
- [ ] Runs cosign + EC verify (Stage 1.2) before inclusion
- [ ] Writes packages into `/opt/acc/packages/` layer

### 3.1.3 Containerfile generation
- [ ] Loads template from `gitops/bootc/Containerfile.bootc.j2`
- [ ] Renders against base config + resolved packages
- [ ] Writes to build dir

### 3.1.4 Podman invocation
- [ ] `podman build --target oci-bootc -t <tag> -f Containerfile.bootc <build-dir>`
- [ ] Export as OCI tarball when `--output <path>` requests it
- [ ] Sha256 sidecar written next to output

### 3.1.5 CLI subcommand
- [ ] `acc-pkg bundle <collective.yaml> --base <flag> -o <image-tar>`
- [ ] Flags: `--base`, `--output`, `--no-verify` (operator-explicit
      bypass for dev), `--builder` (podman/buildah)
- [ ] Exit codes mirror existing acc-pkg contract; new `EXIT_BUILD`
      for podman build failures

### 3.1.6 Tests
- [ ] Mocked subprocess for podman build
- [ ] Containerfile generation snapshot tests per base
- [ ] Catalog resolution via existing fetch test fixtures
- [ ] ~25 new tests; full sweep stays green

## Sub-slice 3.3 — Air-gap (`--offline`)

### 3.3.1 Sigstore cert bundle embedding
- [ ] Embeds Fulcio root + intermediate CA certs into the image
      at `/etc/acc/sigstore/`
- [ ] Renders `cosign verify --tuf-mirror` to use the bundled
      certs

### 3.3.2 EC policy embedding
- [ ] Copies `policy/enterprise-contract.yaml` (Stage 1.2 default
      or operator-specified) into `/etc/acc/policy/`
- [ ] `acc-pkg install` at boot honors `--ec-policy` pointed at
      the bundled file

### 3.3.3 File-mode catalog rendering
- [ ] Renders `/etc/acc/catalogs.yaml` with a single `mode: file`
      catalog entry at `/opt/acc/packages/`
- [ ] All required signers point at bundled pubkeys (no OIDC)

### 3.3.4 Eval panel snapshot
- [ ] If `ACC_RHOAI_PANEL_PATH` set, snapshot the panel YAML into
      the image at `/etc/acc/evals/curated-llms.yaml`
- [ ] Otherwise document operator-supplied path in the bundler's
      `--curated-llms <path>` flag

### Verification (sub-slice 3.3)
- [ ] Bundle built with `--offline` boots on a network-isolated VM
      (operator-side smoke; no automated test for actual offline)

## Open strategic decisions (block sub-slice starts)

- [ ] Q1: Default base when `--base` omitted — `hummingbird`
      (recommended) vs `rhel-bootc`
- [ ] Q2: Builder binary — `podman` (default) + document `buildah`
- [ ] Q3: MicroShift size warning at INFO level — yes (recommended)
