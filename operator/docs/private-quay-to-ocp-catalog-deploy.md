# Deploying the ACC Operator via a private Quay → OCP internal registry → private catalog

This runbook documents the **air-gapped-style** deployment path validated live
on 2026-06-08: build the operator on **acc1**, push to the private
**`quay.ic3net.internal`** registry, mirror into the **OpenShift internal
registry**, and install from a **private CatalogSource** on a cluster that
*cannot reach the internal network* (e.g. an RHPDS sandbox).

It covers both a **manual** walk-through and an **automated** script
(`operator/hack/deploy-private-catalog.sh` + the OCP manifests in
`operator/config/private-catalog/`).

---

## Topology (why acc1 is the bridge)

```
  ┌─────────── ic3 internal net ───────────┐        ┌──── internet ────┐
  │  acc1  ──podman push──►  quay.ic3net    │        │   RHPDS OCP SNO  │
  │  (build host)            .internal:8443 │        │  api/apps public │
  └──────────┬──────────────────────────────┘        └────────┬─────────┘
             │  acc1 ALSO reaches the OCP apps route (internet)         │
             └────────────── oc image mirror ──────────────────────────┘
                          quay → OCP internal registry (via its route)
```

The sandbox cluster **cannot resolve/reach** `quay.ic3net.internal` (it lives on
the ic3 net). **acc1 is the only host that can talk to both sides**, so acc1
pushes to Quay *and* mirrors the images into the cluster's own integrated
registry through the registry's public route. Nothing in-cluster ever pulls
from Quay.

## Prerequisites (validated state)

| Host | Needs | Notes |
|------|-------|-------|
| **acc1** | `podman`, `opm`, `operator-sdk`, `oc`, `make`, `git` | `go` **not** required — the operator image is a multi-stage `ubi10/go-toolset` build. CRD/bundle codegen runs in a `go-toolset` container. |
| **acc1 → Quay** | login to `quay.ic3net.internal:8443` | The registry endpoint is **:8443**. `:443` is an nginx **redirect** only (a push there returns `405`). Robot account `flg+accoperator` (admin on `flg/acc-operator`). |
| **OCP** | cluster-admin; ability to expose the registry route | Single-Node OpenShift here → an `ImageTagMirrorSet` reboots the whole node, so we avoid it (see Final mile). |

### Gotchas discovered (read before you start)

1. **Registry port** — use `quay.ic3net.internal:8443`, not the bare host.
2. **podman auth is keyed by `host:port`** — a login to `quay.ic3net.internal`
   is **not** accepted for `quay.ic3net.internal:8443`. Log in with the port.
3. **`origin/main` operator did not compile / bundle** (all fixed in the PR that
   accompanies this doc, but pin a commit that has them):
   - `go.sum` missing `gorilla/websocket`, `moby/spdystream`, `mxk/go-flowrate`
     (client-go exec path) → `go mod tidy`.
   - `zz_generated_stage1_6_deepcopy.go` used `&t` on `metav1.Time.DeepCopy()`
     (which already returns `*Time`) → assign directly.
   - committed `bundle/manifests/` shipped **only the CSV, no CRDs** → `opm`
     rejects the bundle (`couldn't find …AgentCorpus …found: map[]`).
   - CSV `containerImage`/deployment image was a non-existent placeholder.
4. **`oc image mirror` over the HAProxy route** intermittently fails a blob
   commit with `HTTP 400: unexpected end of JSON input`. Re-run with
   `--max-per-registry=1`; it resumes and succeeds.
5. **Disconnected bundle ref** — the catalog index and the CSV embed whatever
   image ref you build with. If that's the Quay ref, in-cluster unpack fails
   (`lookup quay.ic3net.internal … no such host`). The refs the **cluster** uses
   must point at the **internal registry** (see Final mile).

---

## Variables used throughout

```bash
# acc1
REPO=/home/flg/git/acc                 # checkout (origin = github.com/flg77/acc)
Q=quay.ic3net.internal:8443/flg/acc-operator
VER=0.1.0

# OCP
OCP_API=https://api.ocp.b74q6.sandbox3207.opentlc.com:6443
NS=acc-system                          # install namespace
ROUTE=default-route-openshift-image-registry.apps.ocp.b74q6.sandbox3207.opentlc.com
SVC=image-registry.openshift-image-registry.svc:5000/$NS/acc-operator   # in-cluster ref
```

---

## Manual walk-through

### 1 — Build on acc1 (no host `go`)

```bash
# Clean build tree off origin/main (leaves your working branch untouched)
git -C "$REPO" fetch origin
git -C "$REPO" worktree add --detach /home/flg/acc-opbuild origin/main
cd /home/flg/acc-opbuild/operator

# (If building a commit without the go.sum/deepcopy fixes) regenerate go.sum in
# a container — rootless podman: run as --user 0 so it maps to your host uid and
# can write the mounted files:
podman run --rm --user 0 -e HOME=/tmp -e GOCACHE=/tmp/gocache -e GOPATH=/tmp/gopath \
  -e GOFLAGS=-mod=mod -v "$PWD":/w:Z -w /w \
  registry.access.redhat.com/ubi10/go-toolset:10.0 bash -lc 'go mod tidy'

# Build the operator image (multi-stage; podman, no host go)
make docker-build IMG=$Q:$VER CONTAINER_TOOL=podman
```

### 2 — Push to private Quay (`:8443`)

