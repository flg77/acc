# SPIFFE workload identity for ACC

ACC can authenticate agent-to-arbiter `ROLE_UPDATE` signatures using
**SPIFFE** workload identity instead of a static Ed25519 keypair.
This is the rhoai-facing half of the feature; for edge deployments
see [`docs/spiffe-edge.md`](./spiffe-edge.md).

Proposal 011 in the operator's Obsidian vault is the design of
record.

## Why

The legacy trust model (`security.arbiter_verify_key`) is a **single
static Ed25519 public key**.  It works, but in a datacenter it has
sharp edges:

- no rotation — a leaked private key compromises the collective's
  trust forever;
- no per-workload identity — agents can't prove who they are to
  Kagenti, Llama Stack's MaaS gateway, or TrustyAI;
- ACC pods can't participate in RHOAI 3.4's centralised
  SPIFFE/SPIRE-based AgentOps identity.

SPIFFE replaces "trust one static key" with "trust a SPIRE-attested,
short-lived, rotatable identity".

## What a JWT-SVID proves — and what it does not

When `signing_mode: spiffe`, a `ROLE_UPDATE` carries the arbiter's
**JWT-SVID** in its `signature` field.  Verifying it (against the
SPIRE trust bundle) proves:

- the token was minted by the collective's SPIRE,
- it was issued for audience `acc-role-update`,
- the bearer is the expected arbiter (the `sub` claim — enforced
  only when `security.spiffe.arbiter_spiffe_id` is set),
- it has not expired.

It does **not** cryptographically bind to the `ROLE_UPDATE`'s
`role_definition` content.  Content integrity stays the job of the
existing `approver_id == <registered arbiter>` check and
role-version monotonicity.  The SPIFFE upgrade is a *key-management*
improvement, not a new content-integrity guarantee — see the
`acc/spiffe_verify.py` module docstring for the full reasoning.

## Prerequisites

SPIFFE mode requires three cluster components — ACC does **not**
vendor them:

