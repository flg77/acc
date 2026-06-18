// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package bridge

import (
	"context"
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const runtimeEvidenceBridgeComponentName = "runtime-evidence-bridge"

// ConditionRuntimeEvidenceReady is the status condition this
// reconciler owns (proposal 015).
const ConditionRuntimeEvidenceReady = "RuntimeEvidenceReady"

// Process/file evidence backends, in auto-selection preference order
// (RHACS — Red Hat's own product — first).
const (
	backendNone     = "none"
	backendRHACS    = "rhacs"
	backendFalco    = "falco"
	backendTetragon = "tetragon"
)

// RuntimeEvidenceBridgeReconciler manages the runtime-evidence bridge
// Deployment + ConfigMap (proposal 015).
//
// The bridge consumes whichever runtime-security backend the cluster
// runs (RHACS / Falco / Tetragon for process+file events, NetObserv
// for network-connect events), normalises events into KERNEL_EVENT
// signals, and publishes them on NATS.  It is the only privileged /
// credentialed component — agent pods are unchanged.
//
// The bridge is created only when spec.governance.runtimeEvidence is
// enabled AND at least one process/file backend is detected.  ACC
// never installs a runtime-security tool — it adapts to what is there.
type RuntimeEvidenceBridgeReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *RuntimeEvidenceBridgeReconciler) Name() string { return "bridge/runtime-evidence" }

// SelectBackend picks the process/file evidence backend from the
// detected prerequisites, honouring an explicit preference.  Returns
// backendNone when nothing usable is present.  Exported for tests.
func SelectBackend(pre accv1alpha1.PrerequisiteStatus, preferred string) string {
	avail := map[string]bool{
		backendRHACS:    pre.RHACSInstalled,
		backendFalco:    pre.FalcoInstalled,
		backendTetragon: pre.TetragonInstalled,
	}
	if preferred != "" && preferred != "auto" && avail[preferred] {
		return preferred
	}
	// auto — RHACS (Red Hat-native) > Falco (CNCF incumbent) > Tetragon.
	for _, b := range []string{backendRHACS, backendFalco, backendTetragon} {
		if avail[b] {
			return b
		}
	}
	return backendNone
}

// Reconcile implements SubReconciler.
func (r *RuntimeEvidenceBridgeReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	re := corpus.Spec.Governance.RuntimeEvidence

	// Opt-in gate: nil or disabled → emit nothing.
	if re == nil || !re.Enabled {
		corpus.Status.RuntimeEvidence = accv1alpha1.RuntimeEvidenceStatus{
			ActiveBackend: backendNone, NetworkSource: "none",
		}
		status.SetCondition(&corpus.Status.Conditions, ConditionRuntimeEvidenceReady,
			metav1.ConditionTrue, "Disabled",
			"runtime evidence is disabled (governance.runtimeEvidence.enabled=false)")
		return reconcilers.SubResult{}, nil
	}

	// Standalone has no privileged-DaemonSet eBPF attach path.
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeStandalone {
		corpus.Status.RuntimeEvidence = accv1alpha1.RuntimeEvidenceStatus{
			ActiveBackend: backendNone, NetworkSource: "none",
		}
		status.SetCondition(&corpus.Status.Conditions, ConditionRuntimeEvidenceReady,
			metav1.ConditionTrue, "NotApplicableStandalone",
			"deployMode=standalone has no privileged-DaemonSet eBPF attach "+
				"path; runtime evidence is not applicable")
		return reconcilers.SubResult{}, nil
	}

	backend := SelectBackend(corpus.Status.Prerequisites, re.PreferredBackend)
	networkSource := "none"
	if corpus.Status.Prerequisites.NetObservInstalled {
		networkSource = "netobserv"
	}

	// No process/file backend detected — Cat-A stays metadata-only.
	if backend == backendNone {
		corpus.Status.RuntimeEvidence = accv1alpha1.RuntimeEvidenceStatus{
			ActiveBackend: backendNone, NetworkSource: networkSource,
			BridgeReady: false, Enforcing: false,
		}
		status.SetCondition(&corpus.Status.Conditions, ConditionRuntimeEvidenceReady,
			metav1.ConditionFalse, "NoBackendDetected",
			"runtime evidence is enabled but no process/file backend "+
				"(RHACS/Falco/Tetragon) was detected — Cat-A stays "+
				"metadata-only. Install a runtime-security tool.")
		return reconcilers.SubResult{}, nil
	}

	labels := util.CommonLabels(corpus.Name, runtimeEvidenceBridgeComponentName, corpus.Spec.Version)
	name := fmt.Sprintf("%s-runtime-evidence-bridge", corpus.Name)
	ns := corpus.Namespace
	natsURL := fmt.Sprintf("nats://%s-nats:4222", corpus.Name)

	// ConfigMap — bridge configuration.
	configData := fmt.Sprintf(`
backend: %q
network_source: %q
nats_url: %q
enforce: %t
`, backend, networkSource, natsURL, re.Enforce)
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name: name + "-config", Namespace: ns, Labels: labels,
		},
		Data: map[string]string{"bridge.yaml": configData},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, cm, func(existing client.Object) error {
		existing.(*corev1.ConfigMap).Data = cm.Data
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert runtime-evidence-bridge ConfigMap: %w", err)
	}

	// Deployment — single replica; the only privileged component.
	image := util.ComponentImage(corpus, "acc-runtime-evidence-bridge", corpus.Spec.Version)
	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name: name, Namespace: ns, Labels: labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(int32(1)),
			Selector: &metav1.LabelSelector{MatchLabels: util.SelectorLabels(labels)},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					Containers: []corev1.Container{
						{
							Name:  "runtime-evidence-bridge",
							Image: image,
							Env: []corev1.EnvVar{
								{Name: "ACC_RUNTIME_BACKEND", Value: backend},
								{Name: "ACC_RUNTIME_NETWORK_SOURCE", Value: networkSource},
								{Name: "ACC_RUNTIME_ENFORCE", Value: fmt.Sprintf("%t", re.Enforce)},
								{Name: "ACC_NATS_URL", Value: natsURL},
								{Name: "ACC_CORPUS_NAME", Value: corpus.Name},
							},
							VolumeMounts: []corev1.VolumeMount{
								{Name: "config", MountPath: "/etc/acc-bridge"},
							},
						},
					},
					Volumes: []corev1.Volume{
						{
							Name: "config",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{
										Name: name + "-config",
									},
								},
							},
						},
					},
				},
			},
		},
	}
	result, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, deploy, func(existing client.Object) error {
		existing.(*appsv1.Deployment).Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert runtime-evidence-bridge Deployment: %w", err)
	}

	corpus.Status.RuntimeEvidence = accv1alpha1.RuntimeEvidenceStatus{
		ActiveBackend: backend,
		NetworkSource: networkSource,
		BridgeReady:   true,
		Enforcing:     re.Enforce,
	}
	reason, msg := "Ready", fmt.Sprintf(
		"runtime-evidence bridge active — backend=%s network=%s, enforcing",
		backend, networkSource)
	if !re.Enforce {
		reason, msg = "ObserveMode", fmt.Sprintf(
			"runtime-evidence bridge active in OBSERVE mode — backend=%s "+
				"network=%s; kernel violations are logged, not blocked. "+
				"Recommended observe window: %d days.",
			backend, networkSource, re.ObserveWindowDays)
	}
	status.SetCondition(&corpus.Status.Conditions, ConditionRuntimeEvidenceReady,
		metav1.ConditionTrue, reason, msg)
	return reconcilers.SubResult{Progressing: result != util.UpsertResultNoop}, nil
}
