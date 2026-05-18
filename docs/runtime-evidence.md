# Runtime-evidence Cat-A for ACC

ACC can fold **kernel-level evidence** — what an agent process actually
did (`execve`, `openat`, outbound `connect`) — into Category-A
governance. This is **Phase 3** of the security roadmap, designed by
proposal 015.

It is off by default. With `governance.runtimeEvidence` absent or
`enabled: false`, Cat-A behaves exactly as before (metadata-only).

## Why

Cat-A today evaluates *signal metadata the agent process itself
supplies* — `signal_type`, `agent_role`, and so on. A compromised,
prompt-injected, or drifted agent can present clean metadata while
doing anything underneath. Kernel evidence is what the process *did*,
observed below the application layer, where the agent cannot edit the
story. Defence in depth — each layer is blind where the other sees.

## Provider-agnostic by design

ACC is a **consumer** of runtime-security evidence. It never installs,
operates, or mandates a runtime-security tool — it detects whichever
the cluster already runs and consumes its events.

| Evidence | Backend(s) | Hook |
|---|---|---|
| process exec / file open | **RHACS** (preferred on OpenShift) · **Falco** · **Tetragon** | `execve`, `openat` |
| outbound network connect | **OpenShift NetObserv** (eBPF flow logs, OVN-aware) | `connect` |

On OpenShift the preference order is **RHACS → Falco → Tetragon** —
RHACS is Red Hat's own product and its runtime collector is already an
eBPF probe. If no backend is present, Cat-A simply stays metadata-only.

## Architecture

```
runtime-security tool ──┐
(RHACS / Falco / Tetragon)│   acc-runtime-evidence-bridge
NetObserv ────────────────┤──▶ (adapter framework, normalises to
                          │    KERNEL_EVENT)  ──▶  NATS acc.{cid}.kernel
                          │                              │
                          ▼                              ▼
                  one privileged Deployment      each agent's CognitiveCore
                  (the only privileged piece)    folds events for its own
                                                 pod into Cat-A
```

Agent pods are unchanged — only the bridge Deployment is privileged.

## Per deploy mode

| Mode | Runtime evidence |
|---|---|
| standalone | N/A — no privileged-DaemonSet eBPF attach in Podman; reconciler no-op |
| edge (MicroShift/K3s) | opt-in, only where a backend is detected (kernel-dependent) |
| rhoai (OpenShift) | full — RHACS/Falco/Tetragon for exec+file, NetObserv for connect |

## Configuration

```yaml
spec:
  governance:
    runtimeEvidence:
      enabled: true            # master switch (default false)
      enforce: false           # false = observe baseline; true = block
      observeWindowDays: 28    # recommended observe length
      preferredBackend: auto   # auto | rhacs | falco | tetragon
```

The operator detects the backend, deploys the bridge, and sets
`ACC_RUNTIME_EVIDENCE_ENABLED` / `ACC_RUNTIME_ENFORCE` on agent pods.

## Backend setup

The cluster's security team operates the runtime-security tool; ACC
ships **recommended rule samples** under `regulatory_layer/runtime/`:

- **Tetragon** — apply `regulatory_layer/runtime/tetragon-tracingpolicy.yaml`.
- **Falco** — mount `regulatory_layer/runtime/falco-rules.yaml` into Falco's
  `rules.d`; Falco must run with `json_output: true`.
- **RHACS** — no rule file; ACC consumes RHACS's existing process-baseline
  violations.
- **NetObserv** — no rule file; the bridge filters all flow logs to ACC pods.

## The rules

`regulatory_layer/category_a/kernel_events.rego` is the reference rule
set (the agent runs the equivalent `acc.governance.KernelEventEvaluator`
inline):

- **K-001** — `execve` of a binary outside the agent image's known paths.
- **K-002** — `openat` on `/proc/<pid>/mem` (unambiguously malicious).
- **K-003** — outbound `connect` — recorded as evidence, not enforced
  inline (it needs approved-CIDR context).

## Recommended rollout

1. Enable on a **canary collective** first.
2. Start in **observe** (`enforce: false`) — kernel violations log as
   `OBSERVED:kernel:*` but never block. Run the observe window.
3. Tune the binary allowlist from the observe log.
4. Flip `enforce: true` — violations now `BLOCK:kernel:*` + `ALERT_ESCALATE`.
5. Roll out to the fleet.

## Verifying

```bash
oc get agentcorpus <name> -o jsonpath='{.status.runtimeEvidence}{"\n"}'
oc get agentcorpus <name> \
  -o jsonpath='{.status.conditions[?(@.type=="RuntimeEvidenceReady")]}{"\n"}'
oc get agentcorpus <name> -o jsonpath='{.status.prerequisites}{"\n"}'
kubectl get deploy -l app.kubernetes.io/component=runtime-evidence-bridge -n <ns>

# In observe mode, a synthetic unexpected exec in an agent pod surfaces
# as OBSERVED:kernel:execve:* in the agent's audit record (cat_a_result).
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeEvidenceReady=False NoBackendDetected` | no RHACS/Falco/Tetragon in the cluster | install a runtime-security tool, or accept metadata-only Cat-A |
| Bridge deployed but no `KERNEL_EVENT`s | the backend isn't emitting events for ACC pods | apply the `regulatory_layer/runtime/` rule sample; check pod labels |
| False `ALERT_ESCALATE` floods after `enforce: true` | binary allowlist too narrow | tune from the observe log; widen `kernel_events.rego` allowlist |
| Falco not detected | the Falco DaemonSet lacks the `app.kubernetes.io/name=falco` label | label it, or rely on RHACS/Tetragon |

## See also

- `docs/security-hardening.md` — the full Phase 0–4 security plan.
- `docs/nats-nkeys.md` — NATS NKeys (Phase 0c), the transport-auth complement.
- Proposal 015 in the operator's design vault — the design of record.
