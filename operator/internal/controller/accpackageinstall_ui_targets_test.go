// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// An installed pack must reach the TUI and WebGUI pods too.
//
// The controller installed into one agent pod and deliberately skipped the UI
// pods (their images cannot run the signed install), so on bb3 an operator
// opening the TUI or WebGUI never saw a pack the corpus had installed. UI pods
// now carry a pkg-installer sidecar on the agent-core image; the controller
// installs into it, and Installed means every target pod has the pack.

package controller

import (
	"context"
	"errors"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/ui"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// uiPod builds a TUI/WebGUI pod as the ui reconcilers render it: CommonLabels
// (no agent-role label), the UI container first and the pkg-installer sidecar.
func uiPod(name, ns, corpus, component string, sidecarReady bool) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name: name, Namespace: ns,
			Labels: util.CommonLabels(corpus, component, "0.17.10"),
		},
		Spec: corev1.PodSpec{Containers: []corev1.Container{
			{Name: component},
			{Name: ui.PkgInstallerContainerName},
		}},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{Name: component, Ready: true},
				{Name: ui.PkgInstallerContainerName, Ready: sidecarReady},
			},
		},
	}
}

func podNames(pods []*corev1.Pod) []string {
	out := make([]string, 0, len(pods))
	for _, p := range pods {
		out = append(out, p.Name)
	}
	return out
}

func TestFindUIPodsSelectsTheCorpusTUIAndWebGUI(t *testing.T) {
	r := newReconciler(
		readyAgentPod("agents-0", "ws", "mortgage-corpus", "mortgage_prospect"),
		uiPod("mortgage-corpus-webgui-abc", "ws", "mortgage-corpus", "webgui", true),
		uiPod("mortgage-corpus-tui-xyz", "ws", "mortgage-corpus", "tui", true),
	)
	got, err := r.findUIPods(context.Background(), "ws", "mortgage-corpus")
	if err != nil {
		t.Fatalf("findUIPods: %v", err)
	}
	if names := strings.Join(podNames(got), ","); names != "mortgage-corpus-tui-xyz,mortgage-corpus-webgui-abc" {
		t.Fatalf("UI targets = %q, want the TUI and WebGUI pods (sorted), never the agent", names)
	}
}

func TestFindUIPodsSkipsPodsThatCannotTakeAnInstall(t *testing.T) {
	notRunning := uiPod("c-tui-pending", "ws", "c", "tui", true)
	notRunning.Status.Phase = corev1.PodPending
	terminating := uiPod("c-tui-old", "ws", "c", "tui", true)
	now := metav1.Now()
	terminating.DeletionTimestamp = &now
	terminating.Finalizers = []string{"test/keep"} // the fake client refuses a deleting object without one
	noSidecar := uiPod("c-webgui-0214", "ws", "c", "webgui", true)
	noSidecar.Spec.Containers = noSidecar.Spec.Containers[:1]
	noSidecar.Status.ContainerStatuses = noSidecar.Status.ContainerStatuses[:1]
	infra := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "c-nats-0", Namespace: "ws", Labels: util.CommonLabels("c", "nats", "0.17.10")},
		Status:     corev1.PodStatus{Phase: corev1.PodRunning, ContainerStatuses: []corev1.ContainerStatus{{Name: "nats", Ready: true}}},
	}
	r := newReconciler(
		notRunning, terminating, noSidecar, infra,
		uiPod("c-webgui-sidecar-starting", "ws", "c", "webgui", false),
		uiPod("other-tui", "ws", "other-corpus", "tui", true),
	)
	got, err := r.findUIPods(context.Background(), "ws", "c")
	if err != nil {
		t.Fatalf("findUIPods: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("selected %v; none of these pods can take an install for corpus c", podNames(got))
	}
}

// A WebGUI whose own container crash-loops still gets the pack: readiness is
// the sidecar's.
func TestFindUIPodsUsesSidecarReadinessNotPodReadiness(t *testing.T) {
	p := uiPod("c-webgui", "ws", "c", "webgui", true)
	p.Status.ContainerStatuses[0].Ready = false
	p.Status.Conditions = []corev1.PodCondition{{Type: corev1.PodReady, Status: corev1.ConditionFalse}}
	got, err := newReconciler(p).findUIPods(context.Background(), "ws", "c")
	if err != nil || len(got) != 1 {
		t.Fatalf("got %v, %v; want the pod with a Ready pkg-installer", podNames(got), err)
	}
}

// --- Reconcile: Installed means every target pod has the pack -------------

type execCall struct{ pod, container string }

