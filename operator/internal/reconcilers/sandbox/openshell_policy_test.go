// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package sandbox

import (
	"slices"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

func policyCorpus(s *accv1alpha1.SandboxSpec, np *accv1alpha1.NetworkPolicySpec) *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		Spec: accv1alpha1.AgentCorpusSpec{Sandbox: s, NetworkPolicy: np},
	}
}

func endpointHosts(eps []policyEndpoint) []string {
	out := make([]string, 0, len(eps))
	for _, ep := range eps {
		out = append(out, ep.Host)
	}
	return out
}

// Cat-A: filesystem + process containment is present with the expected shape.
func TestBuildSandboxPolicy_CatADefaults(t *testing.T) {
	doc := buildSandboxPolicy(policyCorpus(&accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, nil))

	if doc.Version != 1 {
		t.Errorf("version = %d, want 1", doc.Version)
	}
	if !doc.FilesystemPolicy.IncludeWorkdir {
		t.Error("include_workdir should be true")
	}
	if !slices.Contains(doc.FilesystemPolicy.ReadWrite, "/workspace") {
		t.Errorf("read_write %v should contain /workspace", doc.FilesystemPolicy.ReadWrite)
	}
	if !slices.Contains(doc.FilesystemPolicy.ReadOnly, "/usr") {
		t.Errorf("read_only %v should contain /usr", doc.FilesystemPolicy.ReadOnly)
	}
	// /workspace must be writable, never simultaneously locked read-only.
	if slices.Contains(doc.FilesystemPolicy.ReadOnly, "/workspace") {
		t.Error("/workspace must not appear in read_only")
	}
	if doc.Process.RunAsUser != sandboxRunAsUser || doc.Process.RunAsGroup != sandboxRunAsGroup {
		t.Errorf("process run-as = %q/%q, want sandbox/sandbox",
			doc.Process.RunAsUser, doc.Process.RunAsGroup)
	}
}

// D3: FailClosed drives the Landlock posture (hard requirement vs best-effort).
func TestBuildSandboxPolicy_FailClosedControlsLandlock(t *testing.T) {
	// Default (FailClosed nil → true) → Landlock is a hard requirement.
	hard := buildSandboxPolicy(policyCorpus(&accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, nil))
	if hard.Landlock.Compatibility != landlockHardRequirement {
		t.Errorf("default landlock = %q, want %q", hard.Landlock.Compatibility, landlockHardRequirement)
	}
	// Explicit FailClosed=false → best-effort (degrade cleanly where absent).
	best := buildSandboxPolicy(policyCorpus(
		&accv1alpha1.SandboxSpec{Enabled: ptr.To(true), FailClosed: ptr.To(false)}, nil))
	if best.Landlock.Compatibility != landlockBestEffort {
		t.Errorf("failClosed=false landlock = %q, want %q", best.Landlock.Compatibility, landlockBestEffort)
	}
}

// Cat-B: egress endpoints derive from the built-in default allow-set, enforced.
func TestBuildSandboxPolicy_CatBEgressDefault(t *testing.T) {
	doc := buildSandboxPolicy(policyCorpus(&accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, nil))
	np, ok := doc.NetworkPolicies[inferencePolicyKey]
	if !ok {
		t.Fatalf("network_policies missing %q: %v", inferencePolicyKey, doc.NetworkPolicies)
	}
	if len(np.Endpoints) != 2 {
		t.Fatalf("default egress should have 2 endpoints, got %v", np.Endpoints)
	}
	for _, ep := range np.Endpoints {
		if ep.Port != egressPort || ep.Enforcement != egressEnforcement || ep.Access != egressAccess {
			t.Errorf("endpoint %+v: want port 443 / enforce / read-write", ep)
		}
	}
	// Default-deny hinges on a non-empty binary allow-list.
	if len(np.Binaries) == 0 {
		t.Error("egress policy should list the permitted binaries")
	}
}

// Cat-B: an operator override + extras flow straight through to the endpoints.
func TestBuildSandboxPolicy_CatBEgressOverride(t *testing.T) {
	np := &accv1alpha1.NetworkPolicySpec{
		AllowedExternalLLM: []string{"llm.internal"},
		ExtraEgressFQDNs:   []string{"tools.internal"},
	}
	doc := buildSandboxPolicy(policyCorpus(&accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, np))
	hosts := endpointHosts(doc.NetworkPolicies[inferencePolicyKey].Endpoints)
	if want := []string{"llm.internal", "tools.internal"}; !slices.Equal(hosts, want) {
		t.Errorf("egress hosts = %v, want %v", hosts, want)
	}
}

// The emitted YAML uses OpenShell's serde field names and round-trips.
func TestBuildSandboxPolicyYAML_Shape(t *testing.T) {
	out, err := BuildSandboxPolicyYAML(policyCorpus(&accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, nil))
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	for _, want := range []string{
		"filesystem_policy:", "network_policies:", "include_workdir: true",
		"run_as_user: sandbox", "compatibility: hard_requirement", "host: api.anthropic.com",
	} {
		if !strings.Contains(string(out), want) {
			t.Errorf("policy YAML missing %q\n---\n%s", want, out)
		}
	}
	var back sandboxPolicyDoc
	if err := yaml.Unmarshal(out, &back); err != nil {
		t.Fatalf("emitted YAML does not round-trip: %v", err)
	}
}
