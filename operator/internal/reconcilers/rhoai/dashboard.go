// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package rhoai

import (
	"context"
	"fmt"
	"reflect"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

var (
	odhApplicationGVK = schema.GroupVersionKind{
		Group: "dashboard.opendatahub.io", Version: "v1", Kind: "OdhApplication",
	}
	odhQuickStartGVK = schema.GroupVersionKind{
		Group: "console.openshift.io", Version: "v1", Kind: "OdhQuickStart",
	}
)

// GroupChecker answers whether an API group is registered in the cluster.
// util.APIGroupChecker satisfies it; tests stub it.
type GroupChecker interface {
	HasAPIGroup(group string) (bool, error)
}

// DefaultDashboardNamespace is where RHOAI's dashboard reads its
// (namespaced!) OdhApplication and OdhQuickStart CRs.
const DefaultDashboardNamespace = "redhat-ods-applications"

// DashboardReconciler surfaces ACC inside the RHOAI dashboard: one
// OdhApplication tile (Applications -> Explore) and the quickstart guides
// (Learning resources) that walk a user from "create an ACC project" to a
// package-infused, model-wired agentset. The CRDs are NAMESPACED — the
// objects land in the dashboard's namespace (default
// redhat-ods-applications, override via spec.rhoai.dashboardNamespace) as
// operator-owned singletons keyed by name, independent of any one corpus.
// The whole reconciler is a silent no-op when the RHOAI dashboard CRDs are
// not installed (version drift across RHOAI releases included).
type DashboardReconciler struct {
	Client  client.Client
	Checker GroupChecker
	// Reader is an uncached reader for the singleton Gets so the manager
	// never starts informers on the dashboard CRDs (which would also
	// require a watch RBAC verb). Falls back to Client when nil.
	Reader client.Reader
}

// Name implements SubReconciler.
func (r *DashboardReconciler) Name() string { return "rhoai/dashboard" }

// Reconcile implements SubReconciler.
func (r *DashboardReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	if !corpus.Status.Prerequisites.RHOAIInstalled {
		return reconcilers.SubResult{}, nil
	}
	if r.Checker == nil {
		return reconcilers.SubResult{}, nil
	}
	ok, err := r.Checker.HasAPIGroup(util.APIGroupOdhDashboard)
	if err != nil || !ok {
		return reconcilers.SubResult{}, nil // dashboard absent or discovery flaky — never block
	}

	ns := DefaultDashboardNamespace
	if corpus.Spec.RHOAI != nil && corpus.Spec.RHOAI.DashboardNamespace != "" {
		ns = corpus.Spec.RHOAI.DashboardNamespace
	}

	log := logf.FromContext(ctx)
	for _, obj := range dashboardObjects(ns) {
		if err := r.upsert(ctx, obj); err != nil {
			if meta.IsNoMatchError(err) || apierrors.IsNotFound(err) {
				// CRD kind (or the dashboard namespace) not present on this
				// RHOAI version — skip quietly.
				log.V(1).Info("RHOAI dashboard kind unavailable; skipping",
					"kind", obj.GetKind(), "name", obj.GetName())
				continue
			}
			return reconcilers.SubResult{}, fmt.Errorf("upsert %s/%s: %w", obj.GetKind(), obj.GetName(), err)
		}
	}
	return reconcilers.SubResult{}, nil
}

// upsert creates the object or updates only its spec when drifted.
func (r *DashboardReconciler) upsert(ctx context.Context, desired *unstructured.Unstructured) error {
	reader := r.Reader
	if reader == nil {
		reader = r.Client
	}
	existing := &unstructured.Unstructured{}
	existing.SetGroupVersionKind(desired.GroupVersionKind())
	err := reader.Get(ctx, types.NamespacedName{Namespace: desired.GetNamespace(), Name: desired.GetName()}, existing)
	if apierrors.IsNotFound(err) {
		return r.Client.Create(ctx, desired)
	}
	if err != nil {
		return err
	}
	desiredSpec := desired.Object["spec"]
	existingSpec := existing.Object["spec"]
	if reflect.DeepEqual(desiredSpec, existingSpec) {
		return nil
	}
	existing.Object["spec"] = desiredSpec
	return r.Client.Update(ctx, existing)
}

// dashboardObjects returns the tile + quickstarts, namespaced into the
// dashboard's namespace.
func dashboardObjects(ns string) []*unstructured.Unstructured {
	objs := []*unstructured.Unstructured{tile()}
	for _, qs := range quickStarts() {
		objs = append(objs, qs)
	}
	for _, o := range objs {
		o.SetNamespace(ns)
	}
	return objs
}

func tile() *unstructured.Unstructured {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(odhApplicationGVK)
	u.SetName("agentic-cell-corpus")
	u.SetLabels(map[string]string{LabelBootstrapped: "true"})
	u.Object["spec"] = map[string]interface{}{
		"displayName": "Agentic Cell Corpus (Community)",
		"provider":    "Agentic Cell Corpus (Community)",
		"category":    "AI/Machine Learning",
		"support":     "community",
		"description": "Biologically-inspired multi-agent governance framework. " +
			"Community project — NOT provided, supported, certified, or endorsed by Red Hat.",
		"docsLink": "https://github.com/flg77/acc",
		"getStartedLink": "https://github.com/flg77/acc/blob/main/operator/docs/rhoai-e2e-agentset-runbook.md",
		"getStartedMarkDown": "Install the ACC operator from the private catalog, then follow the " +
			"\"Create an ACC project\" quick start: every AgentCorpus namespace automatically becomes " +
			"a Data Science Project, ships a default signed package catalog, and wires to the models " +
			"served in your RHOAI projects.",
		"img":      "", // logo rides separately (pending PNG)
		"kfdefApplications": []interface{}{},
		"quickStart":        "acc-create-project",
	}
	return u
}

func quickStarts() []*unstructured.Unstructured {
	mk := func(name, displayName string, minutes int64, description, intro string, tasks []map[string]interface{}, conclusion string) *unstructured.Unstructured {
		u := &unstructured.Unstructured{}
		u.SetGroupVersionKind(odhQuickStartGVK)
		u.SetName(name)
		u.SetLabels(map[string]string{LabelBootstrapped: "true"})
		taskList := make([]interface{}, 0, len(tasks))
		for _, t := range tasks {
			taskList = append(taskList, map[string]interface{}(t))
		}
		u.Object["spec"] = map[string]interface{}{
			"displayName":     displayName,
			"appName":         "agentic-cell-corpus",
			"durationMinutes": minutes,
			"description":     description,
			"introduction":    intro,
			"tasks":           taskList,
			"conclusion":      conclusion,
		}
		return u
	}

	return []*unstructured.Unstructured{
		mk("acc-create-project", "ACC: Create an ACC project + corpus", 10,
			"Create a namespace, instantiate an AgentCorpus with all defaults, and see it appear as a Data Science Project.",
			"An AgentCorpus lives in its own namespace. On creation the operator registers that namespace as an RHOAI Data Science Project and bootstraps the default signed package catalog — everything RHOAI offers wires into the same project.",
			[]map[string]interface{}{
				{"title": "Create the project namespace", "description": "Run `oc new-project my-acc` (or use the console Projects page). Any namespace works — the operator is installed cluster-wide."},
				{"title": "Create the AgentCorpus", "description": "Operators → Installed Operators → Agentic Cell Corpus → Agent Corpus → Create. The form is prefilled (RHOAI deploy mode, image repository, governance defaults). Click Create."},
				{"title": "Create the AgentCollective", "description": "Same operator page → Agent Collective → Create. The prefilled example ships an assistant-led six-role agentset."},
				{"title": "Verify the project", "description": "Open the RHOAI dashboard → Data Science Projects: your namespace is listed. `oc get acccatalog -n my-acc` shows the bootstrapped acc-canonical catalog."},
			},
			"Your corpus runs in a dedicated project with the default catalog available. Continue with the model-wiring quick start."),
		mk("acc-wire-models", "ACC: Map collectives to RHOAI models", 10,
			"Point a collective at a model served in any RHOAI project (cross-namespace).",
			"Each AgentCollective binds one LLM. Models served from other Data Science Projects are consumed cross-namespace — the operator resolves the endpoint and injects it into every agent pod.",
			[]map[string]interface{}{
				{"title": "Find a served model", "description": "RHOAI dashboard → AI hub → Deployments (or `oc get inferenceservice -A`). Note its name and project namespace."},
				{"title": "Reference it from the collective", "description": "In the AgentCollective form set llm.vllm: inferenceServiceRef = the model name, inferenceServiceNamespace = its project, deploy = false."},
				{"title": "Verify the wiring", "description": "`oc -n my-acc get deploy <collective>-observer -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name==\"ACC_VLLM_INFERENCE_URL\")].value}'` prints the resolved endpoint. If traffic is blocked, allow ingress from your project to the model's predictor Service."},
			},
			"Different collectives can map different models from different projects — switch by editing the collective."),
		mk("acc-install-packages", "ACC: Install role packages into the active catalog", 10,
			"Browse the ecosystem catalogs and infuse signed role packages into your corpus.",
			"Packages (roles, skills, MCPs) come from layered AccCatalogs. The bootstrapped acc-canonical catalog serves the published ecosystem at flg77.github.io/acc-ecosystem; signatures are verified against the catalog's signing floor.",
			[]map[string]interface{}{
				{"title": "Inspect the active catalog", "description": "`oc get acccatalog -n my-acc -o yaml` — the bootstrapped acc-canonical entry points at the ecosystem repo catalog."},
				{"title": "Install a package", "description": "Installed Operators → ACC Package Install → Create. The prefilled example installs @acc/workspace-roles at the latest published version. Leave the constraint empty for latest."},
				{"title": "Watch the infusion", "description": "`oc get accpackageinstall -n my-acc -w` until phase Installed; the resolved version and content hash land in status."},
				{"title": "Add more catalogs", "description": "Create additional AccCatalog resources (e.g. your team's own index) — higher priority wins when packages overlap."},
			},
			"Package additions land in the active catalog layers. A personalized-catalog marketplace is on the roadmap."),
		mk("acc-traces", "ACC: See agent traces", 5,
			"Wire the corpus to an OTLP backend and explore agent activity.",
			"The corpus ships an OpenTelemetry collector. Give it a backend (a PVC-backed TempoMonolithic works on any cluster) and traces appear per agent task.",
			[]map[string]interface{}{
				{"title": "Install a trace backend", "description": "Install the Tempo operator and create a TempoMonolithic (no object storage needed). Note its OTLP gRPC service, e.g. tempo-acc-tempo.acc-observability.svc.cluster.local:4317."},
				{"title": "Point the corpus at it", "description": "In the AgentCorpus form set observability.backend = otel and otelCollector.endpoint = the Tempo service (tlsInsecure: true for in-cluster plaintext)."},
				{"title": "Explore traces", "description": "Open the Tempo/Jaeger route and search service acc-agent after prompting the agentset."},
			},
			"Traces correlate per task across agents. MLflow fan-out is available via otelCollector.mlflowEndpoint."),
	}
}
