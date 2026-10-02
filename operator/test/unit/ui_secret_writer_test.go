// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"context"
	"reflect"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/ui"
)

// UX-07: the WebGUI's Credentials page writes the one Secret spec.secretMount
// names. The UI Role may patch that Secret and nothing else — no get (the
// page cannot read back what it writes), no other Secret.

func secretWriterCorpus() *accv1alpha1.AgentCorpus {
	c := webguiCorpus(&accv1alpha1.WebGUISpec{Enabled: ptr.To(true), Keycloak: fullKeycloak()})
	c.Spec.SecretMount = &accv1alpha1.SecretMountSpec{SecretName: "acc-credentials"}
	return c
}

func TestUISecretWriter_PatchOnlyOnTheOneSecret(t *testing.T) {
	r := ui.UISecretWriterRule(secretWriterCorpus())
	if r == nil {
		t.Fatal("want a writer rule when the corpus mounts a Secret and runs a WebGUI")
	}
	if !reflect.DeepEqual(r.APIGroups, []string{""}) || !reflect.DeepEqual(r.Resources, []string{"secrets"}) {
		t.Errorf("rule targets %v/%v, want core secrets", r.APIGroups, r.Resources)
	}
	if !reflect.DeepEqual(r.ResourceNames, []string{"acc-credentials"}) {
		t.Errorf("resourceNames = %v, want exactly the mounted Secret", r.ResourceNames)
	}
	if !reflect.DeepEqual(r.Verbs, []string{"patch"}) {
		t.Errorf("verbs = %v, want patch only (no get: the page never reads a value)", r.Verbs)
	}
}

func TestUISecretWriter_AbsentWithoutMountOrWebGUI(t *testing.T) {
	noMount := secretWriterCorpus()
	noMount.Spec.SecretMount = nil
	noWeb := secretWriterCorpus()
	noWeb.Spec.WebGUI = nil
	disabled := secretWriterCorpus()
	disabled.Spec.WebGUI.Enabled = ptr.To(false)
	for name, c := range map[string]*accv1alpha1.AgentCorpus{
		"no secretMount": noMount, "no webgui": noWeb, "webgui disabled": disabled,
	} {
		if r := ui.UISecretWriterRule(c); r != nil {
			t.Errorf("%s: want no writer rule, got %v", name, r)
		}
		if got := len(ui.UIRules(c)); got != 1 {
			t.Errorf("%s: UIRules has %d rules, want the reader rule alone", name, got)
		}
	}
}

func TestUISecretWriter_WebGUIGetsTheRoleAndTheSecretName(t *testing.T) {
	c, _ := webguiClient(t)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := secretWriterCorpus()
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	ctx := context.Background()
	role := &rbacv1.Role{}
	if err := c.Get(ctx, types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-ui"}, role); err != nil {
		t.Fatalf("expected the UI Role: %v", err)
	}
	if len(role.Rules) != 2 || !reflect.DeepEqual(role.Rules[1].ResourceNames, []string{"acc-credentials"}) {
		t.Errorf("Role rules = %v, want the reader rule + patch on acc-credentials", role.Rules)
	}

	deploy := &appsv1.Deployment{}
	if err := c.Get(ctx, types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-webgui"}, deploy); err != nil {
		t.Fatalf("expected webgui Deployment: %v", err)
	}
	var got string
	for _, e := range deploy.Spec.Template.Spec.Containers[0].Env {
		if e.Name == "ACC_SECRET_WRITE_SECRET" {
			got = e.Value
		}
	}
	if got != "acc-credentials" {
		t.Errorf("ACC_SECRET_WRITE_SECRET = %q, want acc-credentials", got)
	}
}

// The TUI and WebGUI reconcilers upsert the same Role; both must compute the
// same rules from the corpus, or the Role flaps on every reconcile.
func TestUISecretWriter_TUIAndWebGUIAgreeOnTheRole(t *testing.T) {
	c, _ := webguiClient(t)
	corpus := secretWriterCorpus()
	corpus.Spec.TUI = &accv1alpha1.TUISpec{Enabled: ptr.To(true)}
	ctx := context.Background()
	key := types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-ui"}

	if _, err := (&ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}).Reconcile(ctx, corpus); err != nil {
		t.Fatalf("webgui Reconcile: %v", err)
	}
	afterWeb := &rbacv1.Role{}
	if err := c.Get(ctx, key, afterWeb); err != nil {
		t.Fatalf("Role: %v", err)
	}
	if _, err := (&ui.TUIReconciler{Client: c, Scheme: newScheme(t)}).Reconcile(ctx, corpus); err != nil {
		t.Fatalf("tui Reconcile: %v", err)
	}
	afterTUI := &rbacv1.Role{}
	if err := c.Get(ctx, key, afterTUI); err != nil {
		t.Fatalf("Role: %v", err)
	}
	if !reflect.DeepEqual(afterWeb.Rules, afterTUI.Rules) {
		t.Errorf("the TUI rewrote the Role:\n webgui: %v\n tui:    %v", afterWeb.Rules, afterTUI.Rules)
	}
}
