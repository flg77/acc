# SPIFFE workload identity at the edge

This is the edge companion to [`docs/spiffe.md`](./spiffe.md).  Read
that first — it covers the config surface, the JWT-SVID model, and
the rhoai story.  This doc covers what changes when ACC runs in
`deploy_mode: edge`.

Proposal 012 in the operator's Obsidian vault is the design of
record.

## Why edge is different

An edge ACC runs on MicroShift / K3s, is offline-capable, and reaches
its rhoai datacenter over a NATS leaf node.  Three things make SPIFFE
at the edge its own problem:

- **Trust topology** — an edge site is not just "a smaller rhoai".
  It is either *downstream* of an rhoai parent (nested), a *peer* of
  other edges (federated), or wants nothing to do with SPIRE
  (ed25519).
- **Partitions are normal** — an edge loses its uplink routinely.
  SPIFFE must not turn a network blip into an outage.
- **Physical exposure** — a node on a factory floor is easier to
  tamper with than a datacenter pod, so per-site trust boundaries
  matter.

## Pick a topology

```
Does this edge site reach an rhoai datacenter SPIRE server?
├── yes → nested        (recommended — shared trust domain)
└── no
     ├── multiple edge sites that must cross-trust → federated
     └── constrained hardware / won't run SPIRE    → ed25519
```

Set it per site via `security.spiffe.edge_topology` (and the
matching `AgentCollective.spec.spiffe.edgeTopology`).

| Topology | Trust root | SPIFFE ID shape | Offline `rotate`? |
|---|---|---|---|
| `nested` | rhoai parent SPIRE | `spiffe://<td>/edge/<site>/role/<id>` | yes |
| `federated` | this edge's own SPIRE | `spiffe://<td>/role/<id>` | no (use `degrade`) |
| `ed25519` | n/a (legacy keypair) | — | n/a |

### nested — the recommended default

The edge runs a SPIRE server configured as a *nested* server
downstream of the rhoai parent.  All edge identities resolve under
the **same trust domain** as rhoai, with an `/edge/<site-id>/`
qualifier so two edge sites can never issue colliding IDs.

Because the trust root is shared, a rhoai-issued `ROLE_UPDATE`
verifies on an edge agent — and vice versa — with **no bundle
translation**.

Setup: see [`deploy/edge-spire/README.md`](../deploy/edge-spire/README.md).
Key config:

```yaml
security:
  signing_mode: spiffe
  spiffe:
    enabled: true
    trust_domain: acc-prod.example.com   # SAME as the rhoai parent
    edge_topology: nested
    edge_site_id: factory-a              # unique per site
    parent_spire_url: spire.dc.example.com:8081
    offline_action: rotate
```

`edge_site_id` is **operator-supplied** and must be unique across
every site under the trust domain (proposal 012 Q5).

### federated — multi-site, no datacenter

Each edge owns a **distinct** trust domain and cross-trusts peers by
exchanging trust bundles.  No rhoai parent required — built for
industrial multi-site deployments in a customer airgap.

```yaml
security:
  signing_mode: spiffe
  spiffe:
    enabled: true
    trust_domain: factory-a.acc.local    # this site's own domain
    edge_topology: federated
    federation_peers:
      - factory-b.acc.local@https://factory-b.example.com:8443/bundle
    offline_action: degrade              # rotate needs nested
```

The operator issues one `ClusterFederatedTrustDomain` per peer.

### ed25519 — the escape valve

Constrained hardware (a Raspberry Pi fleet with no spare CPU for a
SPIRE server) keeps the legacy keypair model.  `edge_topology:
ed25519` — or simply leave `signing_mode: ed25519`.  This stays
supported forever; the v0.5.0 default flip is rhoai-only.

## Offline survival

When the uplink drops, `spiffe-helper` can no longer refresh the
trust bundle.  Once the cached bundle's age crosses
`offline_max_age_h` (default 72 h), `offline_action` fires:

| `offline_action` | Behaviour | Topology |
|---|---|---|
| `rotate` (default) | edge-local SPIRE rotates its own signing material; agent keeps serving indefinitely | `nested` only |
| `degrade` | agent goes read-only — existing tasks finish, new TASK_ASSIGN gets CONFLICT | any |
| `shutdown` | agent pods exit non-zero (fail-safe) | any |

The agent's `acc.spiffe_offline.OfflineBundleMonitor` polls bundle
age every `bundle_refresh_h`, emits an `acc.spiffe.offline` NATS
audit event on each stale detection, and invokes the action.

## Migration path

An existing edge on v0.4.x running `signing_mode: ed25519`:

1. Install SPIRE for the chosen topology
   ([`deploy/edge-spire/`](../deploy/edge-spire/)).
2. Flip to `signing_mode: spiffe` with `allow_ed25519_fallback:
   true` — SPIFFE is tried first, Ed25519 covers any gap.
3. Once stage 2 runs clean, set `allow_ed25519_fallback: false`.

The v0.5.0 rhoai default flip does **not** touch edge — edge stays
opt-in.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `status.spiffeError: edgeTopology=nested requires edgeSiteID` | nested topology, blank `edge_site_id` | set `edge_site_id` (acc-config.yaml + the CR) |
| `status.spiffeError: federationPeers entries are not <trust-domain>@<url>` | malformed peer entry | fix the `td@url` format; other peers still federate |
| agent logs `trust bundle … is stale` then degrades/shuts down | partition longer than `offline_max_age_h` | restore the uplink; or raise `offline_max_age_h`; or switch to `offline_action: rotate` (nested only) |
| edge agent's `ROLE_UPDATE` rejected by the rhoai arbiter | trust domains differ | nested edges must use the **same** `trust_domain` as the parent |
| airgapped site, bundle endpoint unreachable | no route for `https_web` federation | sneakernet the peer bundle — `kubectl apply` a hand-built `ClusterFederatedTrustDomain` (see `deploy/edge-spire/federation-peer.yaml.example`) |

## Compatibility matrix

The locked requirement (proposal 012) is bi-directional
compatibility with rhoai.  The six message-flow directions and
their trust source:

| Direction | Trust source |
|---|---|
| rhoai → rhoai | rhoai SPIRE bundle |
| rhoai → edge (nested) | edge's cached parent bundle |
| edge → rhoai (nested) | rhoai's record of the nested bundle |
| edge → edge, same site | edge SPIRE bundle |
| edge → edge, different nested sites | shared parent bundle |
| edge ↔ edge (federated) | federated bundle exchange |

`tests/integration/test_spiffe_edge_e2e.py` exercises these against
synthetic JWT-SVIDs.

## See also

- [`docs/spiffe.md`](./spiffe.md) — the SPIFFE feature overview.
- [`deploy/edge-spire/README.md`](../deploy/edge-spire/README.md) —
  install runbook for both topologies.
- [`docs/howto-edge.md`](./howto-edge.md) — full edge deployment.
