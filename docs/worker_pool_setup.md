# Worker-Pool Setup (D-001 / PR-J · PR-M · PR-Q)

How to bring up a pool of **dormant workers** and have the arbiter
assign roles to them at runtime via signed `ROLE_ASSIGN`.  This is
the alternative to the PR-B "one fixed container per role" model:
declare the desired roles (commonly subroles like
`coding_agent_implementer`) in the agentset, declare the pool
*capacity*, and let the arbiter fill the pool on demand — no podman
churn per infusion.

Related: `docs/WORKFLOW_infusion_to_prompt.md`, `docs/DECISIONS.md`
(D-001), `docs/TESTING.md` (§6 manual checklist).

---

## 1. Mental model

```
collective.yaml (agentset)
   ├─ agents:           ← DESIRED roles  (Role → subrole definitions)
   │    - coding_agent_implementer × 2
   │    - coding_agent_reviewer   × 1
   │    - coding_agent_tester     × 1
   └─ worker_pool: 4    ← CAPACITY to fill them  (= sum of replicas)
```

* **`agents`** lists the roles you want running.  These are usually
  the **subroles** of a parent role (the coding_agent micro-cycle:
  implementer / reviewer / tester), matching the Role → subrole
  hierarchy shown in the Ecosystem screen.
* **`worker_pool: N`** pre-spawns N dormant containers
  (`acc-worker-1` … `acc-worker-N`).  Size it to
  `recommended_pool_size(spec)` = the sum of `replicas` so every
  desired slot has a worker; add headroom for runtime infusions.

A dormant worker boots with `ACC_AGENT_ROLE=dormant`: no
CognitiveCore, no LLM client, just a HEARTBEAT (state `DORMANT`) and
a subscription on `acc.<cid>.role_assign`.  On a valid signed
`ROLE_ASSIGN` it materialises a CognitiveCore for the assigned role
and flips to `ACTIVE`.

---

## 2. Keys

The arbiter signs `ROLE_ASSIGN`; every worker verifies the signature
before promoting.  One Ed25519 keypair per collective (the arbiter's
identity — same key family as ROLE_UPDATE, proposal 011):

| Env var | Who needs it | Holds |
|---------|--------------|-------|
| `ACC_ARBITER_VERIFY_KEY` | **every** agent (incl. workers) | Base64 public key |
| `ACC_ARBITER_SIGNING_KEY` | **arbiter only** | Base64 **private** key |

Generate a fresh pair:

```bash
python -c "from acc.role_assign import generate_keypair_b64; \
priv, pub = generate_keypair_b64(); \
print('ACC_ARBITER_SIGNING_KEY='+priv); \
print('ACC_ARBITER_VERIFY_KEY='+pub)"
```

> **Security note.** In a single-host standalone demo (no NATS auth
> boundary) both keys can live in `./.env`, which every agent reads
> via `env_file`.  Workers never *use* the signing key — only the
> arbiter's reconcile loop signs — so the exposure is the shared-host
> filesystem.  **In production** (RHOAI + NKey auth) mount
> `ACC_ARBITER_SIGNING_KEY` as an **arbiter-only** secret
> (Kubernetes `Secret` projected only into the arbiter pod, or
> systemd `LoadCredential=`) and keep only the **verify** key in the
> shared `.env`.  If the signing key is empty the arbiter's reconcile
> loop logs a warning and emits nothing — workers stay dormant rather
> than promoting on an unsigned payload.

---

## 3. Runbook (standalone / podman)

Order matters: cold-stop → **restart baseline** → apply the worker
overlay.  `apply` *layers* the worker overlay onto a running
baseline (the workers `depends_on` healthy nats + redis); it does
not reliably cold-start the baseline itself.

