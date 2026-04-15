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
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const (
	redisComponentName = "redis"
	redisPort          = 6379
)

// RedisReconciler manages the Redis StatefulSet and Service.
// Replicas=1 → standalone; Replicas=3 → Sentinel (managed by operator).
type RedisReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *RedisReconciler) Name() string { return "infra/redis" }

// Reconcile implements SubReconciler.
func (r *RedisReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	ns := corpus.Namespace
	redisSpec := corpus.Spec.Infrastructure.Redis
	labels := util.CommonLabels(corpus.Name, redisComponentName, corpus.Spec.Version)
	name := fmt.Sprintf("%s-redis", corpus.Name)

	// -----------------------------------------------------------------------
	// Service (ClusterIP)
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
				{Name: "redis", Port: redisPort},
			},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, svc, func(existing client.Object) error {
		existing.(*corev1.Service).Spec.Ports = svc.Spec.Ports
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert redis Service: %w", err)
	}

	// -----------------------------------------------------------------------
	// StatefulSet
	// -----------------------------------------------------------------------
	storageClass := ""
	replicas := ptr.To(redisSpec.Replicas)
	image := fmt.Sprintf("%s/redis:%s-alpine", corpus.Spec.ImageRegistry, redisSpec.Version)

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
							Name:  "redis",
							Image: image,
							Ports: []corev1.ContainerPort{
								{Name: "redis", ContainerPort: redisPort},
							},
							VolumeMounts: []corev1.VolumeMount{
								{Name: "data", MountPath: "/data"},
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
								corev1.ResourceStorage: resource.MustParse(redisSpec.StorageSize),
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
		return reconcilers.SubResult{}, fmt.Errorf("upsert redis StatefulSet: %w", err)
	}

	corpus.Status.Infrastructure.RedisVersion = redisSpec.Version

	progressing := result == util.UpsertResultCreated || result == util.UpsertResultUpdated
	return reconcilers.SubResult{Progressing: progressing}, nil
}
