// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

func TestSetCondition_NewCondition(t *testing.T) {
	var conds []metav1.Condition
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionTrue, "AllGood", "everything is fine")
	if len(conds) != 1 {
		t.Fatalf("expected 1 condition, got %d", len(conds))
	}
	if conds[0].Type != "Ready" || conds[0].Status != metav1.ConditionTrue {
		t.Errorf("unexpected condition: %+v", conds[0])
	}
}

func TestSetCondition_Update(t *testing.T) {
	var conds []metav1.Condition
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionTrue, "AllGood", "msg1")
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionFalse, "Broken", "msg2")
	if len(conds) != 1 {
		t.Fatalf("expected 1 condition after update, got %d", len(conds))
	}
	if conds[0].Status != metav1.ConditionFalse {
		t.Errorf("expected False after update, got %s", conds[0].Status)
	}
	if conds[0].Message != "msg2" {
		t.Errorf("expected updated message, got %q", conds[0].Message)
	}
}

func TestSetCondition_PreservesTransitionTime(t *testing.T) {
	var conds []metav1.Condition
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionTrue, "AllGood", "msg1")
	t1 := conds[0].LastTransitionTime
	// Same status — transition time should NOT change.
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionTrue, "StillGood", "msg2")
	if conds[0].LastTransitionTime != t1 {
		t.Errorf("transition time changed unexpectedly")
	}
}

func TestGetCondition(t *testing.T) {
	var conds []metav1.Condition
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionTrue, "OK", "ok")
	statuspkg.SetCondition(&conds, "InfraReady", metav1.ConditionFalse, "Pending", "pending")

	c := statuspkg.GetCondition(conds, "InfraReady")
	if c == nil {
		t.Fatal("expected to find InfraReady condition")
	}
	if c.Status != metav1.ConditionFalse {
		t.Errorf("expected False, got %s", c.Status)
	}

	missing := statuspkg.GetCondition(conds, "DoesNotExist")
	if missing != nil {
		t.Error("expected nil for missing condition")
	}
}

func TestIsConditionTrue(t *testing.T) {
	var conds []metav1.Condition
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionTrue, "OK", "")
	if !statuspkg.IsConditionTrue(conds, "Ready") {
		t.Error("expected IsConditionTrue=true")
	}
	if statuspkg.IsConditionTrue(conds, "Missing") {
		t.Error("expected IsConditionTrue=false for missing condition")
	}
}

func TestRemoveCondition(t *testing.T) {
	var conds []metav1.Condition
	statuspkg.SetCondition(&conds, "Ready", metav1.ConditionTrue, "OK", "")
	statuspkg.SetCondition(&conds, "InfraReady", metav1.ConditionTrue, "OK", "")
	statuspkg.RemoveCondition(&conds, "Ready")
	if len(conds) != 1 {
		t.Fatalf("expected 1 condition after remove, got %d", len(conds))
	}
	if conds[0].Type != "InfraReady" {
		t.Errorf("wrong condition remaining: %s", conds[0].Type)
	}
}
