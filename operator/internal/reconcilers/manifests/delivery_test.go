// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package manifests

import (
	"strings"
	"testing"
)

// TestWalkTree_Roles confirms the embedded roles tree carries every
// persona shipped under roles/. Specific spot-checks: a legacy 5-role
// member, a new D3 split-skills persona, and a new E4 research persona.
func TestWalkTree_Roles(t *testing.T) {
	data, err := walkTree(embedRoles, "data/roles")
	if err != nil {
		t.Fatalf("walkTree: %v", err)
	}
	if len(data) == 0 {
		t.Fatalf("roles tree is empty — did `make sync-manifests` run?")
	}

	for _, want := range []string{
		"analyst__role.yaml",                  // legacy
		"coding_agent_implementer__role.yaml", // D3
		"research_planner__role.yaml",         // E4
	} {
		if _, ok := data[want]; !ok {
			t.Errorf("roles ConfigMap missing key %q", want)
		}
	}
}

// TestWalkTree_NoForbiddenKeys checks the round-trip safety invariant:
// no flattened key should collide with the "__" path separator.
func TestWalkTree_NoForbiddenKeys(t *testing.T) {
	for _, plan := range []struct {
		name string
		root string
		fs   func() (map[string]string, error)
	}{
		{"roles", "data/roles", func() (map[string]string, error) { return walkTree(embedRoles, "data/roles") }},
		{"skills", "data/skills", func() (map[string]string, error) { return walkTree(embedSkills, "data/skills") }},
		{"mcps", "data/mcps", func() (map[string]string, error) { return walkTree(embedMCPs, "data/mcps") }},
	} {
		t.Run(plan.name, func(t *testing.T) {
			data, err := plan.fs()
			if err != nil {
				t.Fatal(err)
			}
			for k := range data {
				// Reverse the flatten and assert the resulting path is
				// the original (no spurious "/" introductions).
				orig := UnflattenKey(k)
				if strings.Contains(orig, PathSeparator) {
					t.Errorf("key %q unflattens to %q which still contains the separator — round-trip is unsafe",
						k, orig)
				}
			}
		})
	}
}

// TestRoundTrip exercises FlattenPath / UnflattenKey against explicit
// inputs, including paths with multiple separators and edge cases.
func TestRoundTrip(t *testing.T) {
	cases := []string{
		"role.yaml",
		"coding_agent_implementer/role.yaml",
		"coding_agent_implementer/system_prompt.md",
		"echo/skill.yaml",
		"web_search_brave/mcp.yaml",
		"a/b/c/d/e.txt",
	}
	for _, c := range cases {
		t.Run(c, func(t *testing.T) {
			flat := FlattenPath(c)
			if strings.Contains(flat, "/") {
				t.Errorf("FlattenPath(%q) = %q still contains '/'", c, flat)
			}
			back := UnflattenKey(flat)
			if back != c {
				t.Errorf("round-trip: %q → %q → %q (mismatch)", c, flat, back)
			}
		})
	}
}

// TestConfigMapName confirms the corpus-scoped naming contract.
func TestConfigMapName(t *testing.T) {
	rolesS, skillsS, mcpsS := Suffixes()
	if rolesS != "acc-roles" || skillsS != "acc-skills" || mcpsS != "acc-mcps" {
		t.Errorf("suffixes drifted: %q %q %q", rolesS, skillsS, mcpsS)
	}
}
