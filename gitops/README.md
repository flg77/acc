# `gitops/` — ACC operator ops (detached from the code base)

Declarative operations for installing + running the ACC **operator** on the
**blackbox3** OpenShift + RHOAI3 cluster, driven by GitOps. Kept separate from
the application code (`acc/`, `operator/`) so ops can evolve without touching
the runtime — and so this tree can later lift into its own ArgoCD-watched repo.

## Topology

```
   acc1 (ops control plane)                 blackbox3  (88.99.192.92)
   ┌─────────────────────┐                  OpenShift 4.x + RHOAI3
   │ OpenShift GitOps     │   sync (git)     ┌───────────────────────────┐
   │  (ArgoCD)            │ ───────────────▶ │ OpenShift Pipelines (Tekton)│ build → internal registry
   │ Ansible AAP (aap1)   │   kick / verify  │ OLM CatalogSource→Sub→CSV   │ install operator
   └─────────────────────┘                  │ AgentCorpus + AgentCollective│ run agents
                                             └───────────────────────────┘
   internal registry: image-registry.openshift-image-registry.svc:5000
```

## Layout

| Path | What | Tool |
|------|------|------|
| `olm/` | `CatalogSource` + `OperatorGroup` + `Subscription` (the local catalog install). Kustomize base + `overlays/blackbox3` (internal-registry image). | OLM / Kustomize |
| `samples/` | Cat-A WASM ConfigMap + `AgentCorpus` (rhoai) + `AgentCollective` for the first bring-up. | Kustomize |
| `tekton/` | `Pipeline` + `Task`s: build operator image → bundle → opm index, push to the internal registry. | OpenShift Pipelines |
| `argocd/` | `Application`s (app-of-apps): sync `olm/` + `samples/` to blackbox3. | OpenShift GitOps |
| `ansible/` | AAP playbooks / job-template definitions: kick the pipeline, wait for CSV, apply CR, smoke-check (token-cheap repeatable ops). | Ansible AAP (aap1) |

## Flow (see `docs/rhoai-bringup-blackbox3.md` for the full runbook)

1. **Build** — Tekton builds operator + bundle + index images to the internal
   registry (`make catalog-build` wrapped as a Task).
2. **Catalog** — ArgoCD syncs `olm/overlays/blackbox3` → `CatalogSource`
   (pointing at the index) appears in OperatorHub → `Subscription` → CSV.
3. **Run** — ArgoCD syncs `samples/` → `AgentCorpus`/`AgentCollective` →
   operator reconciles NATS/Redis/agents.
4. **Verify** — Ansible smoke playbook checks CSV `Succeeded` + corpus `Ready`.

> Images, registry paths, repo URL, and the blackbox3 cluster name are
> parameterised in the overlay + ArgoCD `Application`s — adjust to your env.
> Nothing here is applied automatically; a human/ArgoCD/AAP triggers it.
