# Design: ACC Operator — Cluster Deployment Guide & Certification Roadmap

## Approach

Both documents are **reference-class technical guides** — accurate, step-by-step, and tied to the
exact artifacts that already exist in `operator/`. They are not introductory tutorials; the reader
is assumed to have `kubectl`/`oc` access and basic familiarity with operators.

The install guide covers **three deployment methods** in order of ascending complexity and
decreasing speed, matching the audience at each stage (developer → integration tester → OLM user).
The certification guide is a linear checklist mirroring the Red Hat partner certification pipeline.

---

## Files to Create

| File | Size estimate | Generated from |
|------|--------------|----------------|
| `docs/operator-install-local.md` | ~400 lines | Makefile, config/, bundle/, API types |
| `docs/operator-certification.md` | ~250 lines | Red Hat Connect process docs |

## Files Referenced (read-only — no changes needed)

| File | Used for |
|------|----------|
| `operator/Makefile` | Exact `make` targets to document |
| `operator/config/rbac/role.yaml` | RBAC prerequisites list |
| `operator/config/samples/acc_v1alpha1_agentcorpus_standalone.yaml` | Sample CR for verification step |
| `operator/bundle/manifests/acc-operator.clusterserviceversion.yaml` | OLM install modes, install strategy |
| `operator/bundle/metadata/annotations.yaml` | Bundle channel / package name |
| `operator/api/v1alpha1/agentcorpus_types.go` | Capabilities description |
| `operator/api/v1alpha1/agentcorpus_webhook.go` | Webhook prerequisites |

---

## Document 1 Structure: `operator-install-local.md`

```
## 1. Capabilities Summary          (what the operator does — ≤ 250 words)
## 2. Prerequisites                 (tools, cluster, registry access)
## 3. Lab Cluster Option Matrix     (CRC, Kind+OLM, remote OCP)
## 4. Build & Push the Operator Image
## 5. Method A — Kustomize Deploy   (make deploy, fastest)
## 6. Method B — OLM Bundle Deploy  (operator-sdk run bundle)
## 7. Method C — CatalogSource      (mirrors production OLM flow)
## 8. Create the Category-A WASM ConfigMap  (prerequisite CR)
## 9. Deploy a Sample AgentCorpus
## 10. Verify Installation
## 11. Uninstall
```

### Key Design Decisions

**Method A (Kustomize)** targets developers iterating on the operator itself. Uses `make deploy IMG=...` which calls `kustomize build config/default | kubectl apply -f -`. This is the fastest path but bypasses OLM lifecycle management.

**Method B (OLM bundle)** uses `operator-sdk run bundle` which:
1. Creates a temporary `CatalogSource` pointing at the bundle image
2. Creates an `OperatorGroup` scoping to the target namespace
3. Creates a `Subscription` that OLM resolves into an `InstallPlan`

This exactly mirrors OperatorHub install and is the **required path before certification** because it exercises the webhook cert injection and OLM upgrade machinery.

**Method C (CatalogSource)** covers deploying from an internal index image (built with `opm`) — relevant for enterprise lab clusters that mirror OperatorHub but don't have internet access.

### Lab Cluster Option Matrix

| Option | Kubernetes | OLM | Webhook support | Standalone compatible | Recommended for |
|--------|-----------|-----|-----------------|----------------------|-----------------|
| CRC (OpenShift Local) ≥ 2.38 | OCP 4.14 | ✅ built-in | ✅ cert-manager | ❌ (needs VM) | Primary dev lab |
| Kind + OLM | k8s 1.27 | ✅ manual install | ⚠️ manual cert | ❌ | Pure k8s testing |
| Remote OCP 4.14+ node | OCP 4.14 | ✅ built-in | ✅ built-in | ❌ (Podman-only) | Integration & cert testing |
| Local Podman (standalone) | ❌ | ❌ | ❌ | ✅ | **Not suitable** — use for agent smoke tests only |

---

## Document 2 Structure: `operator-certification.md`

```
## 1. Overview & Catalog Targets    (community-operators vs certified-operators)
## 2. Prerequisites                 (RH partner account, quay.io org, preflight)
## 3. Step 1 — Preflight Checks     (local run against bundle image)
## 4. Step 2 — Red Hat Connect      (create product listing, submit bundle)
## 5. Step 3 — Certification Pipeline  (Konflux/HACBS CI, automated checks)
## 6. Step 4 — Review & Publication (Red Hat reviewer, OperatorHub listing)
## 7. Timeline & Common Failures
## 8. Tech Preview Path             (community-operators, faster but uncertified)
```

### Certification Pipeline Detail

```
Developer                Red Hat Connect              Automated Pipeline
    │                          │                              │
    ├─ preflight check ────────►                              │
    │    └─ bundle image        │                              │
    │    └─ operator image      │                              │
    │                          │                              │
    ├─ submit bundle ──────────►                              │
    │    └─ CSV                 ├─ trigger Konflux CI ────────►│
    │    └─ CRD manifests       │                              ├─ scorecard tests
    │    └─ metadata            │                              ├─ preflight pipeline
    │                          │                              ├─ OCP matrix tests
    │                          │◄─ test results ──────────────┤
    │                          │                              │
    │◄─ reviewer feedback ─────┤                              │
    │    (5–10 business days)   │                              │
    │                          │                              │
    ├─ fix & resubmit ─────────►                              │
    │                          │                              │
    │◄─ publication approved ──┤                              │
    └─ appears on OperatorHub  │                              │
```

---

## Error Handling / Common Failure Modes

| Failure | Cause | Fix |
|---------|-------|-----|
| `CertificateSigningRequest` timeout | Webhook cert not injected | Install cert-manager or use OLM cert injection |
| `ImagePullBackOff` on manager pod | Image not pushed / wrong tag | Verify `IMG` matches registry path |
| `AgentCorpus` stuck in `Progressing` | NATS StatefulSet PVC pending | Ensure default StorageClass provides RWO |
| Preflight `FAILED: check-image-label` | Missing required container labels | Add `vendor`, `name`, `version` labels to Containerfile |
| OLM install fails: `no matching OperatorGroup` | Operator installed into wrong namespace | Create OperatorGroup before Subscription |
| Scorecard timeout | envtest not configured | Ensure `scorecard` has kubeconfig pointing to live cluster |

---

## Testing Strategy

Both documents are validated by **manual walkthrough** — not automated tests:

1. **Install guide** — a second developer follows `operator-install-local.md` from scratch on CRC and reports any step that fails or is ambiguous.
2. **Certification guide** — validated against the [Red Hat Operator Certification](https://access.redhat.com/documentation/en-us/red_hat_software_certification) docs at time of writing; marked with `> ℹ️ Verify against current Red Hat Connect UI` where the portal UI changes frequently.

No CI job is added for doc validation in this change (planned for ACC-6).
