# Proposal: ACC Operator — Cluster Deployment Guide & Certification Roadmap

| Field      | Value                                        |
|------------|----------------------------------------------|
| Change ID  | ACC-5                                        |
| Date       | 2026-04-15                                   |
| Status     | Draft                                        |
| Author     | Michael                                      |
| Branch     | `feature/ACC-5-operator-deployment-docs`     |
| Ticket     | ACC-5                                        |
| Depends on | ACC-4 (`feature/ACC-4-operator-v0.1.0-initial-scaffold`) |

---

## Problem Statement

The ACC Operator code is fully implemented (commit `[3]`, branch `ACC-4`), but there is no
documented path for a developer or cluster administrator to deploy it onto a real OpenShift or
Kubernetes cluster. Equally, there is no roadmap for certifying the operator so it can appear in
the official Red Hat OperatorHub catalog — a prerequisite for any production or partner rollout.

## Current Behavior

- Operator source lives at `operator/` with a working `Makefile`, CRDs, and OLM bundle skeleton.
- No build pipeline, no registry configuration, and no step-by-step install guide exist.
- No `docs/operator-install-local.md` or `docs/operator-certification.md` files exist.
- The local Podman environment has no Kubernetes/OpenShift runtime available
  for end-to-end operator testing.

## Desired Behavior

Two documents ship in `docs/`:

1. **`operator-install-local.md`** — one page that covers:
   - What the ACC Operator does (capabilities summary, ≤ 250 words)
   - Three deployment methods with detailed step-by-step instructions:
     - *Method A*: Kustomize raw deploy (fastest for development)
     - *Method B*: OLM bundle via `operator-sdk run bundle` (validates OLM flow, required for cert)
     - *Method C*: Internal catalog source (mirrors production OLM install)
   - Lab-cluster option matrix: CRC/OpenShift Local, Kind+OLM, existing OCP 4.14+ lab node
   - Verification checklist (CRD installed, operator running, sample corpus reaches Ready)

2. **`docs/operator-certification.md`** — one page covering:
   - Red Hat certification prerequisites (partner portal account, preflight tool, Konflux/HACBS CI)
   - Step-by-step: preflight checks → Connect submission → pipeline → OperatorHub listing
   - Timeline expectations and common failure modes
   - Separation between "tech preview" (community-operator catalog) and "certified" (RH catalog)

## Success Criteria

- [ ] `docs/operator-install-local.md` exists and is accurate for the current `operator/` codebase
- [ ] A developer with `cluster-admin` on OCP 4.14+ can follow the guide from zero to a `Ready` `AgentCorpus` without consulting any other source
- [ ] `docs/operator-certification.md` exists with all Red Hat Connect steps enumerated
- [ ] The lab deployment option matrix explicitly addresses standalone Podman limitations and proposes an alternative

## Scope

**In scope:**
- Writing `docs/operator-install-local.md`
- Writing `docs/operator-certification.md`
- Adding an OpenSpec change record for this documentation work
- Minor Makefile/config fixes discovered during doc writing (noted as tasks)

**Out of scope:**
- Actually running a certification submission (requires registry credentials and a Red Hat partner account)
- Setting up CI/CD pipelines (separate change)
- Generating CRD YAML from `make manifests` (requires Go toolchain; documented as a prerequisite step, not automated here)
- Writing an e2e test suite (separate change)

## Assumptions

1. The target cluster is OpenShift 4.14+ or Kubernetes 1.27+ with OLM pre-installed.
2. The operator image will be pushed to `quay.io/redhat-ai-dev/acc-operator:0.1.0` as per `Makefile`.
3. CRC (OpenShift Local) version ≥ 2.38 is the recommended lab environment (ships with OLM).
4. The Category A WASM blob (`category_a.wasm`) is provided by the user as a ConfigMap pre-requisite — its generation is out of scope.
5. Local Podman (standalone, no Kubernetes) **cannot** directly run the operator; the lab guide redirects to CRC or a remote OCP 4.14 node.
6. The Red Hat certification path targets the **certified-operators** catalog (not community-operators), which requires a Red Hat Technology Partner account.
