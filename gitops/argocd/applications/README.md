# ArgoCD Application templates for ACC

Stage 1.6 ships two Custom Resource Definitions that wire the
`acc-pkg` substrate to GitOps:

* **`AccCatalog`** — declares one entry in the layered catalog
  list.  Operator reconciler renders all `AccCatalog` resources in
  a namespace into a single `acc-catalogs` ConfigMap mounted at
  `/etc/acc/catalogs.yaml` inside ACC pods.
* **`AccPackageInstall`** — declares one `@scope/name@constraint`
  install.  Operator reconciler reconciles by exec-ing
  `acc-cli collective pkg-install` against the target
  AgentCorpus's ACC pod and records `phase` + `installedVersion`
  on the CR's status.

## Sample

[`accpackage-sample.yaml`](./accpackage-sample.yaml) deploys two
`AccCatalog` entries (canonical + corp-internal) and two
`AccPackageInstall` objects (coding-roles + research-roles) into
the `acc` namespace, wrapped in an ArgoCD `Application`.

## What ships in Stage 1.6 vs what's deferred

| Sub-task | Status |
|---|---|
| API types (`acc.redhat.io/v1alpha1/AccPackageInstall`, `AccCatalog`) | Shipped |
| DeepCopy + scheme registration | Shipped (hand-written; replace on next `make generate`) |
| Sample ArgoCD `Application` + CR manifests | Shipped (this folder) |
| Reconcilers under `operator/internal/controller/` | **Deferred** — Stage 1.6b. The exec-into-pod logic + leader election + status patching is multi-day Go work; ships separately so the API surface lands now for downstream tools to import |
| RBAC + OLM bundle update | **Deferred** — lands with the reconciler |
| envtest integration tests | **Deferred** — lands with the reconciler |

The deferred reconciler PR consumes the same `fetch_and_install`
Python entry point that 1.5.3 (`acc-cli collective pkg-install`)
and 1.4 (`PROPOSE_INFUSE` handler) already use — single seam, no
parallel logic.

## CRD generation

Once the operator's `make generate` runs against the new types,
the hand-written DeepCopy at
`operator/api/v1alpha1/zz_generated_stage1_6_deepcopy.go` will be
superseded by the controller-gen output.  Delete it then.
