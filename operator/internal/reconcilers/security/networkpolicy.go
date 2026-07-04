// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package security

import (
	"context"
	"fmt"

	networkingv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/runtime"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// ConditionNetworkPolicyReady is the status condition type this
// reconciler owns (proposal 014).
const ConditionNetworkPolicyReady = "NetworkPolicyReady"

// NetworkPolicyReady reasons — the full taxonomy.
const (
	reasonReady                = "Ready"
	reasonDisabled             = "Disabled"
	reasonNotApplicableStdAlone = "NotApplicableStandalone"
	reasonCNIDoesNotEnforce    = "CNIDoesNotEnforce"
	reasonAuditMode            = "AuditMode"
)

// Policy backends reported in status.networkPolicy.backend.
const (
	backendNone          = "none"
	backendNetworkPolicy = "networkpolicy"
)

// NetworkPolicyReconciler emits the capability-tiered network-isolation
// objects for a corpus (proposal 014, security roadmap Phase 1).
//
// It is opt-in: with spec.networkPolicy nil or Enabled=false it emits
// nothing and reports the condition True/Disabled.  When enabled it
// emits the Tier 1 standard NetworkPolicy set; Tiers 2/3 (FQDN / L7)
// layer on in proposals-014 PR-4 and PR-5.
type NetworkPolicyReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *NetworkPolicyReconciler) Name() string { return "security/networkpolicy" }

// Reconcile implements SubReconciler.
func (r *NetworkPolicyReconciler) Reconcile(
	ctx context.Context, corpus *accv1alpha1.AgentCorpus,
) (reconcilers.SubResult, error) {
	np := corpus.Spec.NetworkPolicy

	// --- opt-in gate: nil or disabled → emit nothing ----------------------
	if np == nil || !np.Enabled {
		corpus.Status.NetworkPolicy = accv1alpha1.NetworkPolicyStatus{
			ActiveTier: 0, Backend: backendNone, PolicyCount: 0,
		}
		status.SetCondition(&corpus.Status.Conditions, ConditionNetworkPolicyReady,
			metav1.ConditionTrue, reasonDisabled,
			"network policy is disabled (spec.networkPolicy.enabled=false)")
		return reconcilers.SubResult{}, nil
	}

	// --- standalone: no Kubernetes, no NetworkPolicy concept --------------
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeStandalone {
		corpus.Status.NetworkPolicy = accv1alpha1.NetworkPolicyStatus{
			ActiveTier: 0, Backend: backendNone, PolicyCount: 0,
		}
		status.SetCondition(&corpus.Status.Conditions, ConditionNetworkPolicyReady,
			metav1.ConditionTrue, reasonNotApplicableStdAlone,
			"deployMode=standalone has no Kubernetes network layer; "+
				"network policy is not applicable")
		return reconcilers.SubResult{}, nil
	}

	// --- emit the Tier 1 NetworkPolicy objects ---------------------------
	// They are emitted even when the CNI does not enforce them, so that a
	// later CNI swap (Calico/Cilium on K3s) makes them effective and the
	// manifests stay consistent across deploy modes.
	//
	// Audit mode (the safe canary): the default-deny policy is omitted,
	// so the allow policies are inert — traffic flows freely while the
	// objects are present for `kubectl describe` inspection.  Flipping
	// to mode=enforce adds the default-deny.
	auditMode := np.Mode == "audit"
	policies := buildTier1Policies(corpus)
	if auditMode {
		policies = withoutDefaultDeny(policies)
	}
	count, err := r.applyPolicies(ctx, corpus, policies)
	if err != nil {
		return reconcilers.SubResult{}, err
	}

	// --- is the running CNI actually enforcing them? ---------------------
	enforced := corpus.Status.Prerequisites.CiliumInstalled ||
		corpus.Status.Prerequisites.OVNEgressFirewallSupported
	switch np.CNIEnforces {
	case "true":
		enforced = true
	case "false":
		enforced = false
	}

	if !enforced {
		// Honesty over false assurance — objects exist but Flannel-class
		// CNIs ignore them.
		corpus.Status.NetworkPolicy = accv1alpha1.NetworkPolicyStatus{
			ActiveTier: 0, Backend: backendNetworkPolicy, PolicyCount: int32(count),
		}
		status.SetCondition(&corpus.Status.Conditions, ConditionNetworkPolicyReady,
			metav1.ConditionFalse, reasonCNIDoesNotEnforce,
			fmt.Sprintf("emitted %d NetworkPolicy object(s) but no policy-"+
				"enforcing CNI was detected — they will NOT be enforced "+
				"(e.g. K3s/Flannel). Install OVN-Kubernetes/Cilium/Calico "+
				"or set spec.networkPolicy.cniEnforces.", count))
		return reconcilers.SubResult{}, nil
	}

	// --- enhanced tiers: Tier 2 FQDN egress / Tier 3 Cilium L7 -----------
	activeTier := int32(1)
	backend := backendNetworkPolicy
	maxTier := np.MaxTier
	if maxTier < 1 {
		maxTier = 1
	}
	if maxTier >= 2 {
		t, b, extra, terr := r.reconcileEnhancedTiers(ctx, corpus, np, maxTier)
		if terr != nil {
			return reconcilers.SubResult{}, terr
		}
		if t > activeTier {
			activeTier, backend = t, b
			count += extra
		}
	}

	corpus.Status.NetworkPolicy = accv1alpha1.NetworkPolicyStatus{
		ActiveTier: activeTier, Backend: backend, PolicyCount: int32(count),
	}
	if auditMode {
		status.SetCondition(&corpus.Status.Conditions, ConditionNetworkPolicyReady,
			metav1.ConditionTrue, reasonAuditMode,
			fmt.Sprintf("Tier %d policies emitted in AUDIT mode — the "+
				"default-deny is omitted so nothing is dropped; %d object(s) "+
				"present for inspection. Set mode=enforce to activate.",
				activeTier, count))
	} else {
		status.SetCondition(&corpus.Status.Conditions, ConditionNetworkPolicyReady,
			metav1.ConditionTrue, reasonReady,
			fmt.Sprintf("Tier %d network isolation active — %d policy "+
				"object(s) enforced (backend: %s)", activeTier, count, backend))
	}
	return reconcilers.SubResult{}, nil
}

