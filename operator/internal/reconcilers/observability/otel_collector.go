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
	"crypto/sha256"
	"encoding/hex"
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
	otelComponentName = "otel-collector"
	otelGRPCPort      = 4317
	otelHTTPPort      = 4318
	otelMetricsPort   = 8889 // 8889: the collector's OWN telemetry binds :8888 since ~v0.118

	// defaultOTelCollectorImage is a pinned contrib build mirrored into the
	// ACC image repository (build chain: skopeo copy from
	// docker.io/otel/opentelemetry-collector-contrib,
	// pushed --format v2s2 --compression-format gzip — the 0.116.0 mirror
	// shipped via plain podman push was corrupt at the exec layer). Keep the tag in sync
	// with the exporter set used in templates/otel_config.go.
	defaultOTelCollectorImage = "quay.io/flg77/acc_images:otel-collector-contrib-0.119.0"

	// OTelConfigHashAnnotation on the collector's pod template carries the
	// SHA-256 of the rendered otel-collector.yaml. The collector reads its
	// config once, at start: a changed ConfigMap reaches the volume within a
	// minute and changes nothing in the running process. On bb3 every config
	// change needed a hand `rollout restart` -- the experiment-id header
	// (0.2.18 era), the RHOAI auth block (0.2.23), the MLflow filter (0.2.24)
	// each sat unused in the ConfigMap until someone noticed. With the hash in
	// the pod template a new config IS a new template, and the Deployment
	// rolls on its own.
	OTelConfigHashAnnotation = "acc.redhat.io/otel-config-sha256"
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
	// Edge mode: OTel Collector requires a persistent network connection to
	// the observability stack which may not be available at the edge.
	// Agents default to the "log" metrics backend in edge mode.
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge {
		return reconcilers.SubResult{}, nil
	}

	if corpus.Spec.Observability.Backend != accv1alpha1.MetricsBackendOTel {
		return reconcilers.SubResult{}, nil
	}

	if corpus.Spec.Observability.OTelCollector == nil {
		return reconcilers.SubResult{}, nil
	}

	ns := corpus.Namespace
	labels := util.CommonLabels(corpus.Name, otelComponentName, corpus.Spec.Version)
	name := util.OTelCollectorServiceName(corpus.Name)

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
			Selector: util.SelectorLabels(labels),
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
	// Service CA — for mlflowAuth: kubernetes (RHOAI's MLflow answers on
	// https with a certificate from the OpenShift service CA). An empty
	// ConfigMap with the inject-cabundle annotation; the service-ca
	// operator fills service-ca.crt and keeps it current. The upsert never
	// writes data, so the injected bundle survives reconciles.
	// -----------------------------------------------------------------------
	kubeAuth := corpus.Spec.Observability.OTelCollector.MLflowAuth == accv1alpha1.MLflowAuthKubernetes
	if kubeAuth {
		caCM := &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{
				Name:        name + "-service-ca",
				Namespace:   ns,
				Labels:      labels,
				Annotations: map[string]string{"service.beta.openshift.io/inject-cabundle": "true"},
			},
		}
		if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, caCM, func(existing client.Object) error {
			cm := existing.(*corev1.ConfigMap)
			if cm.Annotations == nil {
				cm.Annotations = map[string]string{}
			}
			cm.Annotations["service.beta.openshift.io/inject-cabundle"] = "true"
			return nil
		}); err != nil {
			return reconcilers.SubResult{}, fmt.Errorf("upsert otel service-ca ConfigMap: %w", err)
		}
	}

	// -----------------------------------------------------------------------
	// Deployment
	// -----------------------------------------------------------------------
	// Pinned by default: ":latest" contrib builds reject the rendered config
	// (the `logging` exporter was removed upstream) and crash-loop. The
	// default is mirrored into the ACC image repository so RHOAI clusters
	// don't pull from docker.io; spec.observability.otelCollector.image
	// overrides for disconnected clusters.
	image := corpus.Spec.Observability.OTelCollector.Image
	if image == "" {
		image = defaultOTelCollectorImage
	}
	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(int32(1)),
			Selector: &metav1.LabelSelector{MatchLabels: util.SelectorLabels(labels)},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels:      labels,
					Annotations: map[string]string{OTelConfigHashAnnotation: OTelConfigHash(otelConf)},
				},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					Containers: []corev1.Container{
						{
							Name:  "otel-collector",
							Image: image,
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

	if kubeAuth {
		c := &deploy.Spec.Template.Spec.Containers[0]
		c.VolumeMounts = append(c.VolumeMounts, corev1.VolumeMount{Name: "service-ca", MountPath: "/etc/acc/service-ca", ReadOnly: true})
		deploy.Spec.Template.Spec.Volumes = append(deploy.Spec.Template.Spec.Volumes, corev1.Volume{
			Name: "service-ca",
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: name + "-service-ca"},
				},
			},
		})
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

// OTelConfigHash is the value of OTelConfigHashAnnotation for a rendered config.
func OTelConfigHash(conf string) string {
	sum := sha256.Sum256([]byte(conf))
	return hex.EncodeToString(sum[:])
}
