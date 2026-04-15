// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package governance

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
	opaBundleComponentName = "opa-bundle-server"
	opaBundlePort          = 8181
)

// OPABundleServerReconciler manages the Category-B OPA bundle server
// Deployment, Service, and PVC.
type OPABundleServerReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *OPABundleServerReconciler) Name() string { return "governance/opa-bundle-server" }

// Reconcile implements SubReconciler.
func (r *OPABundleServerReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	ns := corpus.Namespace
	catB := corpus.Spec.Governance.CategoryB
	labels := util.CommonLabels(corpus.Name, opaBundleComponentName, corpus.Spec.Version)
	name := fmt.Sprintf("%s-opa-bundle", corpus.Name)

	// -----------------------------------------------------------------------
	// PVC for bundle storage
	// -----------------------------------------------------------------------
	pvc := &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
			Labels:    labels,
		},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
			Resources: corev1.VolumeResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceStorage: resource.MustParse(catB.BundlePVCSize),
				},
			},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, pvc, func(existing client.Object) error {
		// PVC spec is mostly immutable; only update labels.
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert opa-bundle PVC: %w", err)
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
				{Name: "http", Port: opaBundlePort},
			},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, svc, func(existing client.Object) error {
		existing.(*corev1.Service).Spec.Ports = svc.Spec.Ports
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert opa-bundle Service: %w", err)
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
							Name:  "opa",
							Image: catB.BundleServerImage,
							Args: []string{
								"run", "--server",
								fmt.Sprintf("--addr=0.0.0.0:%d", opaBundlePort),
								"--bundle", "/bundles",
							},
							Ports: []corev1.ContainerPort{
								{Name: "http", ContainerPort: opaBundlePort},
							},
							VolumeMounts: []corev1.VolumeMount{
								{Name: "bundles", MountPath: "/bundles"},
							},
							Env: []corev1.EnvVar{
								{
									Name:  "ACC_BUNDLE_POLL_INTERVAL",
									Value: fmt.Sprintf("%d", catB.PollIntervalSeconds),
								},
							},
						},
					},
					Volumes: []corev1.Volume{
						{
							Name: "bundles",
							VolumeSource: corev1.VolumeSource{
								PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
									ClaimName: name,
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
		return reconcilers.SubResult{}, fmt.Errorf("upsert opa-bundle Deployment: %w", err)
	}

	return reconcilers.SubResult{Progressing: result != util.UpsertResultNoop}, nil
}