1. **SPIRE server + agents** — install from the upstream
   [SPIFFE Helm charts](https://github.com/spiffe/helm-charts-hardened).
2. **spire-controller-manager** — provides the `ClusterSPIFFEID`
   CRD.  ACC's operator detects its `spire.spiffe.io` API group and
   reports it in `AgentCorpus.status.prerequisites.spireInstalled`.
3. **SPIFFE CSI Driver** — mounts the SPIRE Agent Workload API
   socket into agent pods (the `spiffe-helper` sidecar needs it).

If SPIRE is absent, ACC degrades gracefully — see *Troubleshooting*.

## Configuration

Two surfaces, both driven from `acc-config.yaml`:

```yaml
security:
  signing_mode: spiffe          # ed25519 | spiffe | auto
  spiffe:
    enabled: true
    trust_domain: acc-prod.example.com
    svid_mount_path: /run/spire/sockets
    jwt_audience: acc-role-update
    allow_ed25519_fallback: true
    arbiter_spiffe_id: ""        # optional — see below
```

…plus the `AgentCollective` CR the operator reconciles:

```yaml
spec:
  spiffe:
    enabled: true
    trustDomain: acc-prod.example.com
```

`signing_mode: auto` resolves to a per-`deploy_mode` default
(`_SIGNING_MODE_BY_DEPLOY_MODE`).  In v0.4.x every mode defaults to
`ed25519` — see *The v0.5.0 default flip*.

### Trust domain

`trust_domain` is the SPIFFE namespace, e.g.
`acc-prod.example.com`.  Leave it blank and the operator derives
`<corpus-name>.acc.local`.  All agents in one corpus share a trust
domain; edge sites under a shared root add a site qualifier (see
the edge doc).

### `arbiter_spiffe_id` — strict subject binding

When set (e.g. `spiffe://acc-prod.example.com/role/research`), the
verifier additionally enforces that the JWT-SVID's `sub` claim
equals it — proving the `ROLE_UPDATE` came from the arbiter
specifically.  When blank, arbiter identity rests solely on the
`approver_id` application-layer check.  Recommended for production.

## Migration — three stages

SPIFFE is opt-in and rolls out in stages so a collective is never
stranded:

| Stage | `signing_mode` | `allow_ed25519_fallback` | Posture |
|---|---|---|---|
| 1 (today) | `ed25519` | — | legacy static key; unchanged |
| 2 | `spiffe` | `true` | SPIFFE verified first; on any SPIFFE error (SPIRE down, JWT expired, bundle gap) falls back to the Ed25519 path — no outage during rollout |
| 3 | `spiffe` | `false` | SPIFFE only; the Ed25519 path is gone |

Move stage 1 → 2 once SPIRE is installed and the operator shows
`status.spiffeIssued: true` on every collective.  Move 2 → 3 once
you've watched stage 2 run clean for a release.

## The v0.5.0 default flip

In **v0.5.0** the `rhoai` row of `_SIGNING_MODE_BY_DEPLOY_MODE`
flips from `ed25519` to `spiffe`.  After that, an rhoai operator
who hasn't set `signing_mode` explicitly gets SPIFFE by default
(with `allow_ed25519_fallback` still defaulting to `true`, so the
flip is safe).  `standalone` and `edge` stay on `ed25519` by
default — SPIRE is not a sensible laptop dependency, and edge has
its own opt-in path.

Operators who want to **stay** on Ed25519 past v0.5.0 set
`signing_mode: ed25519` explicitly — that always wins over the
deploy-mode default.

## How it works end to end

```
operator                          agent pod
────────                          ─────────
SpiffeReconciler                   spiffe-helper sidecar
  observes AgentCollective           talks to the SPIRE Agent
  spec.spiffe.enabled                Workload API (CSI-mounted)
        │                                  │
        ▼                                  ▼
  issues ClusterSPIFFEID            writes svid.pem + jwt_svid.token
  (podSelector → this                + jwt_bundle.json into
   collective's pods)                /run/spire/sockets
        │                                  │
        ▼                                  ▼
  SPIRE attests the pods    →   arbiter reads its JWT-SVID, puts it
                                in ROLE_UPDATE.signature
                                          │
                                          ▼
                                acc.role_store verifies it against
                                jwt_bundle.json (acc.spiffe_verify)
```

## Verifying

```bash
# The operator issued a ClusterSPIFFEID per SPIFFE-enabled collective:
kubectl get clusterspiffeids

# The AgentCollective status shows what the operator computed:
kubectl get agentcollective <name> -o jsonpath='{.status.spiffeID}{"\n"}'
kubectl get agentcollective <name> -o jsonpath='{.status.spiffeIssued}{"\n"}'

# Inside an agent pod, the spiffe-helper sidecar's output:
kubectl exec <agent-pod> -c agent -- ls -l /run/spire/sockets
#   svid.pem  svid_key.pem  svid_bundle.pem  jwt_svid.token  jwt_bundle.json
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `status.spiffeError: …spire-controller-manager… not installed` | SPIRE absent | install SPIRE + spire-controller-manager; or set `signing_mode: ed25519` |
| ROLE_UPDATE rejected: `SPIFFE JWT-SVID verification failed` | bundle stale, clock skew, wrong audience | check the spiffe-helper sidecar logs; confirm `jwt_bundle.json` is fresh; the verifier allows ±60 s skew |
| Agent logs `SPIFFE verification failed — falling back to ed25519` | transient SPIRE issue, `allow_ed25519_fallback: true` | expected during stage 2; investigate if persistent |
| `PyJWT is required for signing_mode=spiffe` | dependency stripped | `pip install pyjwt` (it is a declared dep — only a custom slim build hits this) |

## Edge interoperability

When ACC runs at the edge (`deploy_mode: edge`), SPIFFE works the
same way but the *trust topology* differs — an edge site is nested
under an rhoai parent, federated with peer edges, or stays on
Ed25519.  In the **nested** topology the edge shares the rhoai
trust domain, so a rhoai-issued `ROLE_UPDATE` verifies on an edge
agent (and vice versa) with no bundle translation — the SPIFFE ID
just carries an extra `/edge/<site-id>/` segment.  Full detail —
topology decision tree, offline survival, the bi-directional
compatibility matrix — is in [`docs/spiffe-edge.md`](./spiffe-edge.md).

## See also

- [`docs/spiffe-edge.md`](./spiffe-edge.md) — edge topologies
  (nested / federated), offline survival.
- [`docs/howto-rhoai.md`](./howto-rhoai.md) — full rhoai deployment.
- [`docs/role-sync.md`](./role-sync.md) — file ↔ CRD role sync
  (proposal 010); orthogonal to SPIFFE but both touch ROLE_UPDATE.
- `acc/spiffe_verify.py` — the verifier, with the definitive
  "what a JWT-SVID proves" note.
