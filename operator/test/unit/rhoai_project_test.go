// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the rhoai package: namespace -> Data Science Project
// association, default-catalog bootstrap, and the dashboard tile +
// quickstarts (proposal 022 / operator 0.1.4).
package unit_test

import (
	"context"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/rhoai"
)

func rhoaiCorpus(mode accv1alpha1.DeployMode, rhoaiInstalled bool, spec *accv1alpha1.RHOAISpec) *accv1alpha1.AgentCorpus {
	c := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "corpus", Namespace: "acc-proj"},
		Spec: accv1alpha1.AgentCorpusSpec{
			DeployMode: mode,
			Version:    "0.1.0",
			RHOAI:      spec,
		},
	}
	c.Status.Prerequisites.RHOAIInstalled = rhoaiInstalled
	return c
}

func plainNamespace(labels map[string]string, annotations map[string]string) *corev1.Namespace {
	return &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{
		Name: "acc-proj", Labels: labels, Annotations: annotations,
	}}
}

func getNamespace(t *testing.T, c client.Client) *corev1.Namespace {
	t.Helper()
	ns := &corev1.Namespace{}
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acc-proj"}, ns); err != nil {
		t.Fatalf("get namespace: %v", err)
	}
	return ns
}

// rhoai mode + RHOAI installed + nil block (pre-0.1.4 corpora) => labeled.
func TestRHOAIProject_LabelsNamespace_NilBlockEnabled(t *testing.T) {
	c := kserveClient(t, plainNamespace(nil, nil))
	r := &rhoai.ProjectReconciler{Client: c, Reader: c}
	corpus := rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	ns := getNamespace(t, c)
	if ns.Labels[rhoai.LabelDashboard] != rhoai.LabelDashboardValue {
		t.Errorf("expected dashboard label, got labels %v", ns.Labels)
	}
	if !corpus.Status.RHOAIProjectRegistered {
		t.Error("expected RHOAIProjectRegistered=true")
	}
}

// Materialized-but-empty block (webhook path) is also enabled.
func TestRHOAIProject_EmptyBlockEnabled(t *testing.T) {
	c := kserveClient(t, plainNamespace(nil, nil))
	r := &rhoai.ProjectReconciler{Client: c, Reader: c}
	corpus := rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, &accv1alpha1.RHOAISpec{})

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if getNamespace(t, c).Labels[rhoai.LabelDashboard] != rhoai.LabelDashboardValue {
		t.Error("expected dashboard label for empty rhoai block")
	}
}

// standalone / edge modes and RHOAI-not-installed leave the namespace alone.
func TestRHOAIProject_SkipPaths(t *testing.T) {
	for _, tc := range []struct {
		name      string
		mode      accv1alpha1.DeployMode
		installed bool
	}{
		{"standalone", accv1alpha1.DeployModeStandalone, true},
		{"edge", accv1alpha1.DeployModeEdge, true},
		{"rhoai-not-installed", accv1alpha1.DeployModeRHOAI, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := kserveClient(t, plainNamespace(nil, nil))
			r := &rhoai.ProjectReconciler{Client: c, Reader: c}
			corpus := rhoaiCorpus(tc.mode, tc.installed, nil)

			if _, err := r.Reconcile(context.Background(), corpus); err != nil {
				t.Fatalf("Reconcile: %v", err)
			}
			ns := getNamespace(t, c)
			if _, ok := ns.Labels[rhoai.LabelDashboard]; ok {
				t.Errorf("namespace must stay unlabeled in %s", tc.name)
			}
			if corpus.Status.RHOAIProjectRegistered {
				t.Error("status must stay false on skip paths")
			}
		})
	}
}

