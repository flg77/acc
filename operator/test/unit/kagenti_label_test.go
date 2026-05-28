// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the Kagenti AgentCard auto-discovery label helpers — Phase 1 of
// OpenSpec 20260527-agentcard-discovery.  No envtest / cluster — pure helpers.

package unit_test

import (
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// kagentiCollective returns a minimal AgentCollective with a controllable
// KagentiSpec.  Pass nil to omit the field entirely (default-off case).
func kagentiCollective(kag *accv1alpha1.KagentiSpec) *accv1alpha1.AgentCollective {
	return &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "research", Namespace: "test-ns"},
		Spec: accv1alpha1.AgentCollectiveSpec{
			CollectiveID: "research-01",
			Kagenti:      kag,
		},
	}
}

// -----------------------------------------------------------------------
// KagentiEnabled — predicate
// -----------------------------------------------------------------------

func TestKagentiEnabled_NilSpec(t *testing.T) {
	if collective.KagentiEnabled(kagentiCollective(nil)) {
		t.Error("nil KagentiSpec should not be enabled")
	}
}

func TestKagentiEnabled_NilCollective(t *testing.T) {
	if collective.KagentiEnabled(nil) {
		t.Error("nil collective should not be enabled")
	}
}

func TestKagentiEnabled_ExplicitlyDisabled(t *testing.T) {
	if collective.KagentiEnabled(kagentiCollective(&accv1alpha1.KagentiSpec{Enabled: false})) {
		t.Error("Enabled: false should not be enabled")
	}
}

func TestKagentiEnabled_True(t *testing.T) {
	if !collective.KagentiEnabled(kagentiCollective(&accv1alpha1.KagentiSpec{Enabled: true})) {
		t.Error("Enabled: true should be enabled")
	}
}

// -----------------------------------------------------------------------
// KagentiAgentLabel — the literal label map
// -----------------------------------------------------------------------

func TestKagentiAgentLabel(t *testing.T) {
	got := util.KagentiAgentLabel()
	if len(got) != 1 {
		t.Fatalf("expected exactly one label, got %d (%v)", len(got), got)
	}
	if v := got[accv1alpha1.LabelKagentiType]; v != accv1alpha1.LabelKagentiTypeAgent {
		t.Errorf("expected %s=%s, got %s=%s",
			accv1alpha1.LabelKagentiType, accv1alpha1.LabelKagentiTypeAgent,
			accv1alpha1.LabelKagentiType, v)
	}
	// Pin the exact wire key+value Kagenti's operator watches for.
	if got["kagenti.io/type"] != "agent" {
		t.Errorf("expected kagenti.io/type=agent, got %v", got)
	}
}

// -----------------------------------------------------------------------
// AgentObjectLabels — opt-in merge with the canonical selector-safe set
// -----------------------------------------------------------------------

func canonicalAgentLabels() map[string]string {
	return map[string]string{
		accv1alpha1.LabelManagedBy:    accv1alpha1.LabelManagedByVal,
		accv1alpha1.LabelComponent:    "ingester",
		accv1alpha1.LabelAgentRole:    "ingester",
		accv1alpha1.LabelCollectiveID: "research-01",
	}
}

func TestAgentObjectLabels_DisabledReturnsCanonicalSet(t *testing.T) {
	in := canonicalAgentLabels()
	out := collective.AgentObjectLabels(kagentiCollective(nil), in)
	if _, ok := out["kagenti.io/type"]; ok {
		t.Errorf("disabled: kagenti.io/type must not appear; got %v", out)
	}
	if len(out) != len(in) {
		t.Errorf("disabled: label set size changed (in=%d, out=%d)", len(in), len(out))
	}
}

func TestAgentObjectLabels_EnabledMergesKagentiLabel(t *testing.T) {
	in := canonicalAgentLabels()
	out := collective.AgentObjectLabels(
		kagentiCollective(&accv1alpha1.KagentiSpec{Enabled: true}), in,
	)
	if got := out["kagenti.io/type"]; got != "agent" {
		t.Errorf("enabled: expected kagenti.io/type=agent, got %q", got)
	}
	// Canonical labels survive the merge.
	for k, v := range in {
		if out[k] != v {
			t.Errorf("enabled: canonical label %s lost or changed (want %q, got %q)", k, v, out[k])
		}
	}
}

func TestAgentObjectLabels_DoesNotMutateInput(t *testing.T) {
	in := canonicalAgentLabels()
	original := len(in)
	_ = collective.AgentObjectLabels(
		kagentiCollective(&accv1alpha1.KagentiSpec{Enabled: true}), in,
	)
	if len(in) != original {
		t.Errorf("input label map was mutated (was %d entries, now %d)", original, len(in))
	}
	if _, ok := in["kagenti.io/type"]; ok {
		t.Error("input label map gained the kagenti label — must remain selector-safe")
	}
}
