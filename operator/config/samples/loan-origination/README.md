# Loan-Origination — edge↔DC operator sample CRs

A two-cell deployment of the loan-origination flagship: a regulated mortgage
desk split across the **edge** (branch office) and the **data center**, exercising
ACC's bidirectional cross-collective bridge, OpenShell caging, and MLflow tracing
on a regulated workload. These are the operator-path counterpart to the
standalone/Podman preset `collectives/collective.loan-origination.yaml`.

> Group is **`acc.redhat.io/v1alpha1`**. (`docs/howto-edge.md` still shows the stale
> `acc.redhat-ai-dev.io` group — ignore that.) This is also the first **edge**
> AgentCorpus sample in the tree.

## Topology

```
  EDGE  (02-edge.yaml)                          DC  (01-dc.yaml, the HUB)
  ns loan-origination-edge                      ns loan-origination-dc
  AgentCorpus loan-edge  (deployMode: edge)     AgentCorpus loan-dc  (deployMode: rhoai)
    • loan_intake                                 • underwriter          (OpenShell-caged)
    • loan_officer ── file complete ──┐           • compliance_reviewer  (demographic scope)
    • assistant (auto)                │           • orchestrator, reviewer, assistant (control)
  NATS leaf ──► DC hub :7422          │           NATS hub · Milvus · vLLM/KServe
                                      │
     acc.bridge.loan-edge.loan-dc.delegate ──► underwriter decides (DECISION_RENDER=CRITICAL→oversight)
     acc.bridge.loan-dc.loan-edge.result   ◄── decision returns to the originating edge cell
     acc.loan-edge.role_update             ◄── compliance_reviewer Cat-C push-down (hot-reload)
   offline: acc.bridge.loan-edge.pending (JetStream buffer; drains on leaf reconnect)
```

**The one string that wires the bridge:** `edge.hubCollectiveId` (edge corpus) **must
equal** `AgentCollective.spec.collectiveId` on the DC — both `loan-dc`. Setting it makes
the operator inject `ACC_BRIDGE_ENABLED=true` + `ACC_HUB_COLLECTIVE_ID` into the edge agents.

## Apply order

```bash
# 1) DC first — it is the hub the edge leaf connects into.
kubectl apply -f 01-dc.yaml
kubectl -n loan-origination-dc get agentcorpus loan-dc -w    # wait: phase Ready

# 2) Edge second.
kubectl apply -f 02-edge.yaml
kubectl -n loan-origination-edge get agentcorpus loan-edge -w
# verify the leaf came up:
kubectl -n loan-origination-edge get agentcorpus loan-edge \
  -o jsonpath='{.status.infrastructure.natsLeafConnected}{"\n"}'
```

## ⚠ Known gap — the operator does not open a hub leaf listener

The operator's `nats_config.go` renders a `leafnodes { remotes: [...] }` block **only for
the edge** (gated on `spec.edge.hubNatsUrl`). It does **not** emit a hub-side
`leafnodes { listen: ":7422" }` on the DC corpus, so the DC NATS **does not accept the
edge leaf out of the box**. Until that's addressed, the bridge needs one of:

- **(a) operator enhancement** — render `leafnodes { listen: ":7422" }` when a corpus is a
  delegation hub (e.g. a new `spec.hub.leafListener` toggle, or auto-on when another corpus
  targets it). Preferred, and the real fix.
- **(b) manual/GitOps overlay** — add a leaf-listener to the DC NATS config for the demo.
  ⚠ the operator manages that ConfigMap, so an overlay must survive reconciliation (a
  separate NATS, or a patched template) — not a plain `kubectl edit`.

## Dependencies (must exist before either corpus reaches `Ready`)

1. **`acc-cat-a-wasm` ConfigMap** in **both** namespaces — the files ship a **placeholder**
   (`binaryData: {}`); replace it with the real compiled `category_a.wasm`. This is the same
   artifact the acc-e2e wasm-seed pins, currently a **known fleet blocker**
   (`acc-cat-a-wasm-edge` absent from quay → KubeJobFailed on acc1). **Resolve first.**
2. **DC:** a DataScienceCluster (rhoai auto-detect), **Milvus** reachable at the URI in
   `01-dc.yaml`, and a KServe **InferenceService** `loan-dc-llm` (`deploy:false`) — or switch
   the DC `llm` to the commented **Anthropic** block for a self-contained lab.
3. **OpenShell gateway** reachable from the DC namespace (`sandbox.gatewayURL`).
4. **MLflow** tracking server reachable (`mlflowTrackingUri`), with CORS allowing the
   run-logging POSTs.
5. **NATS leaf reachability** edge→DC on `:7422` — plus the hub-listener gap above.

## Definition of done

- Both corpora `status.phase: Ready`; edge `status.infrastructure.natsLeafConnected: true`.
- Both `AccPackageInstall` = `Installed` (pack cosign-verified from `acc-canonical`).
- A completed file flows **edge `loan_officer` → `acc.bridge.loan-edge.loan-dc.delegate` →
  DC `underwriter` → `acc.bridge.loan-dc.loan-edge.result`** back to the edge.
- The underwriter's tool exec is **caged** (OpenShell kernel-denial smoke fails-closed).
- `DECISION_RENDER` routes to the **oversight queue**; a compliance Cat-C proposal
  hot-reloads to the edge on `acc.loan-edge.role_update`.
- An underwriting golden-suite run **logs to MLflow** (`loan-origination` experiment).

## Notes / limitations

- **Per-agent model isn't on the CRD.** `AgentCollective.spec.llm` is one backend per
  collective, so all DC agents share the DC model (the preset's per-agent haiku/sonnet split
  is a runtime `models.yaml` feature). The coarse edge-vs-DC model split still falls out
  because the cells are separate collectives.
- **Roster source.** The operator roster is `AgentCollective.spec.agents[]` + an
  `AccPackageInstall` — **not** the runtime preset `collective.loan-origination.yaml` (that
  drives the standalone/Podman path only).
- **`loan_risk` / `loan_predictive` MCPs** (the underwriter's tools) aren't authored yet;
  the underwriter runs the five rule-based factors with `predictive.available:false` until
  they land as `spec.mcpServers[]` or a pack-bundled MCP.
- **Demographic isolation** (compliance_reviewer's exclusive scope) is a role + Cat-B
  boundary from the pack's `role.yaml`, not an operator field.
