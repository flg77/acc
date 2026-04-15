# ACC Operator — Red Hat Certification & OperatorHub Roadmap

> **Source:** `operator/` · **Version:** 0.1.0 · **Target catalogs:** community-operators (tech preview) → certified-operators (GA)

## Quick Reference

| Stage | Catalog | Review | Time estimate |
|-------|---------|--------|---------------|
| Tech preview | [community-operators](https://github.com/k8s-operatorhub/community-operators) | Automated CI only | 1–3 days after PR merge |
| Certified (GA) | Red Hat OperatorHub (embedded in OCP console) | Red Hat partner review | 2–4 weeks first submission |

---

## 1. Overview & Catalog Targets

Two independent catalogs can carry the ACC Operator:

### 1.1 Community Operators (operatorhub.io)

Hosted at `github.com/k8s-operatorhub/community-operators`. Bundles are validated by
automated CI (`operator-courier`, preflight, scorecard) but receive **no Red Hat engineering
review**. Approval is fast (≤ 24 h after CI passes). The operator appears on
[operatorhub.io](https://operatorhub.io) and can be installed on any OLM-enabled cluster.

Use this path for:
- Tech preview / early adopter access before certification is complete
- Receiving community feedback on the UX before committing to a certified bundle layout

### 1.2 Certified Operators (Red Hat OperatorHub)

Hosted at `github.com/redhat-openshift-ecosystem/certified-operators`. Requires a **Red Hat
Technology Partner account** and passes through a Red Hat reviewer queue. After approval the
operator appears in the embedded OperatorHub console on every OpenShift cluster — no additional
catalog source required.

Use this path for:
- Enterprise customers who require Red Hat support-grade assurance
- Operators that manage cluster-scoped resources (ClusterRole, ConstraintTemplate) — certified
  operators carry an implicit trust level that community-only bundles do not

### 1.3 Recommended Sequence

```
1. Pass preflight locally (§3)
2. Submit to community-operators (§8) — tech preview, quick turnaround
3. Iterate on certification feedback in parallel
4. Submit to Red Hat Connect (§4) — certified, appears in OCP console
```

The two submissions are **independent** — community-operators acceptance does not accelerate
certified-operators review, but it validates the bundle format before facing Red Hat's pipeline.

---

## 2. Prerequisites

### 2.1 Red Hat Technology Partner Account

Required for certified-operators submission only (not community-operators).

1. Register at <https://connect.redhat.com> → **Become a Partner**
2. Choose **Technology Partner** tier (free)
3. Accept the Partner Agreement
4. Create a new **Operator** project under **Software → Operators → New project**

### 2.2 Container Registry

Both the operator image and bundle image must be **publicly readable** during certification
review. Use one of:

- `quay.io/<your-org>/acc-operator:0.1.0` (operator image)
- `quay.io/<your-org>/acc-operator-bundle:0.1.0` (OLM bundle image)
- `quay.io/<your-org>/acc-operator-index:0.1.0` (optional index/catalog image)

On quay.io, set each repository's **Visibility** to **Public** before submitting.

### 2.3 `preflight` CLI

```bash
# Install via go install
go install github.com/redhat-openshift-ecosystem/openshift-preflight/cmd/preflight@latest

# Verify
preflight --version
# preflight version 1.x.x ...

# Obtain a Pyxis API token at:
# connect.redhat.com → Account → API tokens → New token (scope: Partner)
export PFLT_PYXIS_API_TOKEN=<your-token>
```

### 2.4 `operator-sdk` scorecard

The scorecard runner is bundled with `operator-sdk`. Ensure `bin/operator-sdk` is on PATH or
use the Makefile-managed download:

```bash
cd operator/
make operator-sdk        # downloads operator-sdk v1.36 into operator/bin/
export PATH=$PATH:$(pwd)/bin
operator-sdk version
```

### 2.5 Live Cluster for Bundle Checks

The `preflight check operator` command deploys the operator into a live cluster. Use CRC or a
remote OCP 4.14+ node (see `docs/operator-install-local.md §3` for cluster setup). The cluster
must be accessible via `--kubeconfig`.

---

## 3. Step 1 — Preflight Checks

Preflight validates two independent targets: the **container image** and the **OLM bundle**.
Run both and resolve all failures before submitting to Red Hat Connect.

### 3.1 Check the Operator Container Image

```bash
export IMG=quay.io/<your-org>/acc-operator:0.1.0
export PFLT_PYXIS_API_TOKEN=<token>

preflight check container $IMG \
  --pyxis-api-token $PFLT_PYXIS_API_TOKEN \
  --submit   # omit --submit for a dry run; add it only when ready to log results
```

**Standard checks and ACC-specific guidance:**

| Check | What it tests | ACC status |
|-------|--------------|------------|
| `HasRequiredLabel` | `vendor`, `name`, `version`, `summary`, `description` labels on image | ✅ Set in `operator/Containerfile` |
| `HasLicense` | License file present in image | ✅ UBI10 base includes LICENSE |
| `HasUniqueTag` | Image tag is not `latest` | ✅ Tagged `0.1.0` |
| `LayerCountAcceptable` | ≤ 40 layers | ✅ Multi-stage build keeps layers low |
| `HasNoProhibitedContainerSpec` | No `privileged: true`, no host namespaces | ✅ UID 65532; no host mounts |
| `RunAsNonRoot` | Container does not run as UID 0 | ✅ `USER 65532` in Containerfile |
| `HasModifiedFiles` | Files not owned by the base image are present | ✅ Manager binary installed |
| `HasProhibitedPackages` | No blacklisted RPMs (e.g., `telnet`, `ftp`) | ✅ UBI10-minimal base |
| `BasedOnUbi` | Base image is UBI | ✅ `registry.access.redhat.com/ubi10/ubi-minimal` |
| `CertifiedImagesNotExpired` | Referenced images not past EOL | ✅ UBI10 (supported) |
| `ValidateOperatorBundle` | N/A for container check | — container only |
| `ScorecardBasicSpecSuite` | N/A for container check | — bundle check only |

### 3.2 Check the OLM Bundle

The bundle check deploys the operator into a live cluster and runs scorecard tests.

```bash
export BUNDLE_IMG=quay.io/<your-org>/acc-operator-bundle:0.1.0

# Ensure the operator namespace exists
kubectl create namespace acc-operator-system --dry-run=client -o yaml | kubectl apply -f -

preflight check operator $BUNDLE_IMG \
  --pyxis-api-token $PFLT_PYXIS_API_TOKEN \
  --kubeconfig ~/.kube/config \
  --namespace acc-operator-system \
  --serviceaccount acc-operator-controller-manager
```

**Bundle-specific checks:**

| Check | What it tests | ACC status |
|-------|--------------|------------|
| `ValidateOperatorBundle` | Bundle structure, CSV validity, CRD/CSV alignment | ✅ Generated by `make bundle` |
| `BundleIndexImageAnnotations` | `metadata/annotations.yaml` present; installModes correct | ✅ OwnNamespace + SingleNamespace enabled |
| `HasMinKubeVersion` | `spec.minKubeVersion` set in CSV | ✅ `1.27.0` |
| `ScorecardBasicSpecSuite` | CR spec completeness, status fields | ✅ Sample CRs provided |
| `ScorecardOLMSuite` | CRD validation, owner references, status updates | Requires live cluster |

### 3.3 Run the Operator SDK Scorecard

The scorecard provides additional OLM-specific validation beyond preflight:

```bash
cd operator/

# Build and push bundle first
make bundle-build BUNDLE_IMG=$BUNDLE_IMG
make bundle-push  BUNDLE_IMG=$BUNDLE_IMG

# Run scorecard against live cluster
operator-sdk scorecard $BUNDLE_IMG \
  --kubeconfig ~/.kube/config \
  --namespace acc-operator-system \
  --wait-time 120s
```

Expected output: all `basic-check-spec` and `olm-*` tests `PASS`. The `olm-status-descriptors`
test is most commonly advisory (not blocking) for v0.1.0 operators.

---

## 4. Step 2 — Red Hat Connect Submission

> ℹ️ **Verify against current Red Hat Connect UI** — the portal navigation changes with partner
> program updates. Steps below reflect the UI as of Q1 2026.

### 4.1 Create a Certification Project

1. Log in to <https://connect.redhat.com>
2. Navigate to **Software** → **Operators** → **Publish an Operator**
3. Click **Create project** and select **Operator bundle image**
4. Fill in:
   - **Product name:** `Agentic Cell Corpus Operator`
   - **Short description:** `Kubernetes Operator for biologically-inspired multi-agent AI deployments on OpenShift`
   - **Long description:** copy from `bundle/manifests/acc-operator.clusterserviceversion.yaml` `spec.description`
   - **Distribution method:** `Non-Red Hat container image registry (Quay.io)`

### 4.2 Link the Bundle Image

1. Under **Bundle image** enter: `quay.io/<your-org>/acc-operator-bundle:0.1.0`
2. Click **Verify** — Connect pulls the image metadata and validates the labels
3. Copy the image **SHA256 digest** shown after verification (format: `sha256:abc123...`)
   - Pin the production submission to a digest, not a floating tag

### 4.3 Attach Preflight Results

```bash
# Generate a preflight results file
preflight check operator $BUNDLE_IMG \
  --pyxis-api-token $PFLT_PYXIS_API_TOKEN \
  --kubeconfig ~/.kube/config \
  --namespace acc-operator-system \
  --serviceaccount acc-operator-controller-manager \
  --artifacts ./preflight-artifacts/

# The results file is at preflight-artifacts/results.json
```

Upload `results.json` in the Connect portal under **Test results → Upload preflight results**.

### 4.4 Submit for Review

1. Review the **Checklist** tab — all items must show green before submission
2. Click **Submit for review**
3. Note the **Project ID** (format: `ospid-XXXX`) — used for support requests and re-submissions

---

## 5. Step 3 — Certification Pipeline (Konflux / HACBS)

> ℹ️ **Verify against current Red Hat Connect UI** — the pipeline infrastructure migrated from
> the legacy CI to Konflux (formerly HACBS) in 2024. Portal names may change.

### 5.1 What the Pipeline Tests

After submission, Red Hat's Konflux CI automatically runs:

| Stage | What is tested |
|-------|---------------|
| **Preflight re-run** | Operator container image against all standard checks (fresh environment) |
| **Bundle validation** | `operator-sdk bundle validate` + OLM scorecard suite |
| **OCP version matrix** | Operator deployed on OCP 4.14, 4.15, 4.16, 4.17, 4.18 (all supported versions in the `spec.minKubeVersion` range) |
| **scorecard** | `basic-check-spec` and `olm-suite` on each OCP version |
| **Functional smoke** | If provided: custom scorecard tests in `bundle/tests/scorecard/` |

The pipeline runs automatically — no manual trigger is needed after submission.

### 5.2 Monitor Pipeline Status

In the Connect portal:
1. Navigate to your project → **Pipeline runs** tab
2. Each run shows stage-level pass/fail with expandable logs
3. A failed run blocks the review queue but does **not** require creating a new project

### 5.3 Resubmit After a Fix

```bash
# Fix the issue, rebuild, and retag
make docker-build IMG=quay.io/<your-org>/acc-operator:0.1.0
make docker-push  IMG=quay.io/<your-org>/acc-operator:0.1.0
make bundle-build BUNDLE_IMG=$BUNDLE_IMG
make bundle-push  BUNDLE_IMG=$BUNDLE_IMG

# Re-run preflight locally to confirm the fix
preflight check operator $BUNDLE_IMG \
  --pyxis-api-token $PFLT_PYXIS_API_TOKEN \
  --kubeconfig ~/.kube/config \
  --namespace acc-operator-system \
  --serviceaccount acc-operator-controller-manager
```

In the Connect portal:
1. Navigate to your project → **Bundle image** → update SHA digest if changed
2. Click **Re-run pipeline** (no new project needed)
3. Upload updated preflight results

---

## 6. Step 4 — Review & Publication

> ℹ️ **Verify against current Red Hat Connect UI** — SLAs and reviewer queue behavior are
> subject to Red Hat partner program policies, which may be updated.

### 6.1 Red Hat Reviewer SLA

| Submission type | Expected review time |
|----------------|----------------------|
| First submission | 5–10 business days |
| Re-review (minor fix) | 3–5 business days |
| Re-review (major fix / new version) | 5–10 business days |

The clock starts after the Konflux pipeline passes all stages. Failures in the pipeline do not
consume reviewer time.

### 6.2 Types of Reviewer Feedback

| Category | Description | Action required |
|----------|-------------|-----------------|
| **Blocking** | Must be fixed before approval; pipeline re-run required | Fix, rebuild, re-upload preflight, re-run pipeline |
| **Advisory** | Recommended improvements; do not block approval | Address in next version |
| **Informational** | Notes about best practices; no action needed | No action |

Common blocking issues for first-time submissions:
- CSV `spec.icon` missing or malformed (must be base64-encoded SVG, ≤ 1 MB)
- `spec.maintainers` empty in CSV
- `spec.description` shorter than 100 characters
- Missing `alm-examples` annotation on CSV (sample CR JSON)

### 6.3 Triggering a Re-review

After addressing feedback:
1. Fix the issue in the bundle
2. Rebuild and push updated images
3. Update the image SHA in the Connect portal
4. Click **Submit for review** again (the project state resets to _Under Review_)

### 6.4 After Approval

Once approved:
- The operator appears in the **OperatorHub** tab of the OpenShift web console within 24 hours
- The listing is live at `operatorhub.io` (community) or the OCP embedded catalog (certified)
- To publish a new version: repeat the process with an updated `BUNDLE_IMG` tagged `0.2.0` (or
  next version); the previous version remains available in the catalog

---

## 7. Timeline & Common Failures

| Failure | Preflight check | Likely cause | Fix |
|---------|----------------|--------------|-----|
| Missing image labels | `HasRequiredLabel` | Labels absent from `operator/Containerfile` | Add `vendor`, `name`, `version`, `summary`, `description` LABEL directives (already present in v0.1.0 Containerfile) |
| Root-running container | `RunAsNonRoot` | Missing `USER` directive | Already fixed: `USER 65532` in `operator/Containerfile` |
| Privileged container | `HasNoProhibitedContainerSpec` | `securityContext.privileged: true` in Deployment | Remove — never required by the ACC operator |
| Non-UBI base image | `BasedOnUbi` | Custom or non-Red Hat base used | Use `registry.access.redhat.com/ubi10/ubi-minimal` |
| CSV missing icon | `ValidateOperatorBundle` (advisory) | `spec.icon` empty in CSV | Add a base64-encoded SVG; use `make bundle` after adding to config |
| OwnNamespace not in installModes | `BundleIndexImageAnnotations` | CSV `installModes` incorrect | Already set correctly: OwnNamespace ✅ SingleNamespace ✅ MultiNamespace ❌ AllNamespaces ❌ |
| Scorecard timeout | `ScorecardOLMSuite` | Cluster unreachable or operator pod not starting | Check `--wait-time`; verify cluster access; check operator pod logs |
| `spec.minKubeVersion` absent | `HasMinKubeVersion` | CSV not specifying minimum | Already set to `1.27.0` in CSV |
| `alm-examples` missing | `ValidateOperatorBundle` | Sample CRs not embedded in CSV annotations | Run `make bundle` — operator-sdk embeds samples automatically |
| Bundle image not public | Preflight connectivity | quay.io repo set to private | Set repository visibility to **Public** before running preflight |
| CSV `spec.maintainers` empty | Red Hat reviewer (blocking) | Not a preflight check; manual review | Add `- name: <team> email: <address>` under `spec.maintainers` in CSV |
| CSV description < 100 chars | Red Hat reviewer (blocking) | Short `spec.description` | Expand description in `bundle/manifests/acc-operator.clusterserviceversion.yaml` |

### 7.1 Typical Timeline (First Submission)

```
Day 0   — Preflight passes locally; bundle pushed to quay.io
Day 0   — community-operators PR opened (tech preview path)
Day 1   — community-operators CI passes; operator live on operatorhub.io
Day 0   — Red Hat Connect submission created; Konflux pipeline triggered
Day 1–2 — Konflux pipeline completes (OCP matrix tests)
Day 2–3 — Red Hat reviewer queue entry
Day 7–12 — First reviewer feedback (or approval)
Day 13–17 — Re-submission if fixes needed
Day 18–22 — Approved; operator live in OCP embedded OperatorHub
```

---

## 8. Tech Preview Path via community-operators

The `community-operators` repository at `github.com/k8s-operatorhub/community-operators` is
the fastest route to public availability. No Red Hat partner account is required.

### 8.1 Fork and Add the Bundle

```bash
# 1. Fork k8s-operatorhub/community-operators on GitHub

# 2. Clone your fork
git clone https://github.com/<your-github-org>/community-operators.git
cd community-operators

# 3. Create the operator directory (versioned)
mkdir -p operators/acc-operator/0.1.0

# 4. Copy the bundle manifests from the operator repo
cp -r /path/to/agentic-cell-corpus/operator/bundle/manifests \
       operators/acc-operator/0.1.0/
cp -r /path/to/agentic-cell-corpus/operator/bundle/metadata \
       operators/acc-operator/0.1.0/

# 5. Create or update the top-level ci.yaml
cat > operators/acc-operator/ci.yaml <<EOF
---
addReviewers:
  - <your-github-handle>
reviewers:
  - <your-github-handle>
EOF

# 6. Commit and open a PR
git checkout -b add-acc-operator-0.1.0
git add operators/acc-operator/
git commit -m "operator acc-operator (0.1.0)"
git push origin add-acc-operator-0.1.0
# Open a PR from your fork to k8s-operatorhub/community-operators main
```

### 8.2 Automated CI Checks

The community-operators repository runs the following checks automatically on each PR:

| Check | Tool | Notes |
|-------|------|-------|
| Bundle format | `operator-courier` | Validates CSV structure |
| Preflight | `preflight check operator` | Image must be publicly accessible |
| Scorecard | `operator-sdk scorecard` | Basic + OLM suites |
| Deprecation warnings | Internal linter | Advisory only |
| OCP compatibility | Version range check against `spec.minKubeVersion` | Must be ≤ 1.27 for broad compatibility |

### 8.3 What Happens After Merge

- The operator is indexed within ~2 hours by the OperatorHub indexer
- It appears at `https://operatorhub.io/operator/acc-operator`
- Users can install it via `kubectl apply -f https://operatorhub.io/install/acc-operator.yaml`
  (which creates an `OperatorGroup` + `Subscription` in the `operators` namespace)
- Updates to subsequent versions (0.2.0, etc.) require a new versioned directory and a new PR

### 8.4 Updating to a New Version

```bash
mkdir -p operators/acc-operator/0.2.0
# copy updated bundle...
# The ci.yaml and top-level directory are shared across versions (no duplication)
```

The CSV must include a `spec.replaces: acc-operator.v0.1.0` field to enable OLM upgrade
chaining from the previous version.

---

## Appendix A — Preflight Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `PFLT_PYXIS_API_TOKEN` | Pyxis API token for results submission | `export PFLT_PYXIS_API_TOKEN=...` |
| `PFLT_LOGLEVEL` | Log verbosity | `trace` / `debug` / `info` (default) |
| `PFLT_ARTIFACTS` | Directory for preflight result artifacts | `./preflight-artifacts` |
| `PFLT_NAMESPACE` | Default namespace for operator bundle check | `acc-operator-system` |
| `PFLT_SERVICEACCOUNT` | Service account for operator bundle check | `acc-operator-controller-manager` |

```bash
# Recommended: set in a local .env file (do not commit)
export PFLT_PYXIS_API_TOKEN=<token>
export PFLT_ARTIFACTS=./preflight-artifacts
export PFLT_NAMESPACE=acc-operator-system
export PFLT_SERVICEACCOUNT=acc-operator-controller-manager

# Then run checks without repeating flags
preflight check container quay.io/<org>/acc-operator:0.1.0
preflight check operator  quay.io/<org>/acc-operator-bundle:0.1.0 \
  --kubeconfig ~/.kube/config
```

---

## Appendix B — Required CSV Fields Checklist

Before submitting to either catalog, verify the CSV at
`operator/bundle/manifests/acc-operator.clusterserviceversion.yaml` contains:

- [ ] `metadata.name`: `acc-operator.v0.1.0`
- [ ] `spec.version`: `0.1.0`
- [ ] `spec.replaces`: set for v0.2.0+ (empty for initial submission)
- [ ] `spec.minKubeVersion`: `1.27.0`
- [ ] `spec.displayName`: non-empty human-readable name
- [ ] `spec.description`: ≥ 100 characters
- [ ] `spec.icon`: base64-encoded SVG with `mediatype: image/svg+xml`
- [ ] `spec.maintainers`: at least one entry with `name` and `email`
- [ ] `spec.provider.name`: your organization name
- [ ] `spec.links`: at least one link (documentation URL)
- [ ] `spec.installModes`: OwnNamespace and SingleNamespace set to `true`
- [ ] `metadata.annotations.alm-examples`: JSON array with at least one sample CR
- [ ] `metadata.annotations.capabilities`: `Seamless Upgrades` (Level 3)
- [ ] `metadata.annotations.categories`: e.g., `AI/Machine Learning`
- [ ] `metadata.annotations.containerImage`: matches `$IMG` (operator image, not bundle)
- [ ] `spec.install.spec.deployments[0].spec.template.spec.containers[0].image`: matches `$IMG`