// Opt-out never labels AND never un-labels (additive-only).
func TestRHOAIProject_OptOutNeverRemoves(t *testing.T) {
	off := &accv1alpha1.RHOAISpec{RegisterNamespaceAsProject: ptr.To(false)}

	// Case 1: unlabeled stays unlabeled.
	c1 := kserveClient(t, plainNamespace(nil, nil))
	r1 := &rhoai.ProjectReconciler{Client: c1, Reader: c1}
	if _, err := r1.Reconcile(context.Background(), rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, off)); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if _, ok := getNamespace(t, c1).Labels[rhoai.LabelDashboard]; ok {
		t.Error("opt-out must not label")
	}

	// Case 2: pre-labeled namespace keeps its label untouched.
	pre := plainNamespace(map[string]string{rhoai.LabelDashboard: rhoai.LabelDashboardValue}, nil)
	c2 := kserveClient(t, pre)
	rv := getNamespace(t, c2).ResourceVersion
	r2 := &rhoai.ProjectReconciler{Client: c2, Reader: c2}
	if _, err := r2.Reconcile(context.Background(), rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, off)); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	ns := getNamespace(t, c2)
	if ns.Labels[rhoai.LabelDashboard] != rhoai.LabelDashboardValue {
		t.Error("opt-out must NEVER remove the label (additive-only)")
	}
	if ns.ResourceVersion != rv {
		t.Error("opt-out must not write to the namespace at all")
	}
}

// Foreign labels and annotations survive the patch.
func TestRHOAIProject_PreservesForeignMetadata(t *testing.T) {
	c := kserveClient(t, plainNamespace(
		map[string]string{"foo": "bar", "kubernetes.io/metadata.name": "acc-proj"},
		map[string]string{"team": "fin"},
	))
	r := &rhoai.ProjectReconciler{Client: c, Reader: c}
	if _, err := r.Reconcile(context.Background(),
		rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	ns := getNamespace(t, c)
	if ns.Labels["foo"] != "bar" || ns.Labels["kubernetes.io/metadata.name"] != "acc-proj" {
		t.Errorf("foreign labels lost: %v", ns.Labels)
	}
	if ns.Annotations["team"] != "fin" {
		t.Errorf("foreign annotations lost: %v", ns.Annotations)
	}
	if ns.Labels[rhoai.LabelDashboard] != rhoai.LabelDashboardValue {
		t.Error("dashboard label missing")
	}
}

// projectDisplayName lands as the display-name annotation; empty sets nothing.
func TestRHOAIProject_DisplayName(t *testing.T) {
	c := kserveClient(t, plainNamespace(nil, nil))
	r := &rhoai.ProjectReconciler{Client: c, Reader: c}
	corpus := rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true,
		&accv1alpha1.RHOAISpec{ProjectDisplayName: "Finance Corpus"})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if got := getNamespace(t, c).Annotations[rhoai.AnnotationDisplayName]; got != "Finance Corpus" {
		t.Errorf("display-name annotation = %q", got)
	}

	c2 := kserveClient(t, plainNamespace(nil, nil))
	r2 := &rhoai.ProjectReconciler{Client: c2, Reader: c2}
	if _, err := r2.Reconcile(context.Background(),
		rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if _, ok := getNamespace(t, c2).Annotations[rhoai.AnnotationDisplayName]; ok {
		t.Error("no display-name configured — annotation must be absent")
	}
}

// Converged state never writes (no hot-loop churn).
func TestRHOAIProject_IdempotentNoPatchWhenConverged(t *testing.T) {
	c := kserveClient(t, plainNamespace(map[string]string{rhoai.LabelDashboard: rhoai.LabelDashboardValue}, nil))
	rv := getNamespace(t, c).ResourceVersion
	r := &rhoai.ProjectReconciler{Client: c, Reader: c}
	corpus := rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)

	for i := 0; i < 2; i++ {
		if _, err := r.Reconcile(context.Background(), corpus); err != nil {
			t.Fatalf("Reconcile pass %d: %v", i, err)
		}
	}
	if got := getNamespace(t, c).ResourceVersion; got != rv {
		t.Errorf("converged namespace was written (RV %s -> %s)", rv, got)
	}
	if !corpus.Status.RHOAIProjectRegistered {
		t.Error("status must report registered when already converged")
	}
}

