// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package infra

import (
	"context"
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
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
	natsComponentName = "nats"
	natsPort          = 4222
	natsClusterPort   = 6222
	natsHTTPPort      = 8222
)

// NATSReconciler manages the NATS JetStream StatefulSet, headless Service,
// and ConfigMap for the ACC signal bus.
type NATSReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *NATSReconciler) Name() string { return "infra/nats" }

// Reconcile implements SubReconciler.
func (r *NATSReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	ns := corpus.Namespace
	natsSpec := corpus.Spec.Infrastructure.NATS
	labels := util.CommonLabels(corpus.Name, natsComponentName, corpus.Spec.Version)
	name := fmt.Sprintf("%s-nats", corpus.Name)

	// -----------------------------------------------------------------------
	// 1. ConfigMap — nats.conf
	// -----------------------------------------------------------------------
	natsConf, err := templates.RenderNATSConfig(corpus)
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("render nats config: %w", err)
	}
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name + "-config",
			Namespace: ns,
			Labels:    labels,
		},
		Data: map[string]string{"nats.conf": natsConf},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, cm, func(existing client.Object) error {
		existing.(*corev1.ConfigMap).Data = cm.Data
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert nats ConfigMap: %w", err)
	}

	// -----------------------------------------------------------------------
	// 2. Headless Service
	// -----------------------------------------------------------------------
	headlessSvc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
			Labels:    labels,
		},
		Spec: corev1.ServiceSpec{
			ClusterIP: "None",
			Selector:  labels,
			Ports: []corev1.ServicePort{
				{Name: "client", Port: natsPort},
				{Name: "cluster", Port: natsClusterPort},
				{Name: "http", Port: natsHTTPPort},
			},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, headlessSvc, func(existing client.Object) error {
		existing.(*corev1.Service).Spec.Ports = headlessSvc.Spec.Ports
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert nats Service: %w", err)
	}

	// -----------------------------------------------------------------------
	// 3. StatefulSet
	// -----------------------------------------------------------------------
	replicas := ptr.To(natsSpec.Replicas)
	storageClass := "" // default StorageClass
	sts := &appsv1.StatefulSet{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
			Labels:    labels,
		},
		Spec: appsv1.StatefulSetSpec{
			ServiceName: name,
			Replicas:    replicas,
			Selector:    &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					Containers: []corev1.Container{
						{
							Name:  "nats",
							Image: fmt.Sprintf("%s/nats:%s-alpine", corpus.Spec.ImageRegistry, natsSpec.Version),
							Ports: []corev1.ContainerPort{
								{Name: "client", ContainerPort: natsPort},
								{Name: "cluster", ContainerPort: natsClusterPort},
								{Name: "http", ContainerPort: natsHTTPPort},
							},
							Args: []string{
								"-c", "/etc/nats/nats.conf",
							},
							VolumeMounts: []corev1.VolumeMount{
								{Name: "config", MountPath: "/etc/nats"},
								{Name: "data", MountPath: "/data/jetstream"},
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
			VolumeClaimTemplates: []corev1.PersistentVolumeClaim{
				{
					ObjectMeta: metav1.ObjectMeta{Name: "data"},
					Spec: corev1.PersistentVolumeClaimSpec{
						StorageClassName: &storageClass,
						AccessModes:      []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
						Resources: corev1.VolumeResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceStorage: resource.MustParse(natsSpec.StorageSize),
							},
						},
					},
				},
			},
		},
	}

	result, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, sts, func(existing client.Object) error {
		existingSTS := existing.(*appsv1.StatefulSet)
		existingSTS.Spec.Replicas = sts.Spec.Replicas
		existingSTS.Spec.Template = sts.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert nats StatefulSet: %w", err)
	}

	// Update observed version in status.
	corpus.Status.Infrastructure.NATSVersion = natsSpec.Version

	progressing := result == util.UpsertResultCreated || result == util.UpsertResultUpdated
	return reconcilers.SubResult{Progressing: progressing}, nil
}
