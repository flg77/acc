// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the sandbox package: the OpenShell kernel-enforcement reconciler
// (OpenShell integration, Phase-1 scaffolding — opt-in gate + status only).
package unit_test

import (
	"context"
	"testing"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/sandbox"
)

func sandboxCorpus(mode accv1alpha1.DeployMode, spec *accv1alpha1.SandboxSpec) *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "corpus", Namespace: "acc-proj"},
		Spec: accv1alpha1.AgentCorpusSpec{
			DeployMode: mode,
			Version:    "0.1.0",
			Sandbox:    spec,
		},
	}
}

func sandboxReadyReason(t *testing.T, corpus *accv1alpha1.AgentCorpus) string {
	t.Helper()
	cond := meta.FindStatusCondition(corpus.Status.Conditions, sandbox.ConditionSandboxReady)
	if cond == nil {
		t.Fatalf("SandboxReady condition not set")
	}
	return cond.Reason
}

// Opt-out (nil block) — no sandbox, unchanged container-only isolation.
func TestSandbox_OptOut_NilBlock(t *testing.T) {
	r := &sandbox.OpenShellReconciler{} // Phase-1 reconciler is client-free
	corpus := sandboxCorpus(accv1alpha1.DeployModeRHOAI, nil)

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.SandboxReady || corpus.Status.SandboxBlocked {
		t.Errorf("expected not ready/blocked, got ready=%v blocked=%v",
			corpus.Status.SandboxReady, corpus.Status.SandboxBlocked)
	}
	if got := sandboxReadyReason(t, corpus); got != "Disabled" {
		t.Errorf("expected reason Disabled, got %q", got)
	}
}

// Enabled on rhoai — Phase-1 reports an honest pending state and must NOT
// fail-closed (nothing is provisioned to block yet).
func TestSandbox_Enabled_PendingImplementation(t *testing.T) {
	r := &sandbox.OpenShellReconciler{}
	corpus := sandboxCorpus(accv1alpha1.DeployModeRHOAI, &accv1alpha1.SandboxSpec{})

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if corpus.Status.SandboxReady {
		t.Error("expected SandboxReady=false while provisioning is pending")
	}
	if corpus.Status.SandboxBlocked {
		t.Error("Phase-1 must not fail-closed (nothing provisioned to block)")
	}
	if got := sandboxReadyReason(t, corpus); got != "PendingImplementation" {
		t.Errorf("expected reason PendingImplementation, got %q", got)
	}
}

// Standalone has no in-cluster kernel-sandbox attach path.
func TestSandbox_Standalone_NotApplicable(t *testing.T) {
	r := &sandbox.OpenShellReconciler{}
	corpus := sandboxCorpus(accv1alpha1.DeployModeStandalone,
		&accv1alpha1.SandboxSpec{Enabled: ptr.To(true)})

	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if got := sandboxReadyReason(t, corpus); got != "NotApplicableStandalone" {
		t.Errorf("expected reason NotApplicableStandalone, got %q", got)
	}
}

// The opt-in gate: nil block = off; a present block defaults on; explicit
// false = off (the tri-state +kubebuilder:default=true convention).
func TestSandbox_EnabledGate(t *testing.T) {
	cases := []struct {
		name string
		spec *accv1alpha1.SandboxSpec
		want bool
	}{
		{"nil block", nil, false},
		{"present, default", &accv1alpha1.SandboxSpec{}, true},
		{"explicit true", &accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, true},
		{"explicit false", &accv1alpha1.SandboxSpec{Enabled: ptr.To(false)}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			corpus := sandboxCorpus(accv1alpha1.DeployModeRHOAI, tc.spec)
			if got := sandbox.SandboxEnabled(corpus); got != tc.want {
				t.Errorf("SandboxEnabled = %v, want %v", got, tc.want)
			}
		})
	}
}

// SandboxWorkloadActive (the Phase-3 attach gate) additionally requires a
// GatewayURL — so enabling the opt-in block alone leaves the attach inert.
func TestSandbox_WorkloadActiveGate(t *testing.T) {
	cases := []struct {
		name string
		spec *accv1alpha1.SandboxSpec
		want bool
	}{
		{"nil block", nil, false},
		{"enabled, no gateway", &accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, false},
		{"enabled + gateway", &accv1alpha1.SandboxSpec{Enabled: ptr.To(true), GatewayURL: "https://gw:8080"}, true},
		{"disabled + gateway", &accv1alpha1.SandboxSpec{Enabled: ptr.To(false), GatewayURL: "https://gw:8080"}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			corpus := sandboxCorpus(accv1alpha1.DeployModeRHOAI, tc.spec)
			if got := sandbox.SandboxWorkloadActive(corpus); got != tc.want {
				t.Errorf("SandboxWorkloadActive = %v, want %v", got, tc.want)
			}
		})
	}
}
