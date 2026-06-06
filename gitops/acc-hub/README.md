# acc-hub — internal acc1 Kubernetes catalog endpoint

The Stage 0 catalog endpoint, hosted on the team's `acc1`
Kubernetes cluster.  Serves built `.accpkg` blobs + `index.json` +
cosign signatures over HTTPS at
`https://acc-hub.acc1.internal/` (or your chosen hostname).

It is **internal-only**: ACC-team-private through Stage 1.  Stage 2
promotes the public mirror to `acc-roles.dev`; this hub then
becomes the dev/staging endpoint.

## Architecture

```
Ingress (TLS, internal hostname)
    ↓
Service (ClusterIP, port 80)
    ↓
Deployment (nginx serving from /usr/share/nginx/html)
    ↓
PVC (blob storage — .accpkg + .sig files, mounted at packages/)
+ ConfigMap (index.json + nginx.conf, mounted at index.json + nginx.conf)
```

* **nginx** serves both `index.json` (from a ConfigMap) and the
  binary blobs (from a PVC).  Read-only by design.
* **No authentication** — the ingress is internal-only, behind the
  acc1 network's existing access controls.
* **Index update flow (Stage 0, manual)**: operator edits the
  ConfigMap to add a new package row; nginx serves it on next
  request.  Stage 1's `acc-pkg publish` automates this via an
  authenticated webhook (deferred).

## File map

| File | What it does |
|---|---|
| `00-namespace.yaml` | Creates the `acc-hub` namespace |
| `10-pvc.yaml` | Persistent storage for `.accpkg` + `.sig` blobs |
| `20-configmap-index.yaml` | Initial empty `index.json` |
| `21-configmap-nginx.yaml` | nginx config (paths, MIME types, CORS) |
| `30-deployment.yaml` | nginx Deployment (latest stable) |
| `40-service.yaml` | ClusterIP Service on port 80 |
| `50-ingress.yaml` | TLS Ingress at `acc-hub.acc1.internal` |

## Bootstrap

```bash
# From the repo root, on a kubectl context pointing at acc1:
kubectl apply -f gitops/acc-hub/

# Confirm the pod comes up:
kubectl -n acc-hub rollout status deploy/acc-hub
kubectl -n acc-hub get pods

# Confirm the ingress is reachable (from inside the acc1 network):
curl -sS https://acc-hub.acc1.internal/index.json
# → {"schema_version": 1, "packages": []}
```

If your acc1 cluster uses a different ingress hostname / TLS
issuer, edit `50-ingress.yaml` before applying.

## Publishing a package (Stage 0, manual)

```bash
# 1. Build the pilot pack.
python tools/build_pilot_pkg.py coding_agent

# 2. Sign it with the pilot keypair.
cosign sign-blob \
  --key ~/.acc/keys/acc-pilot.key \
  --output-signature dist/acc-coding-agent-0.1.0.accpkg.sig \
  dist/acc-coding-agent-0.1.0.accpkg

# 3. Use the publish helper to push both into the hub PVC and
#    patch the index ConfigMap.
gitops/acc-hub/publish-to-hub.sh \
  dist/acc-coding-agent-0.1.0.accpkg \
  dist/acc-coding-agent-0.1.0.accpkg.sig

# 4. Confirm:
curl -sS https://acc-hub.acc1.internal/index.json | jq .
curl -sS -o /tmp/x.accpkg \
  https://acc-hub.acc1.internal/packages/acc/coding-agent-0.1.0.accpkg
sha256sum /tmp/x.accpkg
# → must match the content_sha256 from `acc-pkg inspect`
```

## Catalog configuration on a client

Once the hub is up + a package is published, point a dev catalog
at it:

```bash
mkdir -p ~/.acc
cp examples/catalogs.dev.yaml ~/.acc/catalogs.yaml
# Edit ~/.acc/catalogs.yaml — adjust the url and key_path to your
# actual hostnames + path to your pilot pubkey.
```

Then:

```bash
acc-pkg list --available
# Should show @acc/coding-agent@0.1.0 from the acc1-internal catalog.

acc-pkg install @acc/coding-agent@0.1.0 \
  --signature <(curl -sS https://acc-hub.acc1.internal/packages/acc/coding-agent-0.1.0.accpkg.sig) \
  --key ~/.acc/keys/acc-pilot.pub
```

(`acc-pkg install` by catalog spec — `acc-pkg install @scope/name@version` —
ships as part of Stage 1's `PROPOSE_INFUSE` runtime path.  Stage 0
still requires the explicit file path.)

## Operational notes

* **PVC sizing**: 5 GiB default.  Each `.accpkg` is a few KB to a
  few MB; 5 GiB holds thousands of versions.  Bump in
  `10-pvc.yaml` if you publish heavily.
* **Index ConfigMap size limit**: K8s caps a ConfigMap at 1 MiB.
  At ~200 bytes per index entry, that's ~5000 packages.  When you
  hit the limit, migrate the index to the PVC and serve it as a
  regular file (deferred follow-up).
* **TLS cert**: assumes a wildcard cert exists on the acc1 cluster
  for `*.acc1.internal`.  If not, add a `cert-manager.io/cluster-issuer`
  annotation to the Ingress.
* **Backup**: the PVC + ConfigMap together are the entire hub
  state.  Existing acc1 cluster backup picks them up via standard
  Kubernetes resource backup — no special handling.

## Stage 1 follow-ups

* `acc-pkg publish` automates the manual `kubectl cp` + ConfigMap
  edit dance via an authenticated HTTPS webhook.
* Cosign keyless / Fulcio integration replaces the local keypair.
* `AccCatalog` CR (operator slice 1.6) renders to the per-pod
  catalogs.yaml ConfigMap so packages installed via
  `AccPackageInstall` use the hub automatically.
