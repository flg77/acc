// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Three-surface egress parity (gap-analysis risk R-4): the OpenShell sandbox
// policy, the OVN EgressFirewall, and the Cilium FQDN policy must all derive
// their external egress allow-set from the SAME source
// (security.ExternalEgressFQDNs) so the three enforcement surfaces cannot
// drift. The EgressFirewall + Cilium builders already call that function
// (networkpolicy.go), so asserting the OpenShell emitter agrees with it proves
// all three surfaces agree.
package unit_test

import (
	"testing"

	"gopkg.in/yaml.v3"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/sandbox"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/security"
)

// parityPolicyView is a minimal view of the emitted OpenShell policy YAML —
// just enough to read the egress hosts back out.
type parityPolicyView struct {
	NetworkPolicies map[string]struct {
		Endpoints []struct {
			Host string `yaml:"host"`
		} `yaml:"endpoints"`
	} `yaml:"network_policies"`
}

func openShellEgressHosts(t *testing.T, corpus *accv1alpha1.AgentCorpus) []string {
	t.Helper()
	out, err := sandbox.BuildSandboxPolicyYAML(corpus)
	if err != nil {
		t.Fatalf("BuildSandboxPolicyYAML: %v", err)
	}
	var view parityPolicyView
	if err := yaml.Unmarshal(out, &view); err != nil {
		t.Fatalf("unmarshal emitted policy: %v", err)
	}
	var hosts []string
	for _, np := range view.NetworkPolicies {
		for _, ep := range np.Endpoints {
			hosts = append(hosts, ep.Host)
		}
	}
	return hosts
}

func TestOpenShellEgressParityWithAllowSet(t *testing.T) {
	cases := []struct {
		name string
		np   *accv1alpha1.NetworkPolicySpec
	}{
		{"default (no networkPolicy block)", nil},
		{"override + extras", &accv1alpha1.NetworkPolicySpec{
			AllowedExternalLLM: []string{"llm.internal", "llm2.internal"},
			ExtraEgressFQDNs:   []string{"tools.internal"},
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			corpus := &accv1alpha1.AgentCorpus{
				Spec: accv1alpha1.AgentCorpusSpec{
					Sandbox:       &accv1alpha1.SandboxSpec{Enabled: ptr.To(true)},
					NetworkPolicy: tc.np,
				},
			}
			// The single source the OVN EgressFirewall + Cilium FQDN policy
			// also consume.
			want := security.ExternalEgressFQDNs(tc.np)
			got := openShellEgressHosts(t, corpus)
			if !equalStringSet(got, want) {
				t.Errorf("OpenShell egress hosts %v != egress allow-set %v (surfaces drifted)", got, want)
			}
		})
	}
}

// equalStringSet compares two slices as multisets (order-independent).
func equalStringSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	seen := make(map[string]int, len(a))
	for _, s := range a {
		seen[s]++
	}
	for _, s := range b {
		seen[s]--
	}
	for _, n := range seen {
		if n != 0 {
			return false
		}
	}
	return true
}
