// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"sort"
	"testing"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/rolecatalogue"
)

// TestIsKnown_LegacyRoles confirms the original ACCv3 5-role set is still
// in the catalogue. Removing any of these silently would break every
// existing AgentCollective custom resource at admission time.
func TestIsKnown_LegacyRoles(t *testing.T) {
	for _, role := range []string{
		"ingester", "analyst", "synthesizer", "arbiter", "observer",
	} {
		if !rolecatalogue.IsKnown(role) {
			t.Errorf("legacy role %q dropped from catalogue", role)
		}
	}
}

// TestIsKnown_CodingSplitPersonas confirms the D3 split-skills personas
// are admitted. Used by examples/coding_split_skills/.
func TestIsKnown_CodingSplitPersonas(t *testing.T) {
	for _, role := range []string{
		"coding_agent",
		"coding_agent_architect",
		"coding_agent_dependency",
		"coding_agent_implementer",
		"coding_agent_reviewer",
		"coding_agent_tester",
	} {
		if !rolecatalogue.IsKnown(role) {
			t.Errorf("coding-split persona %q missing from catalogue", role)
		}
	}
}

// TestIsKnown_ResearchPersonas confirms the E4 autoresearcher personas
// are admitted. Used by examples/acc_autoresearcher/.
func TestIsKnown_ResearchPersonas(t *testing.T) {
	for _, role := range []string{
		"research_planner",
		"research_strategist",
		"research_economist",
		"research_competitor",
		"research_synthesizer",
		"research_critic",
	} {
		if !rolecatalogue.IsKnown(role) {
			t.Errorf("research persona %q missing from catalogue", role)
		}
	}
}

// TestIsKnown_RejectsUnknown spot-checks a handful of plausible-but-fake
// role names so the catalogue isn't accidentally wide-open.
func TestIsKnown_RejectsUnknown(t *testing.T) {
	for _, role := range []string{
		"",
		"fnord",
		"_base",     // skipped by the generator on purpose
		"TEMPLATE",  // skipped by the generator on purpose
		"INGESTER",  // case-sensitive — uppercase variant must not match
		"researcher", // close to research_* but not present
	} {
		if rolecatalogue.IsKnown(role) {
			t.Errorf("catalogue should reject %q", role)
		}
	}
}

// TestAll_SortedAndUnique validates the public help-text accessor.
func TestAll_SortedAndUnique(t *testing.T) {
	all := rolecatalogue.All()
	if len(all) == 0 {
		t.Fatal("All() returned empty slice — catalogue is empty?")
	}
	if !sort.StringsAreSorted(all) {
		t.Errorf("All() must return sorted slice, got %v", all)
	}
	seen := map[string]struct{}{}
	for _, r := range all {
		if _, dup := seen[r]; dup {
			t.Errorf("All() returned duplicate: %q", r)
		}
		seen[r] = struct{}{}
	}
}

// TestAll_AllowsCallerMutation confirms All() returns a fresh allocation
// — the public API contract states callers may mutate the result.
func TestAll_AllowsCallerMutation(t *testing.T) {
	a := rolecatalogue.All()
	if len(a) == 0 {
		t.Skip("catalogue is empty")
	}
	a[0] = "MUTATED"
	b := rolecatalogue.All()
	if b[0] == "MUTATED" {
		t.Errorf("All() must return a fresh slice; mutation leaked")
	}
}

// TestSuggest_TypoVariants exercises the closest-match heuristic on
// realistic typos. The exact ranking can shift if the catalogue
// changes; we only assert that the *intended* role appears in the top
// suggestions.
func TestSuggest_TypoVariants(t *testing.T) {
	cases := []struct {
		input    string
		mustHave string
	}{
		{"research_plan", "research_planner"},
		{"research_economis", "research_economist"},
		{"coding_agnt_architect", "coding_agent_architect"},
		{"coding_agent_implmenter", "coding_agent_implementer"},
		{"analyzt", "analyst"},
		{"observator", "observer"},
	}
	for _, c := range cases {
		t.Run(c.input, func(t *testing.T) {
			got := rolecatalogue.Suggest(c.input, 3)
			if !contains(got, c.mustHave) {
				t.Errorf("Suggest(%q, 3) = %v; expected to contain %q",
					c.input, got, c.mustHave)
			}
		})
	}
}

// TestSuggest_NCap enforces the n cap.
func TestSuggest_NCap(t *testing.T) {
	got := rolecatalogue.Suggest("xxx", 2)
	if len(got) > 2 {
		t.Errorf("Suggest exceeded n=2 cap: got %d entries", len(got))
	}
}

// TestSuggest_ZeroN documents the n<=0 contract.
func TestSuggest_ZeroN(t *testing.T) {
	for _, n := range []int{0, -1, -100} {
		if got := rolecatalogue.Suggest("research_plan", n); got != nil {
			t.Errorf("Suggest(_, %d) = %v; want nil", n, got)
		}
	}
}

// TestSuggest_DistanceCutoff confirms genuinely unrelated input gets no
// suggestions instead of nonsense matches. The cutoff is an
// implementation choice (currently distance ≤ 8) that callers can
// observe through this contract.
func TestSuggest_DistanceCutoff(t *testing.T) {
	// 30 chars of 'q' is far enough from every real role that no
	// suggestion should fit under the distance cap.
	farInput := "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
	if got := rolecatalogue.Suggest(farInput, 5); len(got) > 0 {
		t.Errorf("Suggest(%q, 5) returned %v; expected no matches above distance cutoff",
			farInput, got)
	}
}

func contains(haystack []string, needle string) bool {
	for _, h := range haystack {
		if h == needle {
			return true
		}
	}
	return false
}
