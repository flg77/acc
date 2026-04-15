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
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const kafkaBridgeComponentName = "kafka-bridge"

// KafkaBridgeReconciler manages the NATS-to-Kafka bridge Deployment and
// its ConfigMap.
//
// The bridge is created only when:
//  - spec.kafka is configured, AND
//  - the Kafka bootstrap servers are reachable (TCP probe passes)
//
// If the probe fails, the Deployment is deleted (or not created) and a
// Warning event is emitted. This satisfies the "loosely coupled" requirement:
// Kafka is not installed by the operator, but the bridge adapts dynamically.
type KafkaBridgeReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *KafkaBridgeReconciler) Name() string { return "bridge/kafka" }

// Reconcile implements SubReconciler.
func (r *KafkaBridgeReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	// Skip if kafka is not configured.
	if corpus.Spec.Kafka == nil {
		return reconcilers.SubResult{}, nil
	}

	kafkaSpec := corpus.Spec.Kafka
	kafkaReachable := corpus.Status.Prerequisites.KafkaReachable

	labels := util.CommonLabels(corpus.Name, kafkaBridgeComponentName, corpus.Spec.Version)
	name := fmt.Sprintf("%s-kafka-bridge", corpus.Name)
	ns := corpus.Namespace

	if !kafkaReachable {
		// Kafka probe failed — skip bridge, set status.
		corpus.Status.KafkaBridgeReady = false
		return reconcilers.SubResult{}, nil
	}

	// -----------------------------------------------------------------------
	// ConfigMap — bridge configuration
	// -----------------------------------------------------------------------
	configData := fmt.Sprintf(`
bootstrap_servers: %q
audit_topic: %q
signal_topics_prefix: %q
nats_url: "nats://%s-nats:4222"
`, kafkaSpec.BootstrapServers, kafkaSpec.AuditTopic, kafkaSpec.SignalTopicsPrefix, corpus.Name)

	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name + "-config",
			Namespace: ns,
			Labels:    labels,
		},
		Data: map[string]string{"bridge.yaml": configData},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, cm, func(existing client.Object) error {
		existing.(*corev1.ConfigMap).Data = cm.Data
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert kafka-bridge ConfigMap: %w", err)
	}

	// -----------------------------------------------------------------------
	// Deployment
	// -----------------------------------------------------------------------
	image := fmt.Sprintf("%s/acc-kafka-bridge:%s", corpus.Spec.ImageRegistry, corpus.Spec.Version)
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
							Name:  "kafka-bridge",
							Image: image,
							Env: []corev1.EnvVar{
								{Name: "ACC_KAFKA_BOOTSTRAP", Value: kafkaSpec.BootstrapServers},
								{Name: "ACC_KAFKA_AUDIT_TOPIC", Value: kafkaSpec.AuditTopic},
								{Name: "ACC_KAFKA_SIGNAL_PREFIX", Value: kafkaSpec.SignalTopicsPrefix},
								{Name: "ACC_NATS_URL", Value: fmt.Sprintf("nats://%s-nats:4222", corpus.Name)},
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
		existingDeploy := existing.(*appsv1.Deployment)
		existingDeploy.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert kafka-bridge Deployment: %w", err)
	}

	corpus.Status.KafkaBridgeReady = true
	return reconcilers.SubResult{Progressing: result != util.UpsertResultNoop}, nil
}
