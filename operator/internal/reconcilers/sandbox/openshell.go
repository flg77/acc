// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package sandbox reconciles OpenShell kernel-enforced execution sandboxing
// for a corpus's agents (OpenShell integration — see the ACC Compliance
// implementation plan).
package sandbox

import (
	"context"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

// ConditionSandboxReady is the status condition this reconciler owns.
const ConditionSandboxReady = "SandboxReady"

// OpenShellReconciler provisions a per-agent OpenShell kernel-enforced
// execution sandbox and (in later phases) translates the corpus's Cat-A/B
// rules into the sandbox's OPA/static policy.
//
// It is the ENFORCE-mode inversion of the consume-only runtime-evidence
// bridge (proposal 015): where that bridge OBSERVES kernel events from
// whichever runtime-security backend the cluster already runs, OpenShell
// PROVISIONS and ENFORCES the cage in-line — turning Cat-A from
// evaluated-at-dispatch into enforced-at-the-kernel.
//
// OPT-IN PER AGENTSET: a no-op unless spec.sandbox is enabled. OpenShell is
// upstream-alpha; the Gateway provisioning + Cat-A→OPA translation + agent-pod
// attach are finalised by the Phase-0 spike, so this reconciler currently
// gates and reports status only (Phase-1 scaffolding, default-OFF, inert).
type OpenShellReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *OpenShellReconciler) Name() string { return "sandbox/openshell" }

// SandboxEnabled reports whether the corpus opted into OpenShell sandboxing.
// A nil block = disabled; a present block with a nil Enabled = enabled
// (matches the +kubebuilder:default=true tri-state convention). Exported so
// the agent-pod attach seam and the tests agree on one gate.
func SandboxEnabled(corpus *accv1alpha1.AgentCorpus) bool {
	s := corpus.Spec.Sandbox
	return s != nil && (s.Enabled == nil || *s.Enabled)
}

// Reconcile implements SubReconciler.
func (r *OpenShellReconciler) Reconcile(_ context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	// Opt-in gate: nil or disabled → agents keep today's container-only
	// isolation. Nothing to provision.
	if !SandboxEnabled(corpus) {
		corpus.Status.SandboxReady = false
		corpus.Status.SandboxBlocked = false
		status.SetCondition(&corpus.Status.Conditions, ConditionSandboxReady,
			metav1.ConditionTrue, "Disabled",
			"OpenShell sandboxing is disabled (spec.sandbox absent or "+
				"enabled=false); agents run with container-only isolation")
		return reconcilers.SubResult{}, nil
	}

	// Standalone has no in-cluster kernel-sandbox attach path.
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeStandalone {
		corpus.Status.SandboxReady = false
		corpus.Status.SandboxBlocked = false
		status.SetCondition(&corpus.Status.Conditions, ConditionSandboxReady,
			metav1.ConditionTrue, "NotApplicableStandalone",
			"deployMode=standalone has no in-cluster kernel-sandbox attach "+
				"path; OpenShell enforcement is not applicable")
		return reconcilers.SubResult{}, nil
	}

	// Phase-1 scaffolding: Gateway provisioning + Cat-A→OPA policy translation
	// + agent-pod attach + fail-closed enforcement land after the Phase-0
	// spike pins OpenShell's (alpha, undocumented) Kubernetes surface. Report
	// an honest pending state; do NOT fail-closed yet — nothing is provisioned
	// to block, and Phase-1 must not change running behaviour.
	corpus.Status.SandboxReady = false
	corpus.Status.SandboxBlocked = false
	status.SetCondition(&corpus.Status.Conditions, ConditionSandboxReady,
		metav1.ConditionFalse, "PendingImplementation",
		"OpenShell sandboxing is enabled but provisioning is not yet "+
			"implemented (gated on the Phase-0 spike pinning OpenShell's "+
			"Kubernetes policy surface). Agents run with container-only "+
			"isolation until then.")
	return reconcilers.SubResult{}, nil
}