// ---------------------------------------------------------------------------
// Default-catalog bootstrap
// ---------------------------------------------------------------------------

func TestDefaultCatalog_CreatedWhenAbsent(t *testing.T) {
	c := kserveClient(t)
	r := &rhoai.DefaultCatalogReconciler{Client: c}
	corpus := rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	cat := &accv1alpha1.AccCatalog{}
	if err := c.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-proj", Name: rhoai.DefaultCatalogName}, cat); err != nil {
		t.Fatalf("expected bootstrapped catalog: %v", err)
	}
	if cat.Spec.Tier != "trusted" || cat.Spec.URL == "" || cat.Spec.RequiredSigner.Issuer == "" {
		t.Errorf("bootstrapped catalog incomplete: %+v", cat.Spec)
	}
	if len(cat.OwnerReferences) != 0 {
		t.Error("bootstrapped catalog must NOT be owned by the corpus (it outlives it)")
	}
	if cat.Labels[rhoai.LabelBootstrapped] != "true" {
		t.Error("expected bootstrapped label")
	}
	if !corpus.Status.DefaultCatalogBootstrapped {
		t.Error("expected DefaultCatalogBootstrapped=true")
	}
}

// Any pre-existing catalog (user- or GitOps-managed) suppresses the bootstrap.
func TestDefaultCatalog_NoopWhenAnyCatalogExists(t *testing.T) {
	user := &accv1alpha1.AccCatalog{
		ObjectMeta: metav1.ObjectMeta{Name: "team-catalog", Namespace: "acc-proj"},
		Spec: accv1alpha1.AccCatalogSpec{
			CatalogID: "team-catalog", Tier: "internal", Mode: "https", URL: "https://example.com",
		},
	}
	c := kserveClient(t, user)
	r := &rhoai.DefaultCatalogReconciler{Client: c}
	corpus := rhoaiCorpus(accv1alpha1.DeployModeStandalone, false, nil) // runs in every mode

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	cat := &accv1alpha1.AccCatalog{}
	err := c.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-proj", Name: rhoai.DefaultCatalogName}, cat)
	if err == nil {
		t.Error("must not create acc-canonical when another catalog exists")
	}
	if !corpus.Status.DefaultCatalogBootstrapped {
		t.Error("status reports bootstrapped (catalogs present) — UI signal")
	}
}

func TestDefaultCatalog_OptOut(t *testing.T) {
	c := kserveClient(t)
	r := &rhoai.DefaultCatalogReconciler{Client: c}
	corpus := rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)
	corpus.Spec.BootstrapDefaultCatalog = ptr.To(false)

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	list := &accv1alpha1.AccCatalogList{}
	if err := c.List(context.Background(), list, client.InNamespace("acc-proj")); err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(list.Items) != 0 {
		t.Error("opt-out must create nothing")
	}
}

// ---------------------------------------------------------------------------
// Webhook defaulting
// ---------------------------------------------------------------------------

// The defaulter materializes spec.rhoai in rhoai mode (so the API server
// fills registerNamespaceAsProject=true) and leaves standalone corpora alone.
func TestWebhookDefault_MaterializesRHOAIBlock(t *testing.T) {
	d := &accv1alpha1.AgentCorpusCustomDefaulter{Client: kserveClient(t)}

	rhoaiCorp := rhoaiCorpus(accv1alpha1.DeployModeRHOAI, false, nil)
	if err := d.Default(context.Background(), rhoaiCorp); err != nil {
		t.Fatalf("Default: %v", err)
	}
	if rhoaiCorp.Spec.RHOAI == nil {
		t.Error("expected spec.rhoai materialized in rhoai mode")
	}

	standalone := rhoaiCorpus(accv1alpha1.DeployModeStandalone, false, nil)
	if err := d.Default(context.Background(), standalone); err != nil {
		t.Fatalf("Default: %v", err)
	}
	if standalone.Spec.RHOAI != nil {
		t.Error("spec.rhoai must stay nil in standalone mode")
	}
}

