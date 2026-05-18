# Network policy for ACC

ACC can isolate agent pods at the network layer with an **opt-in,
capability-tiered** NetworkPolicy feature — security roadmap **Phase 1**,
designed by proposal 014.

It is off by default.  With `spec.networkPolicy` absent or
`enabled: false`, the operator emits no policy objects and the
collective behaves exactly as before.

## Why

ACC agent pods otherwise have **no network isolation** — any pod that
can route to `NATS:4222` or `Redis:6379` reads every agent signal.
NATS NKeys (Phase 0c) authenticate the *connection*; network policy
controls which pods may *reach* the bus at all.  Defence in depth.

## The tiers

| Tier | Mechanism | Needs |
|---|---|---|
| 0 | none (no-op) | standalone — no Kubernetes |
| 1 | standard K8s `NetworkPolicy` (L3/L4) | a policy-enforcing CNI (OVN-Kubernetes) — **the portable must-have** |
| 2 | FQDN egress: OVN `EgressFirewall` *or* Cilium `CiliumNetworkPolicy` | OVN-Kubernetes *or* Cilium |
| 3 | full L7 — HTTP-method-scoped egress | Cilium |

The operator emits the **highest tier the cluster can enforce**, capped
by `spec.networkPolicy.maxTier`.  Cilium is one optional Tier-2/3
backend — never required.

## Per deploy mode

| Mode | CNI | What you get |
|---|---|---|
| standalone | none (Podman) | Tier 0 — no-op; `NetworkPolicyReady=True/NotApplicableStandalone` |
| edge — MicroShift | OVN-Kubernetes | Tier 1 enforced; Tier 2 via `EgressFirewall` |
| edge — K3s | Flannel | objects emitted but **not enforced**; `NetworkPolicyReady=False/CNIDoesNotEnforce` |
| rhoai — OpenShift | OVN-Kubernetes | Tier 1 + Tier 2; Tier 3 only if Cilium is installed |

K3s/Flannel does not enforce `NetworkPolicy`.  The operator emits the
objects anyway (so a later CNI swap to Calico/Cilium activates them)
but reports the honest verdict instead of false assurance.  Override
the heuristic with `spec.networkPolicy.cniEnforces: "true"|"false"`.

## Configuration

```yaml
spec:
  networkPolicy:
    enabled: true            # master switch (default false)
    maxTier: 1               # 1 = L4 floor; 2 = FQDN egress; 3 = L7
    mode: enforce            # enforce | audit
    cniEnforces: auto        # auto | "true" | "false"
    extraEgressFQDNs: []     # extra allowed hostnames (Tier 2+)
    extraEgressCIDRs: []     # extra allowed CIDR blocks
    allowedExternalLLM: []   # overrides the built-in external-LLM FQDN set
```

## Recommended rollout

1. Enable on a **single canary collective / test corpus** first.
2. Start in `mode: audit` — the operator emits the policy set **without
   the default-deny**, so nothing is dropped; inspect the objects with
   `kubectl describe networkpolicy -n <ns>`.
3. Flip to `mode: enforce` — this adds the default-deny.
4. Raise `maxTier` to `2` for real external-egress control once Tier 1
   is proven.
5. Roll out to the rest of the fleet.

## Verifying

```bash
# What tier did the operator achieve?
oc get agentcorpus <name> -o jsonpath='{.status.networkPolicy}{"\n"}'
oc get agentcorpus <name> \
  -o jsonpath='{.status.conditions[?(@.type=="NetworkPolicyReady")]}{"\n"}'

# Detected CNI capability
oc get agentcorpus <name> -o jsonpath='{.status.prerequisites}{"\n"}'

# The emitted objects
kubectl get networkpolicy,egressfirewall,ciliumnetworkpolicy -n <ns>

# Connectivity smoke test
kubectl exec <agent-pod> -n <ns> -- nc -zv <nats-service> 4222   # allowed
kubectl exec <agent-pod> -n <ns> -- nc -zv 1.1.1.1 22            # should hang

# Cilium clusters — observe would-be / actual drops
hubble observe --namespace <ns> --verdict DROPPED
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Collective stalls right after enabling | a real flow (often DNS) got severed | the DNS allow is emitted first + unconditionally; check `kubectl describe networkpolicy`; use `mode: audit` to canary |
| `NetworkPolicyReady=False CNIDoesNotEnforce` | running on K3s/Flannel | install a policy-capable CNI, or set `cniEnforces` if the heuristic is wrong |
| External LLM calls fail at Tier 2 | FQDN not in the allow-set, or a DNS-TTL race | add the host to `allowedExternalLLM` / `extraEgressFQDNs`; on OVN `EgressFirewall`, prefer Cilium where strict FQDN matching matters |
| Tier requested but `activeTier` is lower | the cluster's CNI can't reach that tier | `status.networkPolicy.backend` shows what was used; Tier 3 needs Cilium |

## See also

- `docs/security-hardening.md` — the full Phase 0–4 security plan.
- `docs/nats-nkeys.md` — NATS NKey authentication (Phase 0c), the
  protocol-layer complement to network policy.
- Proposal 014 in the operator's design vault — the design of record.