func installReconciler(t *testing.T, fail map[string]bool, objs ...runtime.Object) (*AccPackageInstallReconciler, *[]execCall) {
	t.Helper()
	scheme := runtime.NewScheme()
	_ = clientgoscheme.AddToScheme(scheme)
	_ = accv1alpha1.AddToScheme(scheme)
	cr := &accv1alpha1.AccPackageInstall{
		ObjectMeta: metav1.ObjectMeta{Name: "mortgage-roles", Namespace: "ws", Generation: 1},
		Spec:       accv1alpha1.AccPackageInstallSpec{Name: "@acc/mortgage-roles", TargetCorpus: "c"},
	}
	calls := &[]execCall{}
	r := &AccPackageInstallReconciler{
		Client: fake.NewClientBuilder().WithScheme(scheme).
			WithRuntimeObjects(append(objs, cr)...).
			WithStatusSubresource(&accv1alpha1.AccPackageInstall{}).
			Build(),
		Scheme: scheme,
	}
	r.exec = func(_ context.Context, pod *corev1.Pod, container string, _ []string) (string, string, error) {
		*calls = append(*calls, execCall{pod.Name, container})
		if fail[pod.Name] {
			return "", "cosign: not found", errors.New("command terminated with exit code 1")
		}
		return `{"installed":[{"spec":"@acc/mortgage-roles","installed":"@acc/mortgage-roles@1.0.1",` +
			`"install_path":"/var/lib/acc/packages/acc/mortgage-roles/1.0.1","was_already_installed":false}],"failed":[]}`, "", nil
	}
	return r, calls
}

func reconcileInstall(t *testing.T, r *AccPackageInstallReconciler) *accv1alpha1.AccPackageInstall {
	t.Helper()
	key := types.NamespacedName{Namespace: "ws", Name: "mortgage-roles"}
	if _, err := r.Reconcile(context.Background(), ctrl.Request{NamespacedName: key}); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	got := &accv1alpha1.AccPackageInstall{}
	if err := r.Client.Get(context.Background(), key, got); err != nil {
		t.Fatalf("get: %v", err)
	}
	return got
}

func readyCondition(cr *accv1alpha1.AccPackageInstall) metav1.Condition {
	for _, c := range cr.Status.Conditions {
		if c.Type == "Ready" {
			return c
		}
	}
	return metav1.Condition{}
}

// No TUI/WebGUI: exactly the old behaviour — one exec into the agent's first
// container, the same status message.
func TestReconcileWithoutUIPodsInstallsIntoTheAgentOnly(t *testing.T) {
	agent := readyAgentPod("agents-0", "ws", "c", "mortgage_prospect")
	agent.Spec.Containers = []corev1.Container{{Name: "agent"}, {Name: "spiffe-helper"}}
	r, calls := installReconciler(t, nil, agent)

	cr := reconcileInstall(t, r)
	if len(*calls) != 1 || (*calls)[0] != (execCall{"agents-0", "agent"}) {
		t.Fatalf("exec calls = %v, want one into agents-0/agent", *calls)
	}
	if cr.Status.Phase != PhaseInstalled || cr.Status.InstalledVersion != "1.0.1" {
		t.Fatalf("phase=%q version=%q, want Installed 1.0.1", cr.Status.Phase, cr.Status.InstalledVersion)
	}
	if msg := readyCondition(cr).Message; msg != "installed via pod agents-0" {
		t.Errorf("Ready message = %q, want the unchanged agent-only message", msg)
	}
}

func TestReconcileInstallsIntoEveryUIPodSidecar(t *testing.T) {
	agent := readyAgentPod("agents-0", "ws", "c", "mortgage_prospect")
	agent.Spec.Containers = []corev1.Container{{Name: "agent"}}
	r, calls := installReconciler(t, nil, agent,
		uiPod("c-tui-1", "ws", "c", "tui", true),
		uiPod("c-webgui-1", "ws", "c", "webgui", true),
	)

	cr := reconcileInstall(t, r)
	want := []execCall{
		{"agents-0", "agent"},
		{"c-tui-1", ui.PkgInstallerContainerName},
		{"c-webgui-1", ui.PkgInstallerContainerName},
	}
	if len(*calls) != len(want) {
		t.Fatalf("exec calls = %v, want %v", *calls, want)
	}
	for i := range want {
		if (*calls)[i] != want[i] {
			t.Fatalf("exec calls = %v, want %v", *calls, want)
		}
	}
	if cr.Status.Phase != PhaseInstalled {
		t.Fatalf("phase = %q, want Installed", cr.Status.Phase)
	}
	if msg := readyCondition(cr).Message; !strings.Contains(msg, "c-tui-1") || !strings.Contains(msg, "c-webgui-1") {
		t.Errorf("Ready message %q should name the UI pods holding the pack", msg)
	}
}

// The agent has it but the WebGUI does not → not Installed.
func TestReconcileIsNotInstalledWhileAUIPodLacksThePack(t *testing.T) {
	agent := readyAgentPod("agents-0", "ws", "c", "mortgage_prospect")
	agent.Spec.Containers = []corev1.Container{{Name: "agent"}}
	r, _ := installReconciler(t, map[string]bool{"c-webgui-1": true}, agent,
		uiPod("c-webgui-1", "ws", "c", "webgui", true),
	)

	cr := reconcileInstall(t, r)
	if cr.Status.Phase != PhaseFailed {
		t.Fatalf("phase = %q, want Failed while a UI pod lacks the pack", cr.Status.Phase)
	}
	cond := readyCondition(cr)
	if cond.Status != metav1.ConditionFalse || cond.Reason != "ExecFailed" ||
		!strings.Contains(cond.Message, "c-webgui-1/"+ui.PkgInstallerContainerName) {
		t.Errorf("Ready = %+v, want False/ExecFailed naming the UI pod", cond)
	}
}
