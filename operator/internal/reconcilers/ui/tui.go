// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package ui

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
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const tuiComponent = "tui"

// TUIReconciler deploys the acc-tui attach pod (proposal 023 / ADR 025
// interaction plane — the power-user/ops terminal). The pod idles so an
// operator can `oc rsh deploy/<corpus>-tui acc-tui` and drive the TUI in the
// rsh TTY (a Deployment pod has no TTY of its own). The web-terminal variant
// (ttyd + oauth-proxy + Route) is a deliberate follow-up (proposal 023 §8 Q1);
// until then `oc rsh` is the attach path.
type TUIReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *TUIReconciler) Name() string { return "ui/tui" }

// Reconcile implements SubReconciler.
func (r *TUIReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	spec := corpus.Spec.TUI
	if spec == nil || (spec.Enabled != nil && !*spec.Enabled) {
		corpus.Status.TUIDeployed = false
		return reconcilers.SubResult{}, nil
	}

	name := fmt.Sprintf("%s-tui", corpus.Name)
	labels := util.CommonLabels(corpus.Name, tuiComponent, corpus.Spec.Version)
	replicas := spec.Replicas
	if replicas == 0 {
		replicas = 1
	}

	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: corpus.Namespace, Labels: labels},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(replicas),
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					SecurityContext:  &corev1.PodSecurityContext{RunAsNonRoot: ptr.To(true)},
					Containers: []corev1.Container{
						{
							Name:  "tui",
							Image: util.ComponentImage(corpus, "acc-tui", corpus.Spec.Version),
							// Idle: the TUI needs an interactive TTY, so the pod
							// stays alive and the operator attaches with
							// `oc rsh deploy/<corpus>-tui acc-tui`.
							Command: []string{"sleep", "infinity"},
							Env: []corev1.EnvVar{
								{Name: "ACC_DEPLOY_MODE", Value: string(corpus.Spec.DeployMode)},
								{Name: "ACC_NATS_URL", Value: fmt.Sprintf("nats://%s-nats:4222", corpus.Name)},
								{Name: "ACC_CORPUS_NAME", Value: corpus.Name},
							},
						},
					},
				},
			},
		},
	}

	// NOTE: when NATS NKey auth is enabled, a future slice projects the
	// "tui" NKey seed here (the seed roster already reserves it); the
	// common NKey-disabled path connects credential-less.

	result, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, deploy, func(existing client.Object) error {
		ed := existing.(*appsv1.Deployment)
		ed.Spec.Replicas = deploy.Spec.Replicas
		ed.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert tui Deployment: %w", err)
	}

	corpus.Status.TUIDeployed = true
	return reconcilers.SubResult{Progressing: result != util.UpsertResultNoop}, nil
}
