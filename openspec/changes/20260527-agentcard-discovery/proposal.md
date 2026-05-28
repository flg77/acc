# Proposal: AgentCard auto-discovery (Kagenti interop)

| Field      | Value                                                              |
|------------|--------------------------------------------------------------------|
| Change ID  | `20260527-agentcard-discovery`                                     |
| Date       | 2026-05-27                                                         |
| Status     | **Landed** (Phase 1; functional once paired with the A2A change below) |
| Depends on | SPIFFE/SPIRE PR-1..5 (workload identity); the A2A interop change below for the card content + signing |
| Cross-refs | Code: `operator/internal/reconcilers/collective/kagenti.go`, `operator/api/v1alpha1/agentcollective_types.go` (`KagentiSpec`). Docs: [`docs/kagenti-discovery.md`](../../../docs/kagenti-discovery.md). Pair: [`20260527-a2a-agent-interop/`](../20260527-a2a-agent-interop/) |

---

## Problem statement

**Kagenti** (Red Hat, kagenti.github.io) is the RHOAI agent platform ACC targets.
Its operator (`kagenti/kagenti-operator`, v0.2.0-alpha.21+) discovers agent
workloads by watching for the label **`kagenti.io/type: agent`** and
auto-creates an `AgentCard` Custom Resource — there is no external registry.
The card is bound to a workload identity (namespace + service account) via
`targetRef`, and A2A peers verify the agent card's `x5c` chain against the
cluster SPIRE.

Today ACC's agent Deployments carry no Kagenti-discoverable label, so a Kagenti
operator running in the same cluster cannot index them. The corollary is
*peer agents can't reach ACC roles via the standard A2A discovery flow.*

## Approach (as landed — Phase 1)

**Do NOT define an ACC-owned AgentCard CRD.** The CRD is owned by Kagenti's
operator; ACC's job is purely to make its workloads *discoverable* by that
operator. This is the key design decision — confirmed during planning, captured
in the vault scope/risk analyses, and what makes Phase 1 a small, safe change.

What landed:

- A new opt-in `Kagenti *KagentiSpec { Enabled bool }` field on
  `AgentCollectiveSpec` (Go), mirroring the existing `SpiffeSpec` opt-in pattern.
  Default off — existing collectives unaffected.
- When `Spec.Kagenti.Enabled == true`, the agent-Deployment reconciler stamps
  `kagenti.io/type: agent` on the Deployment's `ObjectMeta.Labels` **and** the
  pod-template's `Labels`. The Deployment's `Spec.Selector.MatchLabels` is
  intentionally NOT modified — selector labels are immutable in Kubernetes, and
  the Kagenti label is purely a discovery label.
- A new helper `util.KagentiAgentLabel()` and constants `LabelKagentiType` /
  `LabelKagentiTypeAgent` make the wire key + value the single source of truth.
- A new reconciler module `collective/kagenti.go` factors `KagentiEnabled()`
  (predicate) and `AgentObjectLabels()` (canonical-set + Kagenti merge) so the
  agent_deployment reconciler is a one-line read + the policy is unit-testable.

## Out of scope (for this change)

- Defining an ACC-owned AgentCard CRD — explicitly avoided (we reuse Kagenti's).
- Serving the actual card content at `/.well-known/agent-card.json` — that's
  the *A2A interop* change ([`20260527-a2a-agent-interop`](../20260527-a2a-agent-interop/), Phase 1b).
- Signing the card via SPIRE x5c so the `targetRef` identity binding is
  attested — that's the same A2A change, Phase 5.
- Operator-side per-role Kubernetes `Service` to expose the agent's A2A port
  to cluster mesh peers — a small follow-up.
- A spike against a live Kagenti operator to confirm the auto-discovery
  contract end-to-end — gated on having a Kagenti dev cluster.

## Risks

- **CRD reconciliation drift** — Kagenti itself documents manual `kubectl`
  reconcile workarounds; ACC must keep labels accurate across role changes +
  teardown so Kagenti's reconcile stays correct. The selector-immutability
  rule above is the foundation.
- **A2A / AgentCard CRD churn** — Kagenti is at v0.2.0-alpha.21; the CRD
  shape changed at that version. Treat any Kagenti operator bump as an
  explicit re-validation point.
- **Label-only ≠ functional discovery** until the A2A change ships the card
  endpoint + identity binding. The flag stays disabled in production until
  the matching A2A pieces land — which they now have (see
  [`20260527-a2a-agent-interop`](../20260527-a2a-agent-interop/), Phases 1b + 5).

## Verification (landed)

- `operator/test/unit/kagenti_label_test.go` — 8 unit tests:
  `KagentiEnabled` predicate (nil collective / nil spec / explicit false /
  true); `KagentiAgentLabel()` returns exactly `{kagenti.io/type: agent}`;
  `AgentObjectLabels` returns the canonical set when disabled, merges the
  Kagenti label when enabled, never mutates the caller's input (so selector
  labels stay selector-safe).
- CRD schema for the new `kagenti` block lives in
  `operator/config/crd/bases/acc.redhat.io_agentcollectives.yaml`; controller-gen
  re-runs idempotently against the Go source (no drift).
- Operator-side build / test gate: `make manifests && make generate && go test ./...`.
- End-to-end against a live Kagenti operator is still a *deferred spike* — see
  Out of scope.

## See also

- User-facing docs: [`docs/kagenti-discovery.md`](../../../docs/kagenti-discovery.md)
- Paired change: [`20260527-a2a-agent-interop`](../20260527-a2a-agent-interop/)
  (the A2A card content + signing that makes discovery functional).
- Topology + rationale: vault note `ACC RHOAI/Edge-Hub-A2A topology.md` (why
  edge/standalone don't use A2A and the hub acts as a translation gateway).
