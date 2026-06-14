// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the AgentCollective mutating/validating webhook:
//   - assistant default-injection (proposal 023 §4b / 021 C3), and
//   - HYBRID role validation (built-in roles hard-fail on near-typo;
//     package-provided roles pass with an admission warning).
package unit_test

import (
	"strings"
	"testing"

	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

func collectiveWith(agents ...accv1alpha1.AgentRoleSpec) *accv1alpha1.AgentCollective {
	return &accv1alpha1.AgentCollective{
		Spec: accv1alpha1.AgentCollectiveSpec{
			CollectiveID: "test",
			Agents:       agents,
		},
	}
}

func collectiveRoleNames(c *accv1alpha1.AgentCollective) []string {
	out := make([]string, 0, len(c.Spec.Agents))
	for _, a := range c.Spec.Agents {
		out = append(out, string(a.Role))
	}
	return out
}

func collectiveHasRole(c *accv1alpha1.AgentCollective, role string) bool {
	for _, a := range c.Spec.Agents {
		if string(a.Role) == role {
			return true
		}
	}
	return false
}

// Default() injects the assistant concierge when absent — first in the roster,
// with replicas 1.
func TestCollectiveDefault_InjectsAssistant(t *testing.T) {
	c := collectiveWith(accv1alpha1.AgentRoleSpec{Role: "reviewer"})
	c.Default()
	if !collectiveHasRole(c, "assistant") {
		t.Fatalf("expected assistant injected, got %v", collectiveRoleNames(c))
	}
	if string(c.Spec.Agents[0].Role) != "assistant" {
		t.Errorf("assistant should lead the roster, got %v", collectiveRoleNames(c))
	}
	if c.Spec.Agents[0].Replicas != 1 {
		t.Errorf("injected assistant replicas = %d, want 1", c.Spec.Agents[0].Replicas)
	}
}

// An explicitly-declared assistant (even parked at replicas:0) is never
// duplicated by injection.
func TestCollectiveDefault_DoesNotDuplicateAssistant(t *testing.T) {
	c := collectiveWith(
		accv1alpha1.AgentRoleSpec{Role: "assistant", Replicas: 0},
		accv1alpha1.AgentRoleSpec{Role: "reviewer"},
	)
	c.Default()
	n := 0
	for _, a := range c.Spec.Agents {
		if a.Role == "assistant" {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("expected exactly one assistant, got %d (%v)", n, collectiveRoleNames(c))
	}
}

// DisableAssistant suppresses injection.
func TestCollectiveDefault_DisableAssistant(t *testing.T) {
	c := collectiveWith(accv1alpha1.AgentRoleSpec{Role: "reviewer"})
	c.Spec.DisableAssistant = ptr.To(true)
	c.Default()
	if collectiveHasRole(c, "assistant") {
		t.Errorf("DisableAssistant=true must suppress injection, got %v", collectiveRoleNames(c))
	}
}

// A built-in role validates clean: no error, no warning.
func TestCollectiveValidate_BuiltinRoleClean(t *testing.T) {
	c := collectiveWith(accv1alpha1.AgentRoleSpec{Role: "reviewer"})
	warns, err := c.ValidateCreate()
	if err != nil {
		t.Fatalf("built-in role should validate: %v", err)
	}
	if len(warns) != 0 {
		t.Errorf("built-in role should not warn, got %v", warns)
	}
}

// A near-typo of a built-in role is a hard error (caught early).
func TestCollectiveValidate_TypoOfBuiltinRejected(t *testing.T) {
	c := collectiveWith(accv1alpha1.AgentRoleSpec{Role: "revewer"}) // typo of reviewer
	if _, err := c.ValidateCreate(); err == nil {
		t.Fatal("a near-typo of a built-in role must be rejected")
	}
}

// A distinct, package-provided role name is allowed with an admission warning.
func TestCollectiveValidate_PackRoleWarns(t *testing.T) {
	c := collectiveWith(accv1alpha1.AgentRoleSpec{Role: "financial_analyst"})
	warns, err := c.ValidateCreate()
	if err != nil {
		t.Fatalf("package-provided role must be allowed, got error: %v", err)
	}
	if len(warns) == 0 {
		t.Fatal("package-provided role should emit an admission warning")
	}
	if !strings.Contains(strings.Join(warns, " "), "financial_analyst") {
		t.Errorf("warning should name the role, got %v", warns)
	}
}

// A scaling override targeting a role not declared in spec.agents is rejected.
func TestCollectiveValidate_ScalingUndeclaredTarget(t *testing.T) {
	c := collectiveWith(accv1alpha1.AgentRoleSpec{Role: "reviewer"})
	c.Spec.Scaling = &accv1alpha1.ScalingSpec{
		RoleScaling: []accv1alpha1.RoleScalingSpec{{Role: "arbiter"}},
	}
	if _, err := c.ValidateCreate(); err == nil {
		t.Fatal("scaling override for an undeclared role must be rejected")
	}
}
