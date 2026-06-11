# WS-A — Ship `AccCatalog` + `AccPackageInstall` into the OLM bundle

**Proposal 020, workstream A (the OperatorHub blocker).** The two CRD
Go types (`api/v1alpha1/acccatalog_types.go`,
`accpackageinstall_types.go`) and their controllers
(`internal/controller/`) already exist and are registered with the
scheme. What's missing is purely **packaging**: the generated CRD
bases, the CRD kustomization entries, and the CSV `owned` +
`alm-examples` stanzas — so the two APIs install via OperatorHub.

> **Why this is a runbook, not a finished PR.** The CRD *bases* are
> `controller-gen` output and the CSV `owned`/`alm-examples` are
> `operator-sdk generate bundle` output. Both need the Go toolchain
> (`controller-gen`, `operator-sdk`, `kustomize`) which isn't on the
> authoring box. This branch ships the parts that **don't** need
> codegen and are independently verifiable — the two `config/samples`
> CRs (already wired into `config/samples/kustomization.yaml`) — plus
> the exact commands and paste-able snippets for the codegen steps.

Run everything below from `operator/` on a Linux box (or CI) with
`make`, Go ≥1.22, `controller-gen`, `kustomize`, and `operator-sdk`
≥1.34 on `PATH`.

---

## Step 1 — generate the CRD bases

```bash
make manifests
```

`controller-gen` reads the kubebuilder markers on the two types and
emits:

```
config/crd/bases/acc.redhat.io_acccatalogs.yaml
config/crd/bases/acc.redhat.io_accpackageinstalls.yaml
```

Spot-check the generated schema against the Go markers (these are the
constraints the Python loaders in `acc/pkg/catalog.py` also enforce, so
drift here = silent install-time rejection):

| Field | Constraint (must appear in the CRD) |
|---|---|
| `acccatalogs` `.spec.tier` | enum `trusted;tp;community;self` |
| `acccatalogs` `.spec.mode` | enum `https;file` |
| `acccatalogs` `.spec.priority` | int, default 100, min 1, max 1000 |
| `acccatalogs` `.spec.requiredSigner.{issuer,subjectPattern}` | required, minLength 1 |
| `accpackageinstalls` `.spec.name` | pattern `^@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9_-]*$` |
| `accpackageinstalls` `.spec.constraint` | required, minLength 1 |
| `accpackageinstalls` `.spec.allowUnsigned` | bool, default false |
| `accpackageinstalls` `.status.phase` | enum `Pending;Installing;Installed;Failed` |

## Step 2 — add the bases to the CRD kustomization

`config/crd/kustomization.yaml` currently lists only the two existing
bases. Add the two new ones:

```yaml
resources:
- bases/acc.redhat.io_agentcorpora.yaml
- bases/acc.redhat.io_agentcollectives.yaml
- bases/acc.redhat.io_acccatalogs.yaml          # ← add
- bases/acc.redhat.io_accpackageinstalls.yaml   # ← add
```

(If the file uses a different relative prefix, match it — the point is
the two new base filenames join the list.)

## Step 3 — regenerate the bundle (CSV `owned` + `alm-examples`)

```bash
make bundle
```

`operator-sdk generate bundle` derives the `owned` API list and the
`alm-examples` annotation from `config/manifests/`. If `make bundle`
does **not** populate the two new entries automatically (the curated
CSV at `bundle/manifests/acc-operator.clusterserviceversion.yaml` is
hand-maintained today), paste these by hand.

### 3a — `spec.customresourcedefinitions.owned` (append after AgentCollective)

```yaml
    - description: >-
        AccCatalog declares one entry in the layered package catalog list
        rendered to /etc/acc/catalogs.yaml inside ACC pods. The operator
        merges all AccCatalog resources in the namespace into the
        acc-catalogs ConfigMap.
      displayName: ACC Catalog
      kind: AccCatalog
      name: acccatalogs.acc.redhat.io
      version: v1alpha1
    - description: >-
        AccPackageInstall declares a @scope/name@constraint role package to
        install into the namespace's ACC pods. The operator reconciles it
        onto live pods via acc-cli collective pkg-install, enforcing the
        cosign signing floor.
      displayName: ACC Package Install
      kind: AccPackageInstall
      name: accpackageinstalls.acc.redhat.io
      version: v1alpha1
```

### 3b — `metadata.annotations.alm-examples`

This annotation is a JSON **array** of example CRs. Add the two objects
below to the existing array (they are the JSON form of the new
`config/samples/*.yaml` shipped on this branch):

```json
{
  "apiVersion": "acc.redhat.io/v1alpha1",
  "kind": "AccCatalog",
  "metadata": { "name": "acc-community-hub", "namespace": "acc-system" },
  "spec": {
    "catalogId": "acc-community",
    "tier": "community",
    "mode": "https",
    "url": "https://acc-roles.dev/index.json",
    "requiredSigner": {
      "issuer": "https://token.actions.githubusercontent.com",
      "subjectPattern": "^https://github\\.com/flg77/acc-ecosystem/.+$"
    },
    "priority": 100
  }
}
```

```json
{
  "apiVersion": "acc.redhat.io/v1alpha1",
  "kind": "AccPackageInstall",
  "metadata": { "name": "install-coding-roles", "namespace": "acc-system" },
  "spec": {
    "name": "@acc/coding-roles",
    "constraint": "^1.0",
    "catalogRef": "acc-community-hub",
    "targetCorpus": "sol-corpus",
    "allowUnsigned": false
  }
}
```

## Step 4 — RBAC

The Stage-1.6b controllers already carry `+kubebuilder:rbac` markers, so
`make manifests` refreshes `config/rbac/role.yaml`. Confirm the
generated ClusterRole grants `acccatalogs` + `accpackageinstalls`
(+ their `/status` subresources) `get;list;watch;create;update;patch`.
If the CSV's `spec.install.spec.clusterPermissions` is curated
separately, mirror the same rules there.

## Step 5 — validate + ship

```bash
operator-sdk bundle validate ./bundle          # must pass
make bundle-build bundle-push                   # build + push the bundle image
make catalog-build catalog-push FROM_INDEX=...  # FBC: add to the existing index
```

Scorecard (optional but recommended):

```bash
operator-sdk scorecard ./bundle
```

---

## Verification gates (proposal 020 §Verification, WS-A)

1. `make manifests && make install` on kind/CRC creates both CRDs:
   `kubectl get crd acccatalogs.acc.redhat.io accpackageinstalls.acc.redhat.io`
2. Apply both samples — they admit cleanly:
   `kubectl apply -f config/samples/acc_v1alpha1_acccatalog.yaml -f config/samples/acc_v1alpha1_accpackageinstall.yaml`
3. The controllers reconcile: the AccPackageInstall reaches
   `.status.phase: Installed` against a live AgentCorpus pod, and the
   AccCatalog's render stamps `.status.lastRenderedAt`.
4. `operator-sdk bundle validate ./bundle` is green.
5. An OLM `Subscription` to the rebuilt index shows **four** provided
   APIs (AgentCorpus, AgentCollective, AccCatalog, AccPackageInstall).

Once 1–5 pass, WS-A is done and WS-B (the console plugin, which watches
these CRDs) is unblocked. The console plugin's `src/models.ts` must
match the generated `config/crd/bases` — add a CI assertion comparing
the two to catch drift (silent empty lists otherwise).
