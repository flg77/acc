# Edge SPIRE deployment manifests

Operator-applied manifests for running ACC with SPIFFE workload
identity at the **edge** (proposal 012).  ACC does not vendor SPIRE —
these files configure the upstream SPIRE Helm chart + a trust-bundle
fetcher for offline survival.

See `012 - SPIRE for ACC edge deployments.md` in the operator's
Obsidian vault for the full design.

## Files

| File | What it is | How to apply |
|---|---|---|
| `nested-spire-server.values.yaml` | Helm values overlay for the upstream `spiffe/spire` chart, configured for **nested** topology (edge SPIRE downstream of an rhoai parent). | `helm upgrade --install` (see below) |
| `edge-bundle-fetcher.yaml` | PVC + CronJob that caches the rhoai parent's trust bundle so edge agents survive a partition. | `kubectl apply -f` |

## Prerequisites

- An OpenShift / MicroShift / K3s edge cluster.
- Network reachability from the edge to the rhoai datacenter's SPIRE
  server (typically over the same path the NATS leaf node uses).
- Helm 3.
- An RWX storage class for the bundle-cache PVC.  Sites without one:
  see *Offline cache without RWX* below.

## Install — nested topology

1. **Install SPIRE** (per edge cluster):

   ```bash
   helm repo add spiffe https://spiffe.github.io/helm-charts-hardened/
   helm upgrade --install spire spiffe/spire \
     --namespace spire-system --create-namespace \
     --values deploy/edge-spire/nested-spire-server.values.yaml \
     --set-string global.spire.trustDomain="acc-prod.example.com" \
     --set-string spire-server.upstreamAuthority.spire.server.address="spire.dc.example.com" \
     --set spire-server.upstreamAuthority.spire.server.port=8081
   ```

   The trust domain **must** be the same string the rhoai parent uses
   — that shared root is what lets a rhoai-issued ROLE_UPDATE verify
   on an edge agent without bundle translation.

2. **Apply the bundle fetcher.**  Edit `<PARENT_BUNDLE_URL>` in
   `edge-bundle-fetcher.yaml` to point at the parent SPIRE server's
   bundle endpoint, then:

   ```bash
   kubectl apply -f deploy/edge-spire/edge-bundle-fetcher.yaml
   ```

3. **Configure the ACC edge collectives.**  On every `AgentCollective`
   in the edge corpus:

   ```yaml
   spec:
     spiffe:
       enabled: true
       trustDomain: acc-prod.example.com
       edgeTopology: nested
       edgeSiteID: factory-a          # unique per site — see below
   ```

   And the matching `acc-config.yaml` (synced via
   `scripts/sync-host-config.sh`):

   ```yaml
   security:
     signing_mode: spiffe
     spiffe:
       enabled: true
       trust_domain: acc-prod.example.com
       edge_topology: nested
       edge_site_id: factory-a
       parent_spire_url: spire.dc.example.com:8081
   ```

The ACC operator then issues a `ClusterSPIFFEID` with the
site-qualified path
`spiffe://acc-prod.example.com/edge/factory-a/role/<collective>`.

## `edgeSiteID` must be unique per site

Two edge sites under the same trust domain **must** use distinct
`edgeSiteID` values — otherwise they issue colliding SPIFFE IDs and
SPIRE refuses the second registration.  The ACC operator's
cluster-scoped uniqueness check (proposal 012 §1 — landing alongside
the validation in a follow-up) catches this; until then, treat it as
an operator-discipline requirement.  Good values: `factory-a`,
`plant-mke`, `store-1024`.  Avoid placeholders like `site-1`.

## Offline cache without RWX

`edge-bundle-fetcher.yaml` uses a `ReadWriteMany` PVC so the CronJob
can write while agent pods on other nodes read.  Single-node edge
clusters (MicroShift on one box) often lack an RWX storage class.

Two options:

1. **Single-node**: switch the PVC to `ReadWriteOnce` — all pods land
   on the one node anyway.
2. **Multi-node, no RWX**: run the fetcher as a `DaemonSet` writing a
   `hostPath` (`/var/lib/acc/spiffe-cache/bundle.pem`) instead of the
   CronJob+PVC.  Each node then has its own local copy.  A DaemonSet
   variant is tracked for proposal 012 PR-3.

## Federated topology

For edge sites with **no rhoai parent** (`edgeTopology: federated`),
the SPIRE install + federation peer config differ — that path lands
in proposal 012 PR-3 alongside the offline-action implementation.
