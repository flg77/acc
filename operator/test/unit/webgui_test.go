// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the acc-webgui interaction-plane reconciler (proposal 023 / 025):
// Keycloak-OIDC oauth2-proxy sidecar + the ADR-025 §5 fail-safe (never expose
// an unauthenticated surface).
package unit_test

import (
	"context"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/ui"
)

func webguiClient(t *testing.T, objs ...client.Object) (client.Client, func() client.Client) {
	t.Helper()
	s := newScheme(t)
	if err := appsv1.AddToScheme(s); err != nil {
		t.Fatalf("appsv1.AddToScheme: %v", err)
	}
	c := fake.NewClientBuilder().WithScheme(s).WithObjects(objs...).Build()
	return c, func() client.Client { return c }
}

func webguiCorpus(webgui *accv1alpha1.WebGUISpec) *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "rhoai-corpus", Namespace: "acc-system"},
		Spec: accv1alpha1.AgentCorpusSpec{
			Version:         "0.2.0",
			ImageRepository: "quay.io/flg77/acc_images",
			WebGUI:          webgui,
		},
	}
}

func fullKeycloak() *accv1alpha1.WebGUIKeycloakSpec {
	return &accv1alpha1.WebGUIKeycloakSpec{
		IssuerURL:        "https://kc.example.com/realms/acc",
		ClientID:         "acc-webgui",
		ClientSecretName: "acc-webgui-keycloak",
	}
}

// nil spec.webgui → strict no-op.
func TestWebGUI_NilSpecNoop(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := webguiCorpus(nil)
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.WebGUIDeployed {
		t.Error("nil webgui should leave WebGUIDeployed=false")
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-webgui"}, deploy); err == nil {
		t.Error("nil webgui should not create a Deployment")
	}
}

// Enabled but Keycloak incomplete → fail-safe: no deploy (ADR 025 §5).
func TestWebGUI_EnabledWithoutKeycloakIsBlocked(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := webguiCorpus(&accv1alpha1.WebGUISpec{Enabled: ptr.To(true)}) // no Keycloak
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.WebGUIDeployed {
		t.Error("enabled-without-keycloak must NOT deploy (fail-safe)")
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-webgui"}, deploy); err == nil {
		t.Error("must not create a Deployment without Keycloak config")
	}
}

// Disabled → no-op even with Keycloak set.
func TestWebGUI_DisabledNoop(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := webguiCorpus(&accv1alpha1.WebGUISpec{Enabled: ptr.To(false), Keycloak: fullKeycloak()})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.WebGUIDeployed {
		t.Error("disabled webgui must not deploy")
	}
}

