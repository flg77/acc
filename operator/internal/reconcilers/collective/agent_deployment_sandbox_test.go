// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package collective

import (
	"context"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/sandbox"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// OpenShell Model 2 (proposal 051): a sandboxed corpus's agent stays a normal
// StatefulSet (NOT an Agent Sandbox CR) but gains the `openshell sandbox create`
// initContainer + a per-agent Cat-A/B/C policy ConfigMap. The runtime exec
// skills then delegate code execution into that sandbox.
//
// The Agent Sandbox GVK is deliberately NOT registered with the scheme — if the
// reconcile relapsed to emitting a Sandbox CR (the superseded Model-1 path) the
// fake client would fail to create it, so this doubles as a regression guard.
func TestReconcileRoleDeployment_SandboxedProvisionsInitContainer(t *testing.T) {
	s := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		corev1.AddToScheme, appsv1.AddToScheme, accv1alpha1.AddToScheme,
	} {
		if err := add(s); err != nil {
			t.Fatalf("AddToScheme: %v", err)
		}
	}
	cl := fake.NewClientBuilder().WithScheme(s).Build()
	r := &AgentDeploymentReconciler{Client: cl, Scheme: s}

	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "acc-proj"},
		Spec: accv1alpha1.AgentCorpusSpec{
			Version: "0.1.0",
			Sandbox: &accv1alpha1.SandboxSpec{
				Enabled:           ptr.To(true),
				GatewayURL:        "https://gw:8080",
				CredentialsSecret: "openshell-oidc",
			},
		},
	}
	coll := &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "acc-proj"},
		Spec:       accv1alpha1.AgentCollectiveSpec{CollectiveID: "demo"},
	}
	roleSpec := accv1alpha1.AgentRoleSpec{Role: "coding", Replicas: 1}

	_, _, progressing, err := r.reconcileRoleDeployment(
		context.Background(), corpus, coll, roleSpec, "demo-config", "demo-coding-role", "acc-proj", "")
	if err != nil {
		t.Fatalf("reconcileRoleDeployment (sandboxed): %v", err)
	}
	if !progressing {
		t.Error("a freshly-created StatefulSet should be progressing")
	}

	deployName := util.AgentDeploymentName(coll.Name, "coding")

	// The agent is a StatefulSet (NOT a Sandbox CR — the GVK is unregistered).
	sts := &appsv1.StatefulSet{}
	if err := cl.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-proj", Name: deployName}, sts); err != nil {
		t.Fatalf("agent StatefulSet not created: %v", err)
	}

	// Its pod carries the sandbox-create initContainer.
	var createCmd string
	for _, c := range sts.Spec.Template.Spec.InitContainers {
		if len(c.Command) == 3 {
			createCmd = c.Command[2]
		}
	}
	if !strings.Contains(createCmd, "sandbox create") {
		t.Errorf("no sandbox-create initContainer; inits=%+v", sts.Spec.Template.Spec.InitContainers)
	}

	// The Cat-A/B/C policy ConfigMap was created (BuildSandboxPolicyYAML).
	policyCM := &corev1.ConfigMap{}
	if err := cl.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-proj", Name: sandbox.PolicyConfigMapName(deployName)},
		policyCM); err != nil {
		t.Fatalf("sandbox policy ConfigMap not created: %v", err)
	}
}

// Opt-out (or no gateway): the agent is a plain StatefulSet — no sandbox
// initContainer, no policy ConfigMap. Byte-for-byte the pre-OpenShell path.
func TestReconcileRoleDeployment_UnsandboxedIsPlain(t *testing.T) {
	s := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		corev1.AddToScheme, appsv1.AddToScheme, accv1alpha1.AddToScheme,
	} {
		if err := add(s); err != nil {
			t.Fatalf("AddToScheme: %v", err)
		}
	}
	cl := fake.NewClientBuilder().WithScheme(s).Build()
	r := &AgentDeploymentReconciler{Client: cl, Scheme: s}

	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "acc-proj"},
		Spec:       accv1alpha1.AgentCorpusSpec{Version: "0.1.0"}, // no Sandbox block
	}
	coll := &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "plain-set", Namespace: "acc-proj"},
		Spec:       accv1alpha1.AgentCollectiveSpec{CollectiveID: "plain"},
	}
	roleSpec := accv1alpha1.AgentRoleSpec{Role: "coding", Replicas: 1}

	if _, _, _, err := r.reconcileRoleDeployment(
		context.Background(), corpus, coll, roleSpec, "plain-config", "plain-coding-role", "acc-proj", ""); err != nil {
		t.Fatalf("reconcileRoleDeployment (plain): %v", err)
	}

	deployName := util.AgentDeploymentName(coll.Name, "coding")
	sts := &appsv1.StatefulSet{}
	if err := cl.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-proj", Name: deployName}, sts); err != nil {
		t.Fatalf("plain StatefulSet not created: %v", err)
	}
	if len(sts.Spec.Template.Spec.InitContainers) != 0 {
		t.Errorf("opt-out agent must have no initContainers, got %+v",
			sts.Spec.Template.Spec.InitContainers)
	}
	if err := cl.Get(context.Background(),
		types.NamespacedName{Namespace: "acc-proj", Name: sandbox.PolicyConfigMapName(deployName)},
		&corev1.ConfigMap{}); err == nil {
		t.Error("opt-out agent must not create a sandbox policy ConfigMap")
	}
}
