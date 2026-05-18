# NATS NKey authentication for ACC

ACC can authenticate every NATS connection — and gate which subjects
each connection may publish/subscribe — using **NKeys**, a native
NATS identity primitive (Ed25519 keypairs).  This is **Phase 0c** of
the security roadmap; proposal 013 in the operator's Obsidian vault
is the design of record.

NKeys are opt-in.  With `security.nkey.enabled: false` (the default)
ACC connects to NATS exactly as it always has — no credentials, no
permission matrix.

## Why

The legacy NATS bus is **URL-only and unauthenticated**: any process
that can open a socket to the NATS port can publish on any subject,
including the arbiter-only control subjects (`acc.{cid}.plan.*`,
`acc.{cid}.centroid`, `acc.{cid}.domain.*`) and the cross-corpus
`acc.bridge.*` subjects.  The "only the arbiter may publish" rules
(annotated `A-011` / `A-012` / `A-016` in `acc/signals.py`) were, on
the wire, only docstrings.

NKeys make those rules **server-enforced**: each identity presents a
public key and the NATS server applies a per-identity publish/
subscribe permission matrix.

## NKeys vs SPIFFE

They are complementary, not alternatives:

- **SPIFFE** (proposal 011) signs the *contents* of a `ROLE_UPDATE`
  payload — a payload-integrity guarantee.
- **NKeys** (this doc) authenticate the *connection* and gate which
  *subjects* an identity may touch — a transport-authorization
  guarantee.

A deployment may run either, both, or neither.

## The eight identities

| Identity | Used by | Posture |
|---|---|---|
| `arbiter` | the arbiter agent | publishes the control subjects; subscribes to everything |
| `ingester` / `analyst` / `synthesizer` / `coding_agent` / `observer` | worker agents | receive work, report completions; cannot publish control subjects |
| `tui` | the TUI / CLI | read-only on the bus except `plan.submit`, `oversight.*`, `task.assign`, `task.cancel` |
| `leaf` | the edge leaf-node link to the hub | `acc.bridge.>` only |

The permission matrix is the single canonical file
[`acc/nats_permissions.yaml`](../acc/nats_permissions.yaml).  It is
consumed by **both** the operator's Go `nats.conf` renderer and the
standalone Python CLI, so the two never drift.  A contract test
(`tests/test_nats_permissions.py`) fails CI if a subject helper in
`acc/signals.py` is left uncovered.

## Configuration

```yaml
security:
  nkey:
    enabled: true
    role: arbiter                    # this process's identity
    seed_path: /run/acc/nkeys/seed   # this process's NKey seed file
    leaf_seed_path: ""               # edge leaf-link seed (edge only)
```

Env-var overrides: `ACC_NKEY_ENABLED`, `ACC_NKEY_SEED_PATH`,
`ACC_NKEY_ROLE`, `ACC_NKEY_LEAF_SEED_PATH`.

The **seed** is the secret half of the keypair (`S...`-prefixed).  It
must be `0600` and is never logged, never placed in a ConfigMap, and
never rendered in the TUI.

## Enabling it — standalone (Podman)

```bash
# 1. Mint the eight identities (seeds + public_keys.json).
./scripts/acc-nkeys generate --out-dir ./nkeys

# 2. Render a nats.conf with the authorization block.
./scripts/acc-nkeys render-conf --keys-dir ./nkeys --out ./nats.conf

# 3. In podman-compose.yml: swap the nats `command:` to
#    ["-c", "/etc/nats/nats.conf"] and uncomment the nats.conf mount.

# 4. Point every ACC process at its role seed:
export ACC_NKEY_ENABLED=true
export ACC_NKEY_SEED_PATH=./nkeys/seed-arbiter   # per role
```

`acc-nkeys generate` refuses to overwrite an existing key set — pass
`--force` only for a deliberate rotation (see *Rotation*).

## Enabling it — edge / rhoai (operator)

Set the flag on the `AgentCorpus` CR:

```yaml
spec:
  infrastructure:
    nats:
      nkeyAuth:
        enabled: true
```

The operator then:

1. generates the eight NKeys once and stores the seeds in a Secret
   `{corpus}-nats-nkeys` — and **never regenerates it** (regenerating
   would lock every running pod off the bus);
2. renders the `authorization` block into the `{corpus}-nats-config`
   ConfigMap from the embedded permission matrix;
3. projects each agent pod's role seed via a `SecretKeyRef` and sets
   `ACC_NKEY_ENABLED` / `ACC_NKEY_SEED_PATH` on the container.

On **edge**, the leaf-node link to the hub authenticates with the
`leaf` NKey.

## Verifying

```bash
# Standalone — an unauthenticated publish is rejected:
nats pub acc.sol-01.plan.x '{}'        # → permissions violation

# rhoai — the operator-generated Secret has eight seeds:
kubectl get secret <corpus>-nats-nkeys -o jsonpath='{.data}' | jq keys

# The rendered authorization block:
kubectl get configmap <corpus>-nats-config -o jsonpath='{.data.nats\.conf}'
```

## Rotation

0c ships **static** NKeys.  Rotation is a deliberate manual
procedure, because regenerating keys invalidates every connected
client:

1. standalone — `acc-nkeys generate --force`; rhoai/edge — delete the
   `{corpus}-nats-nkeys` Secret so the operator regenerates it;
2. roll-restart every ACC process (agents, TUI) so each picks up its
   new seed;
3. the NATS server reloads the new `nats.conf` authorization block.

Automated rotation is out of scope for 0c.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `NKey auth requested but seed file not found` | `nkey.enabled` true but no seed at `seed_path` | run `acc-nkeys generate` (standalone) or check the operator-projected Secret |
| NATS logs `permissions violation` on publish/subscribe | identity lacks that subject in the matrix, or stale seed after a rotation | confirm the role seed matches the rendered `nats.conf`; re-render after a key change |
| TUI cannot connect after enabling NKeys | `ACC_NKEY_SEED_PATH` points at an agent seed, not the `tui` seed | point the TUI process at `seed-tui` |
| A new subject is rejected everywhere | `acc/nats_permissions.yaml` does not cover it | add a glob; the contract test will tell you which subject |

## See also

- `acc/nats_permissions.yaml` — the canonical permission matrix.
- `acc/nkeys.py` — NKey generation + `nats.conf` rendering.
- `scripts/acc-nkeys` — the standalone-mode CLI.
- [`docs/spiffe.md`](./spiffe.md) — SPIFFE payload signing (the
  complementary half of agent identity).
- [`docs/security-hardening.md`](./security-hardening.md) — the full
  Phase 0–4 security plan.
