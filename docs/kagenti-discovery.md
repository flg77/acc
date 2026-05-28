# Kagenti AgentCard auto-discovery — Phase 1 (operator labeling)

Implements Phase 1 of OpenSpec
[`20260527-agentcard-discovery`](../openspec/changes/20260527-agentcard-discovery/proposal.md)
([tasks](../openspec/changes/20260527-agentcard-discovery/tasks.md)).
Paired with [`20260527-a2a-agent-interop`](../openspec/changes/20260527-a2a-agent-interop/proposal.md)
(the A2A card endpoint + JWT-SVID signing that make discovery functional).
See also [`docs/a2a-interop.md`](a2a-interop.md). When enabled, the operator stamps the label
`kagenti.io/type: agent` on each agent Deployment and pod so **Kagenti's own operator
auto-creates an AgentCard CR** for the workload — ACC deliberately does **not** define
its own AgentCard CRD.

## What this gives you

A future Kagenti-enabled RHOAI cluster can *discover* ACC agents as soon as it lands.
The discovery side of the integration is wired without any new ACC dependency.

## What this does *not* (yet) give you

**Phase 1 is label-only.** Kagenti will find the workload but cannot yet fetch a valid
`agent-card.json` — that requires:

- **A2A adapter** (OpenSpec `20260527-a2a-agent-interop`) serving
  `/.well-known/agent-card.json` per routable role, content sourced from the role
  definition.
- **Identity convergence** (SPIRE + Keycloak) binding the AgentCard's `targetRef` to the
  workload's namespace + service account, with the card signed via the cluster SPIRE
  x5c chain.

Until *both* land, **leave this flag disabled** in production. The constant is wired so
the moment those prerequisites ship, flipping `kagenti.enabled: true` is the only
operator-side step needed.

## How to opt in

Per `AgentCollective`:

```yaml
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCollective
metadata:
  name: research
spec:
  collectiveId: research-01
  corpusRef: { name: my-corpus }
  agents: [ ... ]
  llm: { ... }
  kagenti:
    enabled: true        # default: false
```

Default off — omitting the field changes nothing for existing collectives.

## What changes under the hood

- The agent Deployment's `metadata.labels` and `spec.template.metadata.labels` gain
  `kagenti.io/type: agent`.
- The Deployment's `spec.selector.matchLabels` is **unchanged** — selector labels are
  immutable in Kubernetes, and the Kagenti label is purely a discovery label.
- Nothing else: no new container, no new sidecar, no new ACC dependency.

## How it's tested

`operator/test/unit/kagenti_label_test.go` covers:

- `KagentiEnabled` predicate (nil collective, nil spec, explicit false, explicit true).
- `KagentiAgentLabel()` returns exactly `{kagenti.io/type: agent}`.
- `AgentObjectLabels` returns the canonical set untouched when disabled; merges the
  Kagenti label in when enabled; never mutates the caller's map (so selector labels
  stay selector-safe).

## Roadmap (so this becomes functional)

| Phase | Tracked in | Status |
|---|---|---|
| 1 — Operator labeling (this) | `docs/kagenti-discovery.md` | Landed |
| 2 — A2A adapter (serves the card) | OpenSpec `20260527-a2a-agent-interop` | Proposed |
| 3 — Identity convergence + spike on live Kagenti | OpenSpec `20260527-agentcard-discovery` (remaining tasks) + identity-convergence scope analysis | Proposed |
