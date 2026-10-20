---
type: backlog-item
area: operator-release
id: OR-03
status: done
size: M
depends: [OR-02]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-03 — Build standalone

**What.** The operator builds from its own clone. `ACC_RUNTIME_VERSION` (one file) pins the runtime; `make sync-manifests` downloads the public `flg77/acc` tag tarball and copies `roles/ skills/ mcps/` + `acc/nats_permissions.yaml` into the module (`ACC_SRC=<dir>` overrides offline). The drift test compares against the fetched copy. `KUSTOMIZE` tool target and `config/manifests/` restored so `make bundle` regenerates the CSV and `git diff --exit-code bundle/` is clean. Go 1.25 pinned with a `toolchain` directive; the Containerfile builder matches.

**Why.** Today the module embeds the spearhead root and the CSV is hand-kept; both are why no one else can build or regenerate it.

**Done when.** `make build && make test-unit` pass in a clone with no sibling directory; `make bundle` is a no-op on a clean tree.

**2026-10-04 — done** (acc-spearhead-operator, branch `or-03-standalone-build`, 4 commits). Pin `ACC_RUNTIME_VERSION = 0.27.0`. `make sync-manifests` shallow-clones the public tag (tarball download is proxy-hostile, git is not). `make bundle` regenerates and validates `bundle/`, idempotent; `make bundle-check` for CI. All Go tests and the parity tests pass from a clone with no sibling.

Found on the way, fixed or routed:
- **gofmt corrupts a CEL rule**: Go ≥1.19 doc-comment reformatting turns `''` into `”`; the `modelStoragePVC != ''` rule became invalid CEL in the generated CRD. Rewritten as `size(…) > 0`. Any marker in a doc comment must avoid `''`.
- **AgentCollective webhook declared, never registered** (main.go registers only AgentCorpus): kustomize installs failed every collective create with failurePolicy=Fail; the bundle never had it. Markers neutralised until it is registered.
- **CRD bases stale** for AccCatalog/AccPackageInstall (requiredSigner defaults, constraint optional) — regenerated from the Go types.
- **CSV clusterPermissions lacked** NetworkPolicy / EgressFirewall / CiliumNetworkPolicy / SPIRE grants that `role.yaml` had — now derived from `role.yaml`.
- **ConsolePlugin CR is not a valid registry+v1 bundle object** (`operator-sdk bundle validate` refuses it); dropped from the bundle, applied by hand until [[OR-05 — One bundle for Kubernetes and OpenShift]].
- `config/rbac/role.yaml` is hand-maintained; the rbac markers cover a third → `make manifests` no longer generates RBAC (maturity item: move to markers).
