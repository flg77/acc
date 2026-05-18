// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package security

import (
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

func testCorpus(np *accv1alpha1.NetworkPolicySpec) *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "test-corpus", Namespace: "acc-ns"},
		Spec: accv1alpha1.AgentCorpusSpec{
			DeployMode:    accv1alpha1.DeployModeRHOAI,
			Version:       "0.1.0",
			NetworkPolicy: np,
		},
	}
}

// ---------------------------------------------------------------------------
// Tier 1 rule builders
// ---------------------------------------------------------------------------

func TestBuildTier1Policies_Count(t *testing.T) {
	policies := buildTier1Policies(testCorpus(&accv1alpha1.NetworkPolicySpec{Enabled: true}))
	if len(policies) != 4 {
		t.Fatalf("expected 4 Tier 1 policies, got %d", len(policies))
	}
	want := map[string]bool{
		policyDefaultDeny: false, policyAllowDNS: false,
		policyAllowInNS: false, policyAllowExternal: false,
	}
	for _, p := range policies {
		if _, ok := want[p.Name]; !ok {
			t.Errorf("unexpected policy name %q", p.Name)
		}
		want[p.Name] = true
		if p.Namespace != "acc-ns" {
			t.Errorf("%s: namespace = %q, want acc-ns", p.Name, p.Namespace)
		}
	}
	for name, seen := range want {
		if !seen {
			t.Errorf("missing policy %q", name)
		}
	}
}

func TestDefaultDenyHasNoRules(t *testing.T) {
	policies := buildTier1Policies(testCorpus(&accv1alpha1.NetworkPolicySpec{Enabled: true}))
	for _, p := range policies {
		if p.Name != policyDefaultDeny {
			continue
		}
		if len(p.Spec.Ingress) != 0 || len(p.Spec.Egress) != 0 {
			t.Error("default-deny must have no ingress/egress rules")
		}
		if len(p.Spec.PolicyTypes) != 2 {
			t.Error("default-deny must set both Ingress and Egress policy types")
		}
		return
	}
	t.Fatal("default-deny policy not found")
}

func TestAllowDNSOpensPort53(t *testing.T) {
	policies := buildTier1Policies(testCorpus(&accv1alpha1.NetworkPolicySpec{Enabled: true}))
	for _, p := range policies {
		if p.Name != policyAllowDNS {
			continue
		}
		if len(p.Spec.Egress) != 1 || len(p.Spec.Egress[0].Ports) != 2 {
			t.Fatal("allow-dns must open exactly UDP+TCP 53")
		}
		protos := map[corev1.Protocol]bool{}
		for _, port := range p.Spec.Egress[0].Ports {
			protos[*port.Protocol] = true
			if port.Port.IntValue() != 53 {
				t.Errorf("allow-dns port = %v, want 53", port.Port)
			}
		}
		if !protos[corev1.ProtocolUDP] || !protos[corev1.ProtocolTCP] {
			t.Error("allow-dns must cover both UDP and TCP")
		}
		return
	}
	t.Fatal("allow-dns policy not found")
}

func TestAllowExternalExcludesRFC1918(t *testing.T) {
	policies := buildTier1Policies(testCorpus(&accv1alpha1.NetworkPolicySpec{Enabled: true}))
	for _, p := range policies {
		if p.Name != policyAllowExternal {
			continue
		}
		block := p.Spec.Egress[0].To[0].IPBlock
		if block.CIDR != "0.0.0.0/0" {
			t.Errorf("external CIDR = %q, want 0.0.0.0/0", block.CIDR)
		}
		if len(block.Except) != 3 {
			t.Errorf("external policy must except the 3 RFC1918 ranges, got %v", block.Except)
		}
		return
	}
	t.Fatal("allow-external policy not found")
}

func TestWithoutDefaultDeny(t *testing.T) {
	policies := buildTier1Policies(testCorpus(&accv1alpha1.NetworkPolicySpec{Enabled: true}))
	trimmed := withoutDefaultDeny(policies)
	if len(trimmed) != 3 {
		t.Fatalf("audit-mode set should drop 1 policy, got %d", len(trimmed))
	}
	for _, p := range trimmed {
		if p.Name == policyDefaultDeny {
			t.Error("withoutDefaultDeny still contains the default-deny policy")
		}
	}
}