// Happy path: Deployment (webgui + oauth2-proxy) + Service + status.
func TestWebGUI_DeploysWithKeycloak(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := webguiCorpus(&accv1alpha1.WebGUISpec{
		Enabled:  ptr.To(true),
		Route:    ptr.To(false), // route path exercised on the live cluster
		Keycloak: fullKeycloak(),
		GroupMappings: map[string]string{
			"publisher": "acc-publishers",
			"operator":  "acc-operators",
		},
	})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !corpus.Status.WebGUIDeployed {
		t.Fatal("expected WebGUIDeployed=true")
	}

	// Service exists on the proxy port.
	svc := &corev1.Service{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-webgui"}, svc); err != nil {
		t.Fatalf("expected webgui Service: %v", err)
	}
	if svc.Spec.Ports[0].Port != 4180 {
		t.Errorf("Service should front the proxy port 4180, got %d", svc.Spec.Ports[0].Port)
	}

	// Deployment: two containers — webgui (loopback) + oauth2-proxy.
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-webgui"}, deploy); err != nil {
		t.Fatalf("expected webgui Deployment: %v", err)
	}
	ctrs := map[string]corev1.Container{}
	for _, ct := range deploy.Spec.Template.Spec.Containers {
		ctrs[ct.Name] = ct
	}
	webgui, okW := ctrs["webgui"]
	proxy, okP := ctrs["oauth2-proxy"]
	if !okW || !okP {
		t.Fatalf("expected webgui + oauth2-proxy containers, got %v", deploy.Spec.Template.Spec.Containers)
	}

	env := map[string]string{}
	for _, e := range webgui.Env {
		env[e.Name] = e.Value
	}
	if env["ACC_WEBGUI_HOST"] != "127.0.0.1" {
		t.Error("webgui must bind loopback (proxy is sole ingress)")
	}
	if env["ACC_WEBGUI_AUTH_MODE"] != "oauth-proxy" {
		t.Errorf("AUTH_MODE = %q, want oauth-proxy", env["ACC_WEBGUI_AUTH_MODE"])
	}
	// Group mappings rendered deterministically (operator before publisher).
	if env["ACC_WEBGUI_GROUP_MAPPINGS"] != "operator=acc-operators;publisher=acc-publishers" {
		t.Errorf("GROUP_MAPPINGS = %q (want sorted operator;publisher)", env["ACC_WEBGUI_GROUP_MAPPINGS"])
	}
	// Crash fix — the webgui must be told the corpus NATS, else its
	// FastAPI lifespan NATSObserver falls back to a default that can't
	// resolve and crash-loops at startup.
	if env["ACC_NATS_URL"] != "nats://rhoai-corpus-nats:4222" {
		t.Errorf("ACC_NATS_URL = %q, want the corpus NATS service", env["ACC_NATS_URL"])
	}
	if env["ACC_CORPUS_NAME"] != "rhoai-corpus" {
		t.Errorf("ACC_CORPUS_NAME = %q, want corpus name", env["ACC_CORPUS_NAME"])
	}

	// oauth2-proxy is wired to the Keycloak realm + the client secret.
	argstr := strings.Join(proxy.Args, " ")
	if !strings.Contains(argstr, "--oidc-issuer-url=https://kc.example.com/realms/acc") {
		t.Errorf("proxy missing keycloak issuer: %v", proxy.Args)
	}
	if !strings.Contains(argstr, "--client-id=acc-webgui") {
		t.Errorf("proxy missing client-id: %v", proxy.Args)
	}
	foundSecret := false
	for _, e := range proxy.Env {
		if e.Name == "CLIENT_SECRET" && e.ValueFrom != nil && e.ValueFrom.SecretKeyRef != nil &&
			e.ValueFrom.SecretKeyRef.Name == "acc-webgui-keycloak" {
			foundSecret = true
		}
	}
	if !foundSecret {
		t.Error("proxy CLIENT_SECRET must come from the keycloak Secret")
	}
}

// The CollectiveID of every referenced AgentCollective is injected into
// ACC_COLLECTIVE_IDS so the webgui observer subscribes to the right
// subjects (crash fix — proposal 023 follow-up).
func TestWebGUI_InjectsCollectiveIDs(t *testing.T) {
	coll := &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "ws", Namespace: "acc-system"},
		Spec:       accv1alpha1.AgentCollectiveSpec{CollectiveID: "rhoai-corpus-ws"},
	}
	c, _ := webguiClient(t, coll)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := webguiCorpus(&accv1alpha1.WebGUISpec{
		Enabled: ptr.To(true), Route: ptr.To(false), Keycloak: fullKeycloak(),
	})
	corpus.Spec.Collectives = []accv1alpha1.CollectiveRef{{Name: "ws"}}
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-webgui"}, deploy); err != nil {
		t.Fatalf("expected webgui Deployment: %v", err)
	}
	var got string
	for _, ct := range deploy.Spec.Template.Spec.Containers {
		if ct.Name != "webgui" {
			continue
		}
		for _, e := range ct.Env {
			if e.Name == "ACC_COLLECTIVE_IDS" {
				got = e.Value
			}
		}
	}
	if got != "rhoai-corpus-ws" {
		t.Errorf("ACC_COLLECTIVE_IDS = %q, want rhoai-corpus-ws", got)
	}
}
