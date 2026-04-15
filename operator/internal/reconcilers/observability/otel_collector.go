// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package observability

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
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/templates"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const (
	otelComponentName  = "otel-collector"
	otelGRPCPort       = 4317
	otelHTTPPort       = 4318
	otelMetricsPort    = 8888
)

// OTelCollectorReconciler manages an OpenTelemetry Collector Deployment
// and Service when observability.backend=otel.
type OTelCollectorReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *OTelCollectorReconciler) Name() string { return "observability/otel-collector" }

// Reconcile implements SubReconciler.
func (r *OTelCollectorReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	if corpus.Spec.Observability.Backend != accv1alpha1.MetricsBackendOTel {
		return reconcilers.SubResult{}, nil
	}

	if corpus.Spec.Observability.OTelCollector == nil {
		return reconcilers.SubResult{}, nil
	}

	ns := corpus.Namespace
	labels := util.CommonLabels(corpus.Name, otelComponentName, corpus.Spec.Version)
	name := fmt.Sprintf("%s-otel-collector", corpus.Name)

	// -----------------------------------------------------------------------
	// ConfigMap — otel-collector.yaml
	// -----------------------------------------------------------------------
	otelConf, err := templates.RenderOTelConfig(corpus)
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("render otel config: %w", err)
	}
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name + "-config",
			Namespace: ns,
			Labels:    labels,
		},
		Data: map[string]string{"otel-collector.yaml": otelConf},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, cm, func(existing client.Object) error {
		existing.(*corev1.ConfigMap).Data = cm.Data
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert otel ConfigMap: %w", err)
	}

	// -----------------------------------------------------------------------
	// Service
	// -----------------------------------------------------------------------
	svc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
			Labels:    labels,
		},
		Spec: corev1.ServiceSpec{
			Selector: labels,
			Ports: []corev1.ServicePort{
				{Name: "grpc", Port: otelGRPCPort},
				{Name: "http", Port: otelHTTPPort},
				{Name: "metrics", Port: otelMetricsPort},
			},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, svc, func(existing client.Object) error {
		existing.(*corev1.Service).Spec.Ports = svc.Spec.Ports
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert otel Service: %w", err)
	}

	// -----------------------------------------------------------------------
	// Deployment
	// -----------------------------------------------------------------------
	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(int32(1)),
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					Containers: []corev1.Container{
						{
							Name:  "otel-collector",
							Image: "otel/opentelemetry-collector-contrib:latest",
							Args:  []string{"--config=/conf/otel-collector.yaml"},
							Ports: []corev1.ContainerPort{
								{Name: "grpc", ContainerPort: otelGRPCPort},
								{Name: "http", ContainerPort: otelHTTPPort},
								{Name: "metrics", ContainerPort: otelMetricsPort},
							},
							VolumeMounts: []corev1.VolumeMount{
								{Name: "config", MountPath: "/conf"},
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
		existingDeploy := existing.(*appsv1.Deployment)
		existingDeploy.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert otel Deployment: %w", err)
	}

	return reconcilers.SubResult{Progressing: result != util.UpsertResultNoop}, nil
}