func TestAgentPodSelectorMatchesAgentRole(t *testing.T) {
	sel := agentPodSelector(testCorpus(nil))
	if sel.MatchLabels[accv1alpha1.LabelCorpusName] != "test-corpus" {
		t.Error("selector must pin the corpus name")
	}
	if len(sel.MatchExpressions) != 1 ||
		sel.MatchExpressions[0].Key != accv1alpha1.LabelAgentRole ||
		sel.MatchExpressions[0].Operator != metav1.LabelSelectorOpExists {
		t.Error("selector must require the agent-role label to Exist")
	}
}

// ---------------------------------------------------------------------------
// Tier 2 — FQDN allow-set
// ---------------------------------------------------------------------------

func TestExternalEgressFQDNs_Default(t *testing.T) {
	got := externalEgressFQDNs(&accv1alpha1.NetworkPolicySpec{})
	if len(got) != 2 {
		t.Fatalf("default FQDN set should have 2 entries, got %v", got)
	}
}

func TestExternalEgressFQDNs_OverrideAndExtra(t *testing.T) {
	got := externalEgressFQDNs(&accv1alpha1.NetworkPolicySpec{
		AllowedExternalLLM: []string{"llm.internal"},
		ExtraEgressFQDNs:   []string{"slack-proxy.internal"},
	})
	if len(got) != 2 || got[0] != "llm.internal" || got[1] != "slack-proxy.internal" {
		t.Errorf("FQDN set = %v, want [llm.internal slack-proxy.internal]", got)
	}
}

// ---------------------------------------------------------------------------
// Tier 2 / 3 — unstructured CRD objects
// ---------------------------------------------------------------------------

func TestBuildEgressFirewall(t *testing.T) {
	ef := buildEgressFirewall(testCorpus(&accv1alpha1.NetworkPolicySpec{}),
		[]string{"api.anthropic.com"})
	if ef.GetKind() != "EgressFirewall" {
		t.Errorf("kind = %q, want EgressFirewall", ef.GetKind())
	}
	if ef.GetName() != "default" {
		t.Errorf("EgressFirewall name = %q, want default (OVN requirement)", ef.GetName())
	}
	egress, found, _ := unstructured.NestedSlice(ef.Object, "spec", "egress")
	if !found || len(egress) != 2 {
		t.Fatalf("expected 1 allow + 1 final deny, got %v", egress)
	}
	last := egress[len(egress)-1].(map[string]interface{})
	if last["type"] != "Deny" {
		t.Error("EgressFirewall final rule must be a Deny")
	}
}

func TestBuildCiliumFQDNPolicy(t *testing.T) {
	cnp := buildCiliumFQDNPolicy(testCorpus(&accv1alpha1.NetworkPolicySpec{}),
		[]string{"api.anthropic.com"})
	if cnp.GetKind() != "CiliumNetworkPolicy" {
		t.Errorf("kind = %q, want CiliumNetworkPolicy", cnp.GetKind())
	}
	egress, found, _ := unstructured.NestedSlice(cnp.Object, "spec", "egress")
	if !found || len(egress) != 2 {
		t.Fatalf("expected a DNS rule + an FQDN rule, got %v", egress)
	}
}

func TestBuildCiliumL7Policy(t *testing.T) {
	l7 := buildCiliumL7Policy(testCorpus(&accv1alpha1.NetworkPolicySpec{}),
		[]string{"api.anthropic.com"})
	if l7.GetName() != "acc-agent-fqdn-egress" {
		t.Errorf("L7 policy name = %q — must match the Tier 2 name so it "+
			"supersedes in place", l7.GetName())
	}
	egress, _, _ := unstructured.NestedSlice(l7.Object, "spec", "egress")
	if len(egress) != 2 {
		t.Fatalf("expected DNS + L7 egress rules, got %v", egress)
	}
}
