// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Regression tests for proposal 032 §11 Finding B-2.
//
// util.Upsert used to return UpsertResultUpdated unconditionally on the patch
// path — it never returned Noop for an unchanged resource. Every workload
// reconciler keys Progressing off `result != UpsertResultNoop`, so that was
// always-true: IsProgressing stayed true and the corpus could never reach Ready
// (it pinned at Progressing with stable generations). Upsert must return Noop
// when the merge patch is empty.
package unit_test

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

func upsertDeploy(name string, replicas int32) *appsv1.Deployment {
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "acc-system"},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(replicas),
			Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"app": name}},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{"app": name}},
				Spec:       corev1.PodSpec{Containers: []corev1.Container{{Name: "c", Image: "img:1"}}},
			},
		},
	}
}

// Unchanged resource => Noop (the whole point: don't report Progressing forever).
func TestUpsert_NoopWhenUnchanged(t *testing.T) {
	d := upsertDeploy("u-noop", 1)
	c := kserveClient(t, d)
	res, err := util.Upsert(context.Background(), c, nil, nil, d.DeepCopy(),
		func(existing client.Object) error { return nil }) // no change
	if err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if res != util.UpsertResultNoop {
		t.Fatalf("expected UpsertResultNoop for an unchanged resource, got %v", res)
	}
}

// Changed spec => Updated.
func TestUpsert_UpdatedWhenChanged(t *testing.T) {
	d := upsertDeploy("u-chg", 1)
	c := kserveClient(t, d)
	res, err := util.Upsert(context.Background(), c, nil, nil, d.DeepCopy(),
		func(existing client.Object) error {
			existing.(*appsv1.Deployment).Spec.Replicas = ptr.To(int32(3))
			return nil
		})
	if err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if res != util.UpsertResultUpdated {
		t.Fatalf("expected UpsertResultUpdated when spec changed, got %v", res)
	}
}

// Absent resource => Created.
func TestUpsert_CreatedWhenAbsent(t *testing.T) {
	c := kserveClient(t)
	d := upsertDeploy("u-new", 1)
	res, err := util.Upsert(context.Background(), c, nil, nil, d,
		func(existing client.Object) error { return nil })
	if err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if res != util.UpsertResultCreated {
		t.Fatalf("expected UpsertResultCreated for an absent resource, got %v", res)
	}
}