```bash
podman login quay.ic3net.internal:8443 -u 'flg+accoperator' --password-stdin <<<"$ROBOT_TOKEN"

# Point the bundle CSV's manager image at the ref the CLUSTER will pull.
# We use the in-cluster service ref so no ImageTagMirrorSet (node reboot) is needed:
CSV=bundle/manifests/acc-operator.clusterserviceversion.yaml
sed -i "s#quay.io/redhat-ai-dev/acc-operator:$VER#$SVC:$VER#g" "$CSV"

# Ensure the bundle ships its CRDs (commit fixes this; belt-and-braces):
cp config/crd/bases/acc.redhat.io_*.yaml bundle/manifests/

podman push $Q:$VER
make bundle-build  BUNDLE_IMG=$Q:$VER-bundle CONTAINER_TOOL=podman
podman push $Q:$VER-bundle
```

### 3 — Build the catalog index referencing the **in-cluster** bundle ref

`opm index add` embeds *and pulls* whatever `--bundles` ref you pass. acc1 can't
pull the `svc:5000` ref, so render a **file-based catalog** from a ref acc1 *can*
pull (the Quay ref), then rewrite the bundle image to the `svc:5000` ref before
building the index image — no node-rebooting mirror set required:

```bash
mkdir -p catalog
opm render $Q:$VER-bundle --output=yaml > /tmp/acc-bundle.yaml
cat > catalog/index.yaml <<YAML
---
schema: olm.package
name: acc-operator
defaultChannel: alpha
---
schema: olm.channel
package: acc-operator
name: alpha
entries:
  - name: acc-operator.v$VER
YAML
cat /tmp/acc-bundle.yaml >> catalog/index.yaml
# rewrite the bundle image the cluster will unpack → in-cluster registry ref
sed -i "s#$Q:$VER-bundle#$SVC:$VER-bundle#g" catalog/index.yaml
opm validate catalog
opm generate dockerfile catalog
podman build -f catalog.Dockerfile -t $Q:$VER-index .
podman push $Q:$VER-index
```

> Simpler alternative (if you accept a node reboot): build the index with the
> deprecated `opm index add --bundles $Q:$VER-bundle` (Quay ref) and add an
> `ImageTagMirrorSet` redirecting `quay.ic3net.internal:8443/flg/acc-operator` →
> `$SVC`. On **multi-node** clusters this is fine; on **SNO it reboots the
> whole cluster**.

### 4 — Mirror the three images Quay → OCP internal registry

```bash
oc login --token=$OCP_TOKEN --server=$OCP_API --insecure-skip-tls-verify=true
# one-time: expose the integrated registry
oc patch configs.imageregistry.operator.openshift.io/cluster --type=merge \
  -p '{"spec":{"defaultRoute":true}}'
oc new-project $NS 2>/dev/null || true

AUTHFILE=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json   # already has the Quay robot
oc registry login --registry=$ROUTE --insecure=true --to=$AUTHFILE --skip-check

# serialize to dodge the HAProxy 400 flake; re-run on failure (it resumes)
oc image mirror -a "$AUTHFILE" --insecure=true --max-per-registry=1 \
  $Q:$VER=$ROUTE/$NS/acc-operator:$VER \
  $Q:$VER-bundle=$ROUTE/$NS/acc-operator:$VER-bundle \
  $Q:$VER-index=$ROUTE/$NS/acc-operator:$VER-index
```

### 5 — Wire up OLM (private catalog + install)

```bash
# Let marketplace/unpack pods (other namespaces) pull our images from $NS:
oc policy add-role-to-group system:image-puller system:serviceaccounts -n $NS

oc apply -f operator/config/private-catalog/   # CatalogSource + OperatorGroup + Subscription
```

`operator/config/private-catalog/` contains:

* **CatalogSource** (`openshift-marketplace`) → `spec.image: <SVC>:0.1.0-index`
* **OperatorGroup** (`acc-system`)
* **Subscription** (`acc-system`, channel `alpha`, source `acc-catalog`)

### 6 — Verify

```bash
oc get catalogsource acc-catalog -n openshift-marketplace \
  -o jsonpath='{.status.connectionState.lastObservedState}'      # READY
oc get packagemanifest -n openshift-marketplace -l catalog=acc-catalog
oc get sub,installplan,csv -n acc-system
oc get pods -n acc-system                                        # operator Running
oc get crd | grep acc.redhat.io                                  # 4 CRDs Established
```

### 7 — Teardown

```bash
oc delete -f operator/config/private-catalog/ --ignore-not-found
oc delete project $NS
oc patch configs.imageregistry.operator.openshift.io/cluster --type=merge \
  -p '{"spec":{"defaultRoute":false}}'
git -C "$REPO" worktree remove --force /home/flg/acc-opbuild
```

---

## Automated path

`operator/hack/deploy-private-catalog.sh` parametrizes steps 1–5. It runs the
acc1-side build/push/mirror, then applies the OCP manifests. Every value is an
env var with the defaults above; secrets come **only** from the environment:

```bash
export ROBOT_TOKEN=…          # quay.ic3net.internal robot token (flg+accoperator)
export OCP_TOKEN=sha256~…     # cluster-admin token
./operator/hack/deploy-private-catalog.sh           # full pipeline
./operator/hack/deploy-private-catalog.sh build     # just build+push to Quay
./operator/hack/deploy-private-catalog.sh mirror    # just Quay→OCP mirror
./operator/hack/deploy-private-catalog.sh install   # just OLM wire-up
./operator/hack/deploy-private-catalog.sh teardown
```

The script is idempotent (re-running resumes mirrors, re-applies manifests) and
prints each step with a timestamp so it doubles as the live runbook log.

## Live-run status (2026-06-08)

Proven end-to-end up to **operator visible in a READY private CatalogSource**:
build → Quay (`:8443`) → OCP internal registry → CatalogSource READY → package
`acc-operator` served. The running-operator final mile was deferred pending the
build-defect fixes in the companion PR; re-run §1–§6 against a commit that
includes them to land a Running operator pod.