// ---------------------------------------------------------------------------
// Dashboard tile + quickstarts
// ---------------------------------------------------------------------------

type stubChecker struct{ has bool }

func (s stubChecker) HasAPIGroup(string) (bool, error) { return s.has, nil }

var odhAppGVK = schema.GroupVersionKind{Group: "dashboard.opendatahub.io", Version: "v1", Kind: "OdhApplication"}
var odhQSGVK = schema.GroupVersionKind{Group: "console.openshift.io", Version: "v1", Kind: "OdhQuickStart"}

func dashboardClient(t *testing.T) client.Client {
	t.Helper()
	s := newScheme(t)
	for _, gvk := range []schema.GroupVersionKind{odhAppGVK, odhQSGVK} {
		s.AddKnownTypeWithName(gvk, &unstructured.Unstructured{})
		listGVK := gvk
		listGVK.Kind += "List"
		s.AddKnownTypeWithName(listGVK, &unstructured.UnstructuredList{})
	}
	return fake.NewClientBuilder().WithScheme(s).Build()
}

func TestDashboard_NoopWhenDashboardAbsent(t *testing.T) {
	c := dashboardClient(t)
	r := &rhoai.DashboardReconciler{Client: c, Checker: stubChecker{has: false}}
	if _, err := r.Reconcile(context.Background(),
		rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	app := &unstructured.Unstructured{}
	app.SetGroupVersionKind(odhAppGVK)
	if err := c.Get(context.Background(), types.NamespacedName{Name: "agentic-cell-corpus"}, app); err == nil {
		t.Error("tile must not be created when the dashboard group is absent")
	}
}

func TestDashboard_CreatesTileAndQuickStarts(t *testing.T) {
	c := dashboardClient(t)
	r := &rhoai.DashboardReconciler{Client: c, Checker: stubChecker{has: true}}
	if _, err := r.Reconcile(context.Background(),
		rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}

	app := &unstructured.Unstructured{}
	app.SetGroupVersionKind(odhAppGVK)
	if err := c.Get(context.Background(), types.NamespacedName{Name: "agentic-cell-corpus"}, app); err != nil {
		t.Fatalf("expected OdhApplication tile: %v", err)
	}
	spec, _ := app.Object["spec"].(map[string]interface{})
	if spec["provider"] != "Agentic Cell Corpus (Community)" {
		t.Errorf("tile provider = %v", spec["provider"])
	}

	for _, name := range []string{"acc-create-project", "acc-wire-models", "acc-install-packages", "acc-traces"} {
		qs := &unstructured.Unstructured{}
		qs.SetGroupVersionKind(odhQSGVK)
		if err := c.Get(context.Background(), types.NamespacedName{Name: name}, qs); err != nil {
			t.Errorf("expected quickstart %s: %v", name, err)
		}
	}

	// Idempotent: second pass leaves resource versions unchanged.
	rvBefore := app.GetResourceVersion()
	if _, err := r.Reconcile(context.Background(),
		rhoaiCorpus(accv1alpha1.DeployModeRHOAI, true, nil)); err != nil {
		t.Fatalf("second Reconcile: %v", err)
	}
	app2 := &unstructured.Unstructured{}
	app2.SetGroupVersionKind(odhAppGVK)
	if err := c.Get(context.Background(), types.NamespacedName{Name: "agentic-cell-corpus"}, app2); err != nil {
		t.Fatalf("re-get tile: %v", err)
	}
	if app2.GetResourceVersion() != rvBefore {
		t.Error("converged tile was rewritten on second pass")
	}
}
