// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// findAccPod must select on the label ACC actually puts on pods.
//
// It selected on "acc.redhat.io/corpus", which nothing sets: util.CommonLabels
// -- the helper every pod-producing reconciler goes through -- writes
// LabelCorpusName ("acc.redhat.io/corpus-name"). The effect was that setting
// AccPackageInstall.spec.targetCorpus, a documented field, guaranteed the
// install could never find a pod. It failed with "no ready ACC agent pod",
// which reads like the pods are unhealthy rather than like the selector is
// wrong, and it failed just as hard with five Running+Ready agents.
//
// Found deploying @acc/mortgage-roles to bb3 (WS-02).

package controller

import (
	"context"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// readyAgentPod builds a pod labelled exactly as the collective reconciler
// labels one: CommonLabels (which is where the corpus label comes from) plus
// the agent-role label that marks it as an agent rather than infrastructure.
func readyAgentPod(name, ns, corpus, role string) *corev1.Pod {
	labels := util.CommonLabels(corpus, role, "0.17.9")
	labels[accv1alpha1.LabelAgentRole] = role
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns, Labels: labels},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			Conditions: []corev1.PodCondition{
				{Type: corev1.PodReady, Status: corev1.ConditionTrue},
			},
		},
	}
}

func newReconciler(objs ...runtime.Object) *AccPackageInstallReconciler {
	scheme := runtime.NewScheme()
	_ = clientgoscheme.AddToScheme(scheme)
	_ = accv1alpha1.AddToScheme(scheme)
	return &AccPackageInstallReconciler{
		Client: fake.NewClientBuilder().
			WithScheme(scheme).
			WithRuntimeObjects(objs...).
			Build(),
	}
}

func TestFindAccPodMatchesTheLabelTheOperatorActuallySets(t *testing.T) {
	pod := readyAgentPod("agents-0", "acc-workshop", "mortgage-corpus", "mortgage_prospect")
	r := newReconciler(pod)

	got, err := r.findAccPod(context.Background(), "acc-workshop", "mortgage-corpus")
	if err != nil {
		t.Fatalf("targetCorpus set and a Ready agent pod present, but: %v", err)
	}
	if got.Name != "agents-0" {
		t.Fatalf("found %q, want agents-0", got.Name)
	}
}

func TestFindAccPodIgnoresAnotherCorpusInTheSameNamespace(t *testing.T) {
	mine := readyAgentPod("mine-0", "shared", "my-corpus", "mortgage_prospect")
	theirs := readyAgentPod("theirs-0", "shared", "other-corpus", "assistant")
	r := newReconciler(theirs, mine)

	got, err := r.findAccPod(context.Background(), "shared", "my-corpus")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Name != "mine-0" {
		t.Fatalf("selected %q from another corpus; targetCorpus must scope the search", got.Name)
	}
}

func TestFindAccPodSkipsInfrastructurePods(t *testing.T) {
	// NATS/Redis/OTel carry the corpus label but no agent-role label, and have
	// neither the acc source nor the venv interpreter the pkg-install exec
	// needs -- exec'ing into one fails with exit 127 rather than anything
	// legible.
	infra := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name: "corpus-nats-0", Namespace: "acc-workshop",
			Labels: util.CommonLabels("mortgage-corpus", "nats", "0.17.9"),
		},
		Status: corev1.PodStatus{
			Phase:      corev1.PodRunning,
			Conditions: []corev1.PodCondition{{Type: corev1.PodReady, Status: corev1.ConditionTrue}},
		},
	}
	r := newReconciler(infra)

	if _, err := r.findAccPod(context.Background(), "acc-workshop", "mortgage-corpus"); err == nil {
		t.Fatal("selected an infrastructure pod; only agent pods can run pkg-install")
	}
}

func TestFindAccPodRejectsAPodThatIsNotReady(t *testing.T) {
	pod := readyAgentPod("agents-0", "acc-workshop", "mortgage-corpus", "mortgage_prospect")
	pod.Status.Conditions = []corev1.PodCondition{
		{Type: corev1.PodReady, Status: corev1.ConditionFalse},
	}
	r := newReconciler(pod)

	if _, err := r.findAccPod(context.Background(), "acc-workshop", "mortgage-corpus"); err == nil {
		t.Fatal("selected a pod that is not Ready")
	}
}
