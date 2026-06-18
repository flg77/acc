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

// Post ecosystem-split, the compiled-in catalogue holds ONLY the built-in
// CONTROL roles (the ones that always ship in-tree under roles/). Movable
// personas — analyst, synthesizer, the coding-split / research / business
// families — now install dynamically from packs and are NOT in the catalogue;
// the AgentCollective webhook admits them via its warn-and-allow path. See
// rolecatalogue + agentcollective_webhook.go (hybrid validation).

// TestIsKnown_BuiltinControlRoles confirms every built-in CONTROL role is in
// the catalogue. Dropping one silently would hard-reject a core role at
// admission time (e.g. the assistant the webhook injects into every
// collective).
func TestIsKnown_BuiltinControlRoles(t *testing.T) {
	for _, role := range []string{
		"arbiter", "assistant", "compliance_officer",
		"ingester", "observer", "orchestrator", "reviewer",
	} {
		if !rolecatalogue.IsKnown(role) {
			t.Errorf("built-in CONTROL role %q missing from catalogue", role)
		}
	}
}

// TestIsKnown_PackRolesNotBuiltin confirms movable/pack-provided personas are
// NOT in the built-in catalogue. These are admitted by the webhook with an
// admission warning (the operator can't know the installed pack set at build
// time), not by the compiled-in catalogue.
func TestIsKnown_PackRolesNotBuiltin(t *testing.T) {
	for _, role := range []string{
		"analyst", "synthesizer", // legacy ACCv3 roles, now pack-provided
		"coding_agent", "coding_agent_architect",
		"research_planner", "research_critic",
		"financial_analyst",
	} {
		if rolecatalogue.IsKnown(role) {
			t.Errorf("pack-provided role %q must not be in the built-in catalogue", role)
		}
	}
}

// TestIsKnown_RejectsUnknown spot-checks a handful of names that are neither
// built-in nor plausible pack roles so the catalogue isn't accidentally
// wide-open.
func TestIsKnown_RejectsUnknown(t *testing.T) {
	for _, role := range []string{
		"",
		"fnord",
		"_base",     // skipped by the generator on purpose
		"TEMPLATE",  // skipped by the generator on purpose
		"ARBITER",   // case-sensitive — uppercase variant must not match
		"researcher", // not a built-in role
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

// TestNearestWithin exercises the typo-vs-distinct discriminator the webhook
// uses to decide hard-error (near a built-in) vs warn-and-allow (distinct,
// plausibly package-provided).
func TestNearestWithin(t *testing.T) {
	// A near-typo of a built-in is within distance 2.
	if got := rolecatalogue.NearestWithin("revewer", 2); !contains(got, "reviewer") {
		t.Errorf(`NearestWithin("revewer", 2) = %v; want it to contain "reviewer"`, got)
	}
	// A distinct, package-provided name sits well beyond distance 2 of any
	// built-in → empty (so the webhook warns rather than hard-rejects).
	if got := rolecatalogue.NearestWithin("financial_analyst", 2); len(got) != 0 {
		t.Errorf(`NearestWithin("financial_analyst", 2) = %v; want empty`, got)
	}
	// Negative maxDist yields nil.
	if got := rolecatalogue.NearestWithin("arbiter", -1); got != nil {
		t.Errorf("NearestWithin(_, -1) = %v; want nil", got)
	}
}

// TestSuggest_TypoVariants exercises the closest-match heuristic on realistic
// typos of built-in roles. The exact ranking can shift if the catalogue
// changes; we only assert the intended role appears in the top suggestions.
func TestSuggest_TypoVariants(t *testing.T) {
	cases := []struct {
		input    string
		mustHave string
	}{
		{"arbtier", "arbiter"},
		{"complianse_officer", "compliance_officer"},
		{"orchestratr", "orchestrator"},
		{"revewer", "reviewer"},
		{"observator", "observer"},
		{"assistnt", "assistant"},
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
		if got := rolecatalogue.Suggest("arbiter", n); got != nil {
			t.Errorf("Suggest(_, %d) = %v; want nil", n, got)
		}
	}
}

// TestSuggest_DistanceCutoff confirms genuinely unrelated input gets no
// suggestions instead of nonsense matches. The cutoff is an implementation
// choice (currently distance ≤ 8) that callers can observe through this
// contract.
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
