// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for util.SelectorLabels — the version-stripped subset applied to every
// immutable Pod/Service selector.  These pin the invariant that protects the
// reconcile chain from the "`selector` does not match template `labels`" abort:
// a Deployment/StatefulSet spec.selector is immutable, so the version label must
// live in the object + pod-template labels (CommonLabels) but NOT the selector,
// or a version bump (0.1.x -> 0.2.0) would diverge the patched template labels
// from the frozen selector and the API server would reject every workload
// update from the first one onward.  Pure helpers — no envtest / cluster.
package unit_test

import (
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// TestSelectorLabels_StripsVersionKeepsRest proves the helper removes exactly
// the version label and leaves every other identity label intact.
func TestSelectorLabels_StripsVersionKeepsRest(t *testing.T) {
	full := util.CommonLabels("corpus-a", "nats", "0.2.0")
	sel := util.SelectorLabels(full)

	if _, ok := sel[accv1alpha1.LabelVersion]; ok {
		t.Errorf("selector must not carry the version label; got %v", sel)
	}
	for _, k := range []string{
		accv1alpha1.LabelManagedBy,
		accv1alpha1.LabelComponent,
		accv1alpha1.LabelCorpusName,
	} {
		if sel[k] != full[k] {
			t.Errorf("selector lost identity label %s: want %q, got %q", k, full[k], sel[k])
		}
	}
	if len(sel) != len(full)-1 {
		t.Errorf("selector should drop exactly one key (version); full=%d, sel=%d", len(full), len(sel))
	}
}

// TestSelectorLabels_AgentLabels covers the richest label set (role +
// collective-id) — only the version is dropped; role/collective survive so the
// agent Deployment selector still uniquely targets its own pods.
func TestSelectorLabels_AgentLabels(t *testing.T) {
	full := util.AgentLabels("corpus-a", "research-01", accv1alpha1.AgentRole("ingester"), "0.2.0")
	sel := util.SelectorLabels(full)

	if _, ok := sel[accv1alpha1.LabelVersion]; ok {
		t.Errorf("agent selector must not carry the version label; got %v", sel)
	}
	if sel[accv1alpha1.LabelAgentRole] != "ingester" {
		t.Errorf("agent selector lost the role label; got %v", sel)
	}
	if sel[accv1alpha1.LabelCollectiveID] != "research-01" {
		t.Errorf("agent selector lost the collective-id label; got %v", sel)
	}
}

// TestSelectorLabels_SubsetOfTemplate is the Kubernetes-level invariant: every
// selector key must appear, with the same value, in the template labels —
// otherwise the workload spec is itself invalid (selector must match template).
func TestSelectorLabels_SubsetOfTemplate(t *testing.T) {
	template := util.CommonLabels("corpus-a", "redis", "0.2.0") // what rides on the pod template
	sel := util.SelectorLabels(template)

	for k, v := range sel {
		if template[k] != v {
			t.Errorf("selector key %s=%q is not a subset of the template labels (%v)", k, v, template)
		}
	}
	// And the template must still carry the version (so version-based filtering
	// of pods keeps working) even though the selector does not.
	if template[accv1alpha1.LabelVersion] != "0.2.0" {
		t.Errorf("template labels must retain the version; got %v", template)
	}
}

// TestSelectorLabels_StableAcrossVersionBump is the regression test for the
// chain-abort root cause.  The selector the operator computes at 0.1.0 must be
// byte-for-byte identical to the one it computes at 0.2.0 — that is what makes
// the immutable spec.selector survive an OLM upgrade.  Meanwhile the template
// labels legitimately differ (the version moved).
func TestSelectorLabels_StableAcrossVersionBump(t *testing.T) {
	selOld := util.SelectorLabels(util.CommonLabels("corpus-a", "nats", "0.1.0"))
	selNew := util.SelectorLabels(util.CommonLabels("corpus-a", "nats", "0.2.0"))

	if len(selOld) != len(selNew) {
		t.Fatalf("selector key count changed across version bump: %d -> %d", len(selOld), len(selNew))
	}
	for k, v := range selOld {
		if selNew[k] != v {
			t.Errorf("selector changed across version bump at %s: %q -> %q (immutable selector would be rejected)", k, v, selNew[k])
		}
	}

	// Sanity: the *template* labels really did change, so the test is exercising
	// a genuine version delta rather than two identical inputs.
	tmplOld := util.CommonLabels("corpus-a", "nats", "0.1.0")
	tmplNew := util.CommonLabels("corpus-a", "nats", "0.2.0")
	if tmplOld[accv1alpha1.LabelVersion] == tmplNew[accv1alpha1.LabelVersion] {
		t.Fatal("test setup error: template version did not change between the two corpora")
	}
}

// TestSelectorLabels_DoesNotMutateInput guards against the helper aliasing or
// clearing the caller's label map (which is reused for the template labels).
func TestSelectorLabels_DoesNotMutateInput(t *testing.T) {
	full := util.CommonLabels("corpus-a", "otel", "0.2.0")
	before := len(full)
	_ = util.SelectorLabels(full)
	if len(full) != before {
		t.Errorf("input label map was mutated (was %d entries, now %d)", before, len(full))
	}
	if full[accv1alpha1.LabelVersion] != "0.2.0" {
		t.Error("input label map lost its version label — template labels would be corrupted")
	}
}
