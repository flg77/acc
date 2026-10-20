---
type: backlog-item
area: operator-release
id: OR-05
status: done
size: M
depends: [OR-02]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-05 — One bundle for Kubernetes and OpenShift

**What.** The bundle stops shipping `acc-console-plugin.consoleplugin.yaml`, `acc-console-plugin.service.yaml` and the second CSV deployment. A reconciler (`internal/reconcilers/ui/consoleplugin.go`) creates the ConsolePlugin, its Deployment and Service when `console.openshift.io/v1` is discovered — the pattern `consolelink.go` already uses — and does nothing elsewhere. RBAC for `consoleplugins`. The `console.openshift.io/plugins` CSV annotation stays.

**Why.** On vanilla Kubernetes OLM fails the InstallPlan on the unknown `ConsolePlugin` kind and the service-ca annotation mints nothing. We target both catalogs with one package.

**Done when.** `operator-sdk run bundle` succeeds on kind + OLM with no ConsolePlugin created, and on CRC with the plugin served and enabled.

**2026-10-04 — done** (acc-spearhead-operator, branch `or-05-one-bundle`, stacked on OR-03). `internal/consoleplugin/` renders Deployment + Service + ConsolePlugin; a leader-only runnable applies them on `console.openshift.io/v1` discovery (start-up, then every 10 min) and does nothing elsewhere. The ConsolePlugin's service namespace follows the operator (no more hard-coded `acc-system`). Inputs on the manager: `OPERATOR_NAMESPACE`, `RELATED_IMAGE_CONSOLE_PLUGIN` (empty disables), `ACC_CONSOLE_PLUGIN_PULL_SECRETS` (until OR-06). The bundle is CRDs + CSV; `make bundle` stays idempotent. **Open:** the cluster verification (kind + OLM, CRC) — OR-07 brings kind to CI; the OpenShift half needs CRC or a lab cluster.