```bash
cd /path/to/acc            # the deploy dir holding ./.env + acc-deploy.sh

# 1. Provision the keypair (idempotent — skips if present).
grep -q ACC_ARBITER_VERIFY_KEY .env || cat >> .env <<'EOF'

# worker-pool keypair
ACC_ARBITER_VERIFY_KEY=<paste public key>
ACC_ARBITER_SIGNING_KEY=<paste private key>   # arbiter-only in prod
EOF

# 2. Rebuild (if collective.py / agent.py changed) + cold stop.
./acc-deploy.sh build
./acc-deploy.sh down

# 2a. RESTART THE BASELINE so nats/redis are healthy and the
#     arbiter + agents boot with the new .env keys BEFORE the
#     workers (which depend_on nats+redis) get layered on.
./acc-deploy.sh up

# 3. Apply the worker-pool overlay → adds acc-worker-1..N dormant.
./acc-deploy.sh apply collective.worker-pool.yaml

# 4. Confirm the pool is up.
podman ps --format '{{.Names}}\t{{.Status}}' | grep -E 'acc-worker|acc-agent'

# 5. Trigger the arbiter reconcile → assigns the desired roles onto
#    the dormant pool via signed ROLE_ASSIGN.
podman exec acc-tui acc-cli nats pub acc.sol-01.collective.reconcile '{}'

# 6. Verify promotions (~2 heartbeats, ~6s).
sleep 6
podman logs acc-agent-arbiter 2>&1 | grep -iE 'worker_reconcile|role_assign' | tail -10
podman logs acc-worker-1     2>&1 | grep -iE 'role_assign|promoted|REGISTER' | tail -5
```

### Expected output

* **Step 4** — `acc-worker-1` … `acc-worker-N` all `Up`, plus the
  baseline trio (ingester / analyst / arbiter).
* **Step 5–6** — the arbiter logs:
  ```
  worker_reconcile: 3 desired, 0 already active, 3 assigning, 0 unmet
  role_assign: promoted (agent_id=worker-1 new_role=coding_agent_implementer cluster_id='backend' …)
  ```
  on three of the workers.  The 4th stays `DORMANT` (spare).
* **TUI** — Soma / Performance show the promoted workers `ACTIVE`
  under their assigned subroles.

---

## 4. Network-name gotcha

`roles_to_compose` synthesizes the overlay with:

```yaml
networks:
  acc-net:
    external: true
    name: production_acc-net
```

This must match the network podman-compose actually created for the
baseline.  If `apply` errors with *"network production_acc-net not
found"*, check the real name:

```bash
podman network ls | grep acc
```

The name is `<compose-project>_acc-net`; the default project is
`production` (the compose dir).  If your project prefix differs,
that hardcoded `production_acc-net` in `acc/collective.py:roles_to_compose`
is the one value to reconcile (or run `apply` from the
`container/production/` dir so the project prefix matches).

---

## 5. Troubleshooting

Every failure path logs a specific reason — grep the worker:

```bash
podman logs acc-worker-1 2>&1 | grep role_assign
```

| Log line | Cause | Fix |
|----------|-------|-----|
| `verify_key not configured` | the verify key didn't reach the worker's env | the `.env` wasn't re-read — ensure the `down` ran before `up`; confirm `podman exec acc-worker-1 env \| grep ACC_ARBITER_VERIFY_KEY` |
| `signature does not match payload` | arbiter signed with a different key than the worker verifies | the two `.env` keys must be a matched pair |
| `target_agent_id … != self` (debug) | normal — the assignment was for a different worker | none; only the targeted worker promotes |
| arbiter: `no arbiter_signing_key configured` | signing key didn't reach the arbiter | `podman exec acc-agent-arbiter env \| grep ACC_ARBITER_SIGNING_KEY` |
| arbiter: `… 0 assigning, N unmet` | pool smaller than desired slots | raise `worker_pool` in collective.yaml to ≥ `recommended_pool_size` |
| arbiter: `cannot resolve role 'X'` | the subrole has no `roles/X/role.yaml` | add the role definition or fix the name in `agents:` |

---

## 6. Idempotency + re-runs

The reconcile is idempotent.  Re-triggering it after the workers
have promoted is a no-op: `compute_assignments` counts the now-ACTIVE
workers as already satisfying their slots
(`already_satisfied` rises, `assigning` drops to 0).  You can safely
fire `collective.reconcile` on every `collective.yaml` edit — the
arbiter only assigns the *delta*.

To grow the pool, raise `worker_pool`, re-`apply`, and re-trigger.
To re-purpose a worker, edit the `agents:` list and re-trigger — a
worker already running role A is left alone unless A is removed from
the desired set (live re-assignment of an ACTIVE worker is a future
enhancement; today you'd restart that worker to return it to the
dormant pool).

---

## 7. K8s mode

In RHOAI / K8s the operator (`operator/`) owns reconcile via the
`AgentCollectiveSpec` CRD — `worker_pool` maps to a Deployment
replica count of dormant pods, and the controller emits ROLE_ASSIGN
(or sets pod env directly).  The standalone path documented here
catches the podman deployment up to that model; the two share the
`acc.worker_reconcile` matching algorithm.