// reconcileEnhancedTiers emits the Tier 2 (FQDN egress) or Tier 3
// (Cilium L7) policy and returns the achieved tier, backend, and the
// number of extra objects emitted.  When no enhanced backend is
// available it returns Tier 1 unchanged.
func (r *NetworkPolicyReconciler) reconcileEnhancedTiers(
	ctx context.Context, corpus *accv1alpha1.AgentCorpus,
	np *accv1alpha1.NetworkPolicySpec, maxTier int32,
) (tier int32, backend string, extraCount int, err error) {
	cilium := corpus.Status.Prerequisites.CiliumInstalled
	ovn := corpus.Status.Prerequisites.OVNEgressFirewallSupported
	fqdns := ExternalEgressFQDNs(np)

	switch {
	case maxTier >= 3 && cilium:
		if err := r.upsertUnstructured(ctx, corpus,
			buildCiliumL7Policy(corpus, fqdns)); err != nil {
			return 1, backendNetworkPolicy, 0, err
		}
		return 3, backendCilium, 1, nil
	case maxTier >= 2 && cilium:
		if err := r.upsertUnstructured(ctx, corpus,
			buildCiliumFQDNPolicy(corpus, fqdns)); err != nil {
			return 1, backendNetworkPolicy, 0, err
		}
		return 2, backendCilium, 1, nil
	case maxTier >= 2 && ovn:
		if err := r.upsertUnstructured(ctx, corpus,
			buildEgressFirewall(corpus, fqdns)); err != nil {
			return 1, backendNetworkPolicy, 0, err
		}
		return 2, backendEgressFirewall, 1, nil
	default:
		// maxTier requests an enhanced tier but no backend supports it.
		return 1, backendNetworkPolicy, 0, nil
	}
}

// withoutDefaultDeny returns the policy slice minus the default-deny
// object — used by audit mode.
func withoutDefaultDeny(policies []*networkingv1.NetworkPolicy) []*networkingv1.NetworkPolicy {
	out := policies[:0:0]
	for _, p := range policies {
		if p.Name != policyDefaultDeny {
			out = append(out, p)
		}
	}
	return out
}

// applyPolicies upserts each desired NetworkPolicy and returns the count.
func (r *NetworkPolicyReconciler) applyPolicies(
	ctx context.Context, corpus *accv1alpha1.AgentCorpus,
	policies []*networkingv1.NetworkPolicy,
) (int, error) {
	for _, p := range policies {
		desired := p
		if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, desired,
			func(existing client.Object) error {
				existing.(*networkingv1.NetworkPolicy).Spec = desired.Spec
				return nil
			}); err != nil {
			return 0, fmt.Errorf("upsert NetworkPolicy %s: %w", p.Name, err)
		}
	}
	return len(policies), nil
}
