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
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/nkeygen"
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
	// 0. NKey Secret (proposal 013) — generated once, never rewritten.
	// -----------------------------------------------------------------------
	nkeyPublicKeys, err := r.reconcileNKeySecret(ctx, corpus)
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("reconcile nkey secret: %w", err)
	}

	// -----------------------------------------------------------------------
	// 1. ConfigMap — nats.conf
	// -----------------------------------------------------------------------
	natsConf, err := templates.RenderNATSConfig(corpus, nkeyPublicKeys)
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
	// nil => leave storageClassName unset so the cluster default StorageClass
	// applies. Never emit the empty string, which disables dynamic provisioning.
	var storageClass *string
	if natsSpec.StorageClass != "" {
		sc := natsSpec.StorageClass
		storageClass = &sc
	}
	// Image: honor an explicit full-ref override; else derive via ComponentImage
	// (imageRepository/imageRegistry). The derived ref fails when neither hosts a
	// `nats` image — see NATSSpec.Image.
	natsImage := natsSpec.Image
	if natsImage == "" {
		natsImage = util.ComponentImage(corpus, "nats", natsSpec.Version+"-alpine")
	}
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
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					Containers: []corev1.Container{
						{
							Name:  "nats",
							Image: natsImage,
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
						StorageClassName: storageClass,
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

// reconcileNKeySecret ensures the per-corpus NKey Secret exists when
// NKey auth is enabled (proposal 013, PR-4).
//
// The Secret holds one NKey *seed* per identity (key ``seed-<identity>``)
// for the six agent roles plus ``tui`` and ``leaf``.  It is generated
// EXACTLY ONCE: if the Secret already exists this function never
// rewrites it — regenerating the seeds would invalidate every running
// pod's credential and lock the collective off its own bus.
//
// Returns the identity→public-key map (re-derived from the persisted
// seeds) for the nats.conf authorization block, or nil when NKey auth
// is disabled.
func (r *NATSReconciler) reconcileNKeySecret(
	ctx context.Context, corpus *accv1alpha1.AgentCorpus,
) (map[string]string, error) {
	natsSpec := corpus.Spec.Infrastructure.NATS
	if natsSpec.NKeyAuth == nil || !natsSpec.NKeyAuth.Enabled {
		return nil, nil
	}

	secretName := fmt.Sprintf("%s-nats-nkeys", corpus.Name)
	key := types.NamespacedName{Namespace: corpus.Namespace, Name: secretName}
	secret := &corev1.Secret{}
	err := r.Client.Get(ctx, key, secret)
	if apierrors.IsNotFound(err) {
		// First reconcile — mint the eight identities.
		data := map[string][]byte{}
		for _, identity := range templates.NKeyIdentities() {
			seed, _, genErr := nkeygen.GenerateUserNKey()
			if genErr != nil {
				return nil, fmt.Errorf("generate nkey for %s: %w", identity, genErr)
			}
			data["seed-"+identity] = []byte(seed)
		}
		secret = &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{
				Name:      secretName,
				Namespace: corpus.Namespace,
				Labels:    util.CommonLabels(corpus.Name, natsComponentName, corpus.Spec.Version),
			},
			Type: corev1.SecretTypeOpaque,
			Data: data,
		}
		if refErr := ctrl.SetControllerReference(corpus, secret, r.Scheme); refErr != nil {
			return nil, fmt.Errorf("set owner ref on nkey secret: %w", refErr)
		}
		if createErr := r.Client.Create(ctx, secret); createErr != nil {
			return nil, fmt.Errorf("create nkey secret: %w", createErr)
		}
	} else if err != nil {
		return nil, fmt.Errorf("get nkey secret: %w", err)
	}
	// NOTE: the existing-Secret branch deliberately does nothing — the
	// seeds are never rewritten once minted.

	// Re-derive the public keys from whatever seeds the Secret holds.
	publicKeys := map[string]string{}
	for _, identity := range templates.NKeyIdentities() {
		seed, ok := secret.Data["seed-"+identity]
		if !ok {
			continue
		}
		pub, derErr := nkeygen.PublicFromSeed(string(seed))
		if derErr != nil {
			return nil, fmt.Errorf("derive public key for %s: %w", identity, derErr)
		}
		publicKeys[identity] = pub
	}
	return publicKeys, nil
}
