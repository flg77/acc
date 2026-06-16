// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the acc-tui attach-pod reconciler (proposal 023 / ADR 025).
package unit_test

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/ui"
)

func tuiCorpus(tui *accv1alpha1.TUISpec) *accv1alpha1.AgentCorpus {
	c := webguiCorpus(nil)
	c.Spec.TUI = tui
	return c
}

func TestTUI_NilSpecNoop(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(nil)
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.TUIDeployed {
		t.Error("nil tui should leave TUIDeployed=false")
	}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-tui"}, &appsv1.Deployment{}); err == nil {
		t.Error("nil tui should not create a Deployment")
	}
}

func TestTUI_DisabledNoop(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(false)})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.TUIDeployed {
		t.Error("disabled tui must not deploy")
	}
}

func TestTUI_DeploysIdleAttachPod(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(true)})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !corpus.Status.TUIDeployed {
		t.Fatal("expected TUIDeployed=true")
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-tui"}, deploy); err != nil {
		t.Fatalf("expected tui Deployment: %v", err)
	}
	ctr := deploy.Spec.Template.Spec.Containers[0]
	if ctr.Name != "tui" {
		t.Errorf("container name = %q, want tui", ctr.Name)
	}
	// Idle so `oc rsh ... acc-tui` can attach a TTY.
	if len(ctr.Command) == 0 || ctr.Command[0] != "sleep" {
		t.Errorf("tui pod should idle (sleep), got command %v", ctr.Command)
	}
	env := map[string]string{}
	for _, e := range ctr.Env {
		env[e.Name] = e.Value
	}
	if env["ACC_NATS_URL"] != "nats://rhoai-corpus-nats:4222" {
		t.Errorf("ACC_NATS_URL = %q, want the corpus NATS service", env["ACC_NATS_URL"])
	}
}

// webTerminal=true + a corpus webgui Keycloak → ttyd behind oauth2-proxy.
func TestTUI_WebTerminalDeploysTtydWithKeycloak(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(true), WebTerminal: ptr.To(true)})
	corpus.Spec.WebGUI = &accv1alpha1.WebGUISpec{Keycloak: fullKeycloak()} // reused for auth
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if !corpus.Status.TUIDeployed {
		t.Fatal("expected TUIDeployed=true")
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-tui"}, deploy); err != nil {
		t.Fatalf("expected tui Deployment: %v", err)
	}
	byName := map[string][]string{}
	for _, ct := range deploy.Spec.Template.Spec.Containers {
		byName[ct.Name] = ct.Command
	}
	if _, ok := byName["oauth2-proxy"]; !ok {
		t.Fatalf("web terminal must run behind oauth2-proxy, got %v", deploy.Spec.Template.Spec.Containers)
	}
	cmd, ok := byName["tui"]
	if !ok || len(cmd) == 0 || cmd[0] != "ttyd" {
		t.Errorf("web-terminal tui container should run ttyd, got %v", cmd)
	}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-tui"}, &corev1.Service{}); err != nil {
		t.Errorf("expected tui Service for the web terminal: %v", err)
	}
}

// webTerminal=true WITHOUT a Keycloak config → fail-safe to the idle
// rsh-attach pod (ADR 025 §5: never expose an unauthenticated terminal).
func TestTUI_WebTerminalWithoutKeycloakFallsBackToIdle(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(true), WebTerminal: ptr.To(true)})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	deploy := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-tui"}, deploy); err != nil {
		t.Fatalf("expected tui Deployment: %v", err)
	}
	ctr := deploy.Spec.Template.Spec.Containers[0]
	if len(ctr.Command) == 0 || ctr.Command[0] != "sleep" {
		t.Errorf("no keycloak → must fall back to the idle (sleep) pod, got %v", ctr.Command)
	}
}
