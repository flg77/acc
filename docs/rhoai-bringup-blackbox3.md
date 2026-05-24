# RHOAI bring-up runbook — ACC operator on blackbox3

Install the ACC operator into the **local operator catalog** of the
**blackbox3** OpenShift + RHOAI3 cluster, via GitOps, then run a first
AgentCorpus. All ops artifacts live in `gitops/` (detached from the code).

> Reuses the generic recipe in `docs/operator-install-local.md` (Method C) and
> `docs/howto-rhoai.md`; this runbook pins it to our hosts + internal registry.

## Hosts

| Host | ssh alias | Role |
|------|-----------|------|
| OpenShift + RHOAI3 cluster | `blackbox3` (88.99.192.92) | deploy target; **internal registry** |
| Ops control plane | `acc1` | OpenShift GitOps (ArgoCD) + dev repo |
| Automation | `aap1` (10.199.12.20) | Ansible Automation Platform (job templates) |

## Prerequisites on blackbox3

- OpenShift 4.14+ with OLM, RHOAI3, internal image registry exposed.
- Operators: **OpenShift GitOps** (ArgoCD) and **OpenShift Pipelines** (Tekton).
- `cluster-admin` for CRD + ClusterRole install.
- Optional (operator degrades gracefully if absent): KServe/RHOAI serving,
  Milvus, OPA Gatekeeper, KEDA, Prometheus Operator. The lab AgentCorpus
  (`gitops/samples`) ships a minimal profile (1× NATS/Redis, log observability,
  Gatekeeper/Kafka/OTel off).
- **Cat-A WASM ConfigMap** must exist before the AgentCorpus
  (`gitops/samples/base/cat-a-wasm-configmap.yaml` ships a lab placeholder).

## Flow

```
acc1 ArgoCD ── git ──▶ blackbox3
   │                     ├─ Tekton: build operator img → bundle → opm index → internal registry
   │  app-of-apps        ├─ OLM: CatalogSource → OperatorGroup → Subscription → CSV Succeeded
   └─ aap1 kicks/verifies└─ AgentCorpus + AgentCollective → NATS/Redis/agents
```

## 1 — Build images (Tekton → internal registry)

```bash
# one-time (blackbox3): SA + image-builder rolebinding + pipeline
oc new-project acc-operator-system
oc create sa pipeline -n acc-operator-system
oc policy add-role-to-user system:image-builder \
  system:serviceaccount:acc-operator-system:pipeline -n acc-operator
oc apply -f gitops/tekton/pipeline.yaml

# run the build (or let AAP do it — step 4)
oc create -f gitops/tekton/pipelinerun.yaml -n acc-operator-system
tkn pipelinerun logs -f -n acc-operator-system
```

Produces in `image-registry.openshift-image-registry.svc:5000/acc-operator/`:
`acc-operator:0.1.0`, `acc-operator-bundle:0.1.0`, `acc-operator-index:0.1.0`.
(Local equivalent: `make -C operator docker-build docker-push bundle-build
bundle-push catalog-build catalog-push CATALOG_IMG=…/acc-operator-index:0.1.0`.)

## 2 — Install the operator from the local catalog (ArgoCD)

```bash
# on acc1's ArgoCD: register blackbox3 as a cluster named "blackbox3"
argocd cluster add <blackbox3-context> --name blackbox3   # or a cluster Secret

# app-of-apps pulls in the OLM + corpus child apps
oc apply -f gitops/argocd/app-of-apps.yaml          # in openshift-gitops
# acc-operator-olm syncs gitops/olm/overlays/blackbox3 → CatalogSource/Sub
```

Verify (reuses `docs/operator-install-local.md` §10):

```bash
oc get catalogsource acc-operator-catalog -n openshift-marketplace
oc get csv -n acc-operator-system            # acc-operator.v0.1.0 → Succeeded
oc get pods -n acc-operator-system           # controller-manager Running
```

## 3 — Deploy the first AgentCorpus

```bash
# the corpus app is manual-sync; ensure the Cat-A WASM CM + CRDs exist first
oc get crd agentcorpora.acc.redhat.io agentcollectives.acc.redhat.io
argocd app sync acc-corpus                   # or: oc apply -k gitops/samples/base
oc get agentcorpus,agentcollective -n acc-system
oc get pods -n acc-system                    # nats / redis / ingester / analyst / arbiter
```

## 4 — Or drive it all from AAP (aap1, token-cheap)

Wire `gitops/ansible/bringup.yml` + `smoke.yml` as Job Templates (see
`gitops/ansible/README.md`): `bring up operator` → `smoke check`. Both
idempotent.

## Teardown

```bash
argocd app delete acc-corpus acc-operator-olm
oc delete -k gitops/olm/overlays/blackbox3
oc delete csv -n acc-operator-system -l operators.coreos.com/acc-operator.acc-operator-system
# CRDs are destructive (delete all corpora/collectives):
oc delete crd agentcorpora.acc.redhat.io agentcollectives.acc.redhat.io
```

## Known gaps (see `docs/operator-standalone-parity.md`)

- The lab corpus uses one collective-level LLM. Per-agent models, memory
  reflection, and the prompt-cache toggle are set today via
  `AgentRoleSpec.extraEnv` (see the agentset guide); clean CRD fields are a
  tracked follow-up.
- First bring-up assumes you supply a reachable LLM (existing KServe
  InferenceService or an OpenAI-compatible URL) + a Milvus URI.
