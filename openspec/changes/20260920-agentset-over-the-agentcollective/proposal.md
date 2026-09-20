# 20260920-agentset-over-the-agentcollective — proposal

## Why

`20260920-surfaces-detect-environment` Phase 1 (v0.18.0) made the surfaces say
where they run and stop offering what cannot work there. In a cluster the
TUI's *Ecosystem → Agentset* tab now explains that the agentset is an
`AgentCollective` and prints the two `oc` commands that show it — it still
shows **nothing of it**. The operator's requirement is the opposite of "go and
look elsewhere": *"they connect the running environment."*

What a pod can see today (bb3 `wksp-user2`, operator 0.2.26, runtime 0.18.1):

* The TUI and WebGUI pods run as the namespace's `default` ServiceAccount, which
  may read **no** ACC object (`oc auth can-i get agentcollectives` → no; the same
  for `agentcorpora`, `accpackageinstalls`).
* The `kubernetes` Python client is not in the UI images (it is an optional
  dependency used only by `acc/role_crd_loader.py`).
* The observed half is already there: every agent's role, state and resolved
  model arrive on the bus (`CollectiveSnapshot`), which is what the tab's *live*
  column counts today.

Also found while reading the operator for this: the defaulting webhook that
gives every `AgentCollective` an `assistant` (`agentcollective_webhook.go`,
proposal 023 §4b) is **not registered by the OLM bundle** — the CSV carries
`magentcorpus` / `vagentcorpus` only — so on an OLM install no collective gets
one. The operator asked exactly that ("no assistant … but it always should").

## What changes

### Phase 1 (this ship) — see the cluster, read-only

* **`acc/deployment.py`** — the seam KT-00 §4 describes, as small as the read
  path needs: `agentset()` returns one `Agentset` (where it is declared, its
  agents: role × replicas × model, the packages the roles come from with their
  install phase, the corpus version) from the backend the environment selects.
  `CheckoutBackend` reads `collective.yaml` (today's code, moved behind it);
  `ClusterBackend` reads the `AgentCollective`, `AgentCorpus` and
  `AccPackageInstall` objects of its namespace over the Kubernetes API with the
  pod's ServiceAccount token — **standard library only** (`urllib` + `ssl`
  against the mounted CA), no new dependency. A refusal is reported as what it
  is (*"this pod's ServiceAccount may not read agentcollectives — the operator
  grants that from 0.2.27"*), never as an empty table.
* **TUI → Ecosystem → Agentset in a cluster**: the table is the collective —
  role, declared replicas, the model the CR declares **and** the model the
  agents resolved (from the bus; they differ on bb3, where the CRD's `llm` is an
  inert `ollama` stand-in), live count, and a state that says *awaiting pack*
  while an agent is up but its package is not installed. Below it: where it is
  declared, the corpus version, each package with constraint → installed
  version and phase. The editor stays read-only; no control is added.
* **TUI → Configuration → LLM Endpoints leads with LIVE BACKENDS** (asked for
  twice): what runs first, the configured-backend summary and the form after.
* **Operator 0.2.27**: a `<corpus>-ui` ServiceAccount for the TUI and WebGUI
  Deployments with a namespaced, **read-only** Role on
  `agentcorpora`, `agentcollectives`, `accpackageinstalls`, `acccatalogs`
  (`get`, `list`, `watch`) — no core resources, no secrets, no write verb. The
  three objects are owned by the corpus. For this the operator's own
  ClusterRole gains `serviceaccounts` and `roles` / `rolebindings`; Kubernetes
  lets it grant only what it holds itself.

### Phases 2–N (deferred)

* **The `AgentCollective` webhooks.** `cmd/main.go` sets up the `AgentCorpus`
  webhook only and the CSV lists only `magentcorpus` / `vagentcorpus`, so the
  default-`assistant` injection has never run on an OLM install. Turning it on
  is NOT part of this change: a validating webhook that has never seen the live
  objects can refuse them, and the injected assistant would start on the
  collective's `llm` block — on a deployment that wires its model through
  `extraEnv` that is the inert stand-in. It needs the operator's word and the
  rollout template carrying the LLM wiring first.

* The same screen in the WebGUI (`GET /api/agentset`, KW-03) on this backend.
* Changes (add / remove an agent, replicas, model) as proposal → approval →
  apply — needs the operator's answers on who owns the CR (KT-00 §6 Q1, Q2).
* Packages and catalogs as `AccPackageInstall` / `AccCatalog` (KT-06, KT-07).

## Impact

* **Affected code:** `acc/deployment.py` (new), `acc/tui/screens/ecosystem.py`,
  `acc/tui/screens/configuration.py`, `operator/internal/reconcilers/ui/`,
  `operator/config/rbac/role.yaml`, the CSV's `clusterPermissions`.
* **New env knobs:** `ACC_KUBERNETES_API` — an explicit API base URL; without it
  `KUBERNETES_SERVICE_HOST` / `_PORT` are used. (`ACC_SERVICEACCOUNT_DIR` is the
  one `acc.identity` already reads.) The tests point both at a local fake.
* **Tests:** the cluster backend against a local fake API server (objects,
  403, unreachable, malformed); pilot tests for the tab in both environments;
  the LLM tab order; Go unit tests for the ServiceAccount, Role, RoleBinding and
  the Deployments' `serviceAccountName`.
* **Backward compatibility:** standalone is unchanged (the checkout backend is
  today's `load_collective`). With an operator older than 0.2.27 the tab shows
  the Phase-1 explanation plus the exact permission that is missing.

## What stays open after Phase 1

* Nothing can be changed from either surface yet — by design of this phase.
* `status.readyAgents` / `phase` on the `AgentCollective` are wrong today
  (G-17); the tab uses the bus for the observed half and does not show them.
* An `assistant` injected by the webhook still needs the collective's LLM
  wiring (`extraEnv` on bb3) — the rollout template has to carry it.
