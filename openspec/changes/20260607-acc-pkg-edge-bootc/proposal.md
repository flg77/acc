# 20260607-acc-pkg-edge-bootc — proposal

## Why

Stages 0-2 ship the substrate, the trust chain, the public hub,
and the discovery surfaces.  But every install today targets a
**connected** machine — DC or laptop.  The architecture's edge
story (brainstorm Q5) requires one more capability: producing a
**bootc image** layering ACC core + a pinned package set on top of
the chosen base OS, so a disconnected edge host can boot the full
agent runtime without ever talking to a hub.

The brainstorm picks Fedora Hummingbird as the canonical agentic-
edge OS but treats `--base` as a per-bundle build flag, not a
strategy decision (per Q5 clarification).  Stage 3's job is to
implement the bundler with first-class support for four base
choices: `hummingbird`, `rhel-bootc`, `microshift`, and operator-
supplied custom OCI bootc images.

## Scope

Three sub-slices:

| Sub-slice | What ships | Operator gates |
|---|---|---|
| 3.1 | `acc/pkg/bundle.py` — bundler core + Containerfile generation + sha256 sidecar | None |
| 3.2 | Per-base configs at `gitops/bootc/{hummingbird,rhel-bootc,microshift}.yaml` + Jinja `Containerfile.bootc.j2` | None |
| 3.3 | Air-gap bundle (`--offline`) — embeds Sigstore cert bundle + EC policy + offline-mode catalogs.yaml in the image | None |

All work lives inside `acc/` (this repo) — no external repo
coordination, no operator-side `gh repo create`.  Stage 3 is the
most contained of the three remaining stages.

### What's NOT in scope

* MicroShift OCP cluster image — Stage 3 layers ACC on top of
  MicroShift's existing bootc base; the OCP plumbing is upstream.
* Hummingbird upstream changes — ACC consumes the published
  `quay.io/hummingbird-community/bootc-os` image; if Hummingbird
  promotion slips (brainstorm Q5 risk), the `--base rhel-bootc`
  fallback covers production.
* `bootc switch` orchestration on edge hosts — that's the operator's
  ansible/RHACM workflow, not ACC tooling.

## Per-sub-slice file inventory

### 3.1 — `acc/pkg/bundle.py` bundler core

| Component | Purpose |
|---|---|
| `BundleSpec` Pydantic model | base, packages, collective_yaml_ref, output_path, extra_layers |
| `bundle(spec)` | Resolves packages via `fetch_and_install` semantics (catalog → download → verify); writes Containerfile from template; invokes `podman build --target oci-bootc` |
| Content-tree hash | Same scheme as `acc/pkg/build.py` — bundle is deterministic across runs |
| Sidecar sha256 | Written next to the bundle so catalogs can index it |
| `acc/pkg/cli.py` new subcommand | `acc-pkg bundle <collective.yaml> --base <flag> -o <image-tar>` |

### 3.2 — Per-base configs + Jinja template

| File | Content |
|---|---|
| `gitops/bootc/Containerfile.bootc.j2` | Jinja template; loop over packages + extra layers |
| `gitops/bootc/hummingbird.yaml` | `from: quay.io/hummingbird-community/bootc-os:latest`; minimal RPM additions |
| `gitops/bootc/rhel-bootc.yaml` | `from: registry.redhat.io/rhel9/rhel-bootc:9.5`; minimal additions |
| `gitops/bootc/microshift.yaml` | `from: registry.redhat.io/openshift4/microshift-bootc`; single-node OCP |
| `gitops/bootc/README.md` | Operator runbook: build → push → `bootc switch` on edge host |

### 3.3 — Air-gap (`--offline`)

| Feature | Why |
|---|---|
| Embed Sigstore cert bundle | Cosign verify offline |
| Embed EC policy | Stage 1.2 policy enforced without network access |
| Render `/etc/acc/catalogs.yaml` with `mode: file` | Points at the bundle's own `packages/` layer |
| Embed an offline RHOAI panel reference (or operator-supplied panel YAML) | Eval can run against the bundled models without external lookup |

## Impact

* **Affected code:**
  * NEW `acc/pkg/bundle.py` (~250 LOC; mirrors `build.py` structure)
  * MODIFY `acc/pkg/cli.py` — `bundle` subcommand
  * NEW `gitops/bootc/*.yaml` + Jinja template
  * NEW `tests/pkg/test_bundle.py` (~25 tests with mocked `podman build` subprocess)
* **New env knobs:**
  * `ACC_BOOTC_BUILDER` (default `podman`; can substitute `buildah` for rootless edge builds)
* **Tests:** ~25 new (mocked podman + Jinja template render + Containerfile shape)
* **Backward compatibility:** purely additive

## Open strategic decisions

1. **Default base** when `--base` is omitted — `hummingbird`
   (recommended per brainstorm Q5) or `rhel-bootc` (more
   conservative).  Recommendation: `hummingbird` with a
   `--fallback-base` flag for the slipped-promotion contingency.
2. **Builder binary** — `podman build` (default), `buildah`,
   or both?  Recommendation: `podman` default, document `buildah`
   path for rootless edge.
3. **MicroShift opt-in size** — full MicroShift adds ~1.5 GB
   per brainstorm Q5.  Do we ship the size warning in the CLI on
   `--base microshift` selection, or trust the operator's read of
   the docs?  Recommendation: emit at INFO level so it shows in
   CI logs.

## What stays open after Stage 3

* Federation (Phase F) — A2A cross-hub discovery, private corporate
  hubs.  Substrate exists in `acc/a2a/` from Stage 0; wire-up is
  a separate proposal.
* SBOM generation as a sibling sidecar to the cosign signature
  (Sigstore + SPDX) — important for supply-chain audit beyond what
  Stage 1.2's EC policy already provides.

## References

* Stage 0 pilot: `openspec/changes/20260603-acc-pkg-pilot/proposal.md`
* Stage 1: `openspec/changes/20260605-acc-pkg-trust-and-assistant/proposal.md`
* Stage 2: `openspec/changes/20260606-acc-ecosystem-hub-and-scale/proposal.md`
* Architecture: `openspec/changes/20260604-role-ecosystem-strategy/ecosystem-implementation.md`
* Brainstorm Q5: `<vault>/ACC Openspec/ACC Role Ecosystem/Ecosystem split — brainstorm.md`
* Hummingbird sources: Fedora Magazine, Linux Magazine, Help Net Security
  (full citations in the brainstorm).
* Naming convention: `openspec/RENAMES.md` (functional).
